"""
Conversation manager.

Owns one turn of the conversation:

    "hello"  ->  Message  ->  history  ->  PromptBuilder
             ->  LLM.generate()  ->  Response  ->  saved

or, when the caller wants it as it is written:

    chat_stream()  ->  fragments  ->  StreamChunkEvent  ->  UI
    sentences()    ->  sentences  ->  (a voice can speak these)

Both paths share `_prepare`, so they cannot drift apart in what they
announce or what context they assemble.

It receives every dependency through the constructor and knows nothing
about providers, storage engines, UI, TTS or vision.

Optional collaborators (events, vision, knowledge, tools) all default to
None. With none of them supplied this behaves exactly as it did in
Sprint 4.

When a tool runner is supplied, one more thing can happen between the
provider and the reply: the model can ask for a tool, and the turn does
not finish until that tool has actually run and its real result has been
put back in front of the model. Nothing here executes anything - the
request goes to the injected `ToolRunner`, which is the only thing in
Aura permitted to run a tool and the only thing that decides whether a
call is allowed. See `_resolve_tools`.

The events it publishes are facts ("the user said X", "a reply arrived"),
never UI state. Deriving a display state from those facts is the avatar's
job, so there is exactly one owner of that state.
"""

from dataclasses import dataclass, field

from brain.adapters import records_to_messages
from brain.agent_mode import absorb, is_machine_turn
from brain.capabilities import TaskClass, classify_task, generate_for
from brain.message import Message
from brain.ports import (
    ConversationStore,
    EventPublisher,
    KnowledgeProvider,
    LLM,
    ToolResultLike,
    ToolRunner,
    VisionContextLike,
    VisionProvider,
)
from brain.consistency import IdentityAnchor, anchor_of
from brain.persona import PersonaState, persona_of, render_of
from brain.persona_validator import validate
from brain.planner import plan_for
from brain.prompt_builder import PromptBuilder
from brain.recovery import reconcile
from brain.response import Response
from brain.streaming import SentenceAggregator, stream_of
from brain.style import ResponseStyler, hint_of
from brain.task_graph import build, render
from brain.tool_calling import (
    TOOL_CALL_LIMIT,
    Malformed,
    ToolCall,
    call_key,
    read_tool_call,
)

from events.types import (
    ErrorEvent,
    ResponseEvent,
    StreamChunkEvent,
    StreamFinishedEvent,
    StreamStartedEvent,
    TaskFinishedEvent,
    TaskStepChangedEvent,
    TaskStuckEvent,
    ThinkingEvent,
    UserInputEvent,
)

from core.logger import logger


DEFAULT_HISTORY_LIMIT = 20


@dataclass(frozen=True)
class _Turn:
    """
    Everything assembled once, so a turn can be re-rendered cheaply.

    A tool-calling turn builds its prompt more than once - the second time
    with the real result in it. Recomputing the pieces each round would
    take a fresh screenshot and re-query memory between a tool running and
    Aura describing what it did, which is both wasteful and a way for the
    context to change underneath the answer.

    Passed between methods rather than stored on the manager: one engine
    serves every session on the server, and a per-turn field on a shared
    object is a race, not a cache.
    """

    user_msg: Message
    contexts: list[str] = field(default_factory=list)
    context: dict | None = None
    history: list[Message] = field(default_factory=list)
    vision: VisionContextLike | None = None
    knowledge: list[str] = field(default_factory=list)
    temporal: list[str] = field(default_factory=list)
    task: TaskClass = TaskClass.CHAT

    # The register this turn is in, resolved once.
    #
    # Here rather than recomputed because it is now read twice - by the
    # prompt that asks the model for it and by the validator that enforces
    # it - and those two reading different states would mean correcting a
    # reply towards a register nobody asked for. It is also why a machine
    # turn needs no exemption flag: a machine turn builds no `_Turn` at
    # all, so its persona is None and `validate` returns its JSON verbatim.
    persona: PersonaState | None = None

    # Phase 4: the request-scoped evidence ledger. Built when a verifier
    # is wired up, holds every tool outcome and recalled memory line this
    # turn actually gathered, and travels with the turn so the final text
    # can be checked against exactly what happened.
    ledger: "object | None" = None


class ConversationManager:

    def __init__(
        self,
        memory: ConversationStore,
        builder: PromptBuilder,
        llm: LLM,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
        events: EventPublisher | None = None,
        vision: VisionProvider | None = None,
        knowledge: KnowledgeProvider | None = None,
        style: ResponseStyler | None = None,
        identity: IdentityAnchor | None = None,
        persona=None,
        tools: ToolRunner | None = None,
        clock=None,
        pipeline=None,
        cognitive=None,
        verifier=None,
    ):

        self.memory = memory
        self.builder = builder
        self.llm = llm
        self.history_limit = history_limit
        self.events = events
        self.vision = vision
        self.knowledge = knowledge
        self.style = style
        self.identity = identity
        self.persona = persona
        self.tools = tools

        # Both optional, both defaulting to None so that every existing
        # caller builds the prompt it built before Phase 8.
        #
        #   clock     a core.temporal.TemporalClock. Supplies the TIME
        #             section. Injected rather than called directly so a
        #             test can pin "now" and assert on the prompt.
        #   pipeline  a memory.pipeline.MemoryPipeline. Decides what is
        #             worth keeping out of each turn and supplies the
        #             ranked lines that go into MEMORY.
        self.clock = clock
        self.pipeline = pipeline

        # Phase 4: a ResponseVerifier, or None for a caller that does not
        # want the response-grounded layer. Defaults None so every
        # existing caller builds the pipeline it built before Phase 4.
        self.verifier = verifier

        # A core.cognitive.CognitiveStore, or None for a caller that has
        # no use for one. Deliberately a store keyed by session rather
        # than a field holding one state: see the comment above `_Turn`
        # for why one engine serving every session cannot keep per-turn
        # state on itself. A dict behind a lock is the safe form of the
        # same idea, and it is what lets an agent tick ask what the last
        # tick already accomplished.
        self.cognitive = cognitive


    def chat(
        self,
        user_message: str,
        contexts: list[str] | None = None,
        source: str = "text",
        context: dict | None = None,
        session_id: str = "default",
    ) -> Response:
        """
        Process a user message and return the assistant's reply.

        When a tool runner is wired up and has something to offer, the
        first reply may be a request rather than an answer. `_resolve_tools`
        settles that before anything is styled, saved or announced, so what
        the user sees is written with the real outcome already in hand.

        A machine turn - a device agent step, or an intent probe - takes
        the same path as far as the provider and then stops. Its reply is
        returned to the caller verbatim: not styled, not saved, and not
        announced on the bus. See `_machine_turn_notes` for why each of
        those three would be a bug.
        """

        machine = is_machine_turn(context)

        user_msg, prompt, turn, task = self._prepare(
            user_message, contexts, source, context, machine=machine, session_id=session_id
        )

        if not machine:
            self._emit(ThinkingEvent())

        text = self._generate(prompt, task)

        if machine:
            return Response(text=text)

        text = self._resolve_tools(text, turn)

        # Phase 4 runs last, after style and persona, for the reason
        # `_voiced` gives for running after `_styled`: validating last is
        # what makes the thing the user reads the thing that was checked.
        # A verifier that ran before the style filter would be grounding a
        # draft, and the subtractive style pass could delete the very
        # clause that carried a hedge.
        text, verifier_summary = self._verify_final(
            self._voiced(self._styled(text), turn), turn
        )

        response = Response(
            text=text,
            verifier=verifier_summary,
        )

        self._remember(user_msg, response.text, session_id=session_id)

        self._emit(ResponseEvent(text=response.text))

        return response



    def chat_stream(
        self,
        user_message: str,
        contexts: list[str] | None = None,
        source: str = "text",
        context: dict | None = None,
        session_id: str = "default",
    ):
        """
        The same turn, delivered as it is written.

        Yields text fragments and publishes the stream on the bus, so a
        window can print as it goes and a voice can start speaking after
        the first sentence instead of after the last token.

            StreamStartedEvent
            StreamChunkEvent   x N        fragments, for a UI
            SpokenSentence...            (aggregated, see `sentences`)
            StreamFinishedEvent          final text
            ResponseEvent(streamed=True)

        This is a generator: nothing runs, nothing is published and
        nothing is saved until it is iterated. A caller that wants the
        reply without consuming the stream should use `chat`.

        Two honest consequences of streaming, both visible in the events
        rather than hidden:

        The style filter is subtractive over a whole reply, so it cannot
        run on a fragment - deciding whether an opening clause is filler
        needs the clause to have finished. The persona validator is the
        same: whether an address term is a habit or emphasis is a fact
        about the reply, not about a chunk of it. Fragments therefore
        stream raw and `StreamFinishedEvent.text` carries the finished
        reply. A UI that printed chunks should replace its buffer with
        that; the difference is a deleted filler phrase or a corrected
        pronoun, never a changed fact.

        A provider failure part way through raises, exactly as `chat`
        does, and the partial reply is not saved. A half turn in history
        would be indistinguishable from a real one on the next prompt.

        Streaming offers no tools, deliberately, even when a runner is
        wired up. A tool request is a JSON object, and a streamed one is
        already on the user's screen a token at a time before there is
        enough of it to recognise as a request - the reply would have to
        be retracted after being read. A caller that wants Aura to be able
        to do something uses `chat`; this path answers, it does not act.

        A machine turn still yields its fragments, because the caller
        asked for them, but publishes nothing and saves nothing - see
        `chat`.
        """

        machine = is_machine_turn(context)

        user_msg, prompt, turn, _task = self._prepare(
            user_message, contexts, source, context,
            machine=machine, offer_tools=False,
            session_id=session_id,
        )

        if not machine:
            self._emit(ThinkingEvent())
            self._emit(StreamStartedEvent())

        pieces: list[str] = []

        try:
            for index, fragment in enumerate(stream_of(self.llm, prompt)):

                if not fragment:
                    continue

                pieces.append(fragment)

                if not machine:
                    self._emit(StreamChunkEvent(text=fragment, index=index))

                yield fragment

        except Exception as error:
            self._emit(ErrorEvent(message=str(error), source="llm"))
            if not machine:
                self._emit(
                    StreamFinishedEvent(text="".join(pieces), ok=False,
                                        chunks=len(pieces))
                )
            raise

        if machine:
            return

        text = self._voiced(self._styled("".join(pieces)), turn)

        # Phase 4: verify at stream completion. Fragments already went
        # out raw - that is the honest shape of this architecture, and it
        # is documented as such - so the authoritative final text is the
        # repaired one, delivered in the finished event a UI replaces its
        # buffer with.
        verified, verifier_summary = self._verify_final(text, turn)

        self._remember(user_msg, verified, session_id=session_id)

        self._emit(
            StreamFinishedEvent(
                text=verified,
                ok=True,
                chunks=len(pieces),
                verifier=verifier_summary,
                session_id=session_id,
            )
        )

        self._emit(ResponseEvent(text=verified, streamed=True))


    def sentences(
        self,
        user_message: str,
        contexts: list[str] | None = None,
        source: str = "text",
        min_chars: int = 0,
        session_id: str = "default",
    ):
        """
        The stream regrouped into whole sentences.

        For anything that cannot use a partial word - speech, most
        obviously. The events published are identical; only what this
        generator yields differs, so a caller choosing sentences does not
        change what a UI subscribed to the bus sees.
        """

        aggregator = SentenceAggregator(min_chars=min_chars)

        for fragment in self.chat_stream(user_message, contexts, source, session_id=session_id):
            yield from aggregator.feed(fragment)

        tail = aggregator.flush()

        if tail:
            yield tail



    def _prepare(
        self,
        user_message: str,
        contexts: list[str] | None,
        source: str,
        context: dict | None = None,
        machine: bool = False,
        offer_tools: bool = True,
        session_id: str = "default",
    ) -> tuple[Message, str, "_Turn | None", TaskClass]:
        """
        Everything both paths do before the provider is called.

        Shared so that streaming and non-streaming turns cannot drift
        apart in what they announce or what context they assemble.

        Returns the assembled turn alongside the prompt so a tool-calling
        round can re-render without re-gathering anything. None for a
        machine turn, which has nothing to re-render and is never offered
        a tool.

        The task class is returned too. It is decided here, once, from
        the same material the prompt was built from - so a tool round
        re-rendering the turn cannot land in a different lane than the
        first pass did, and the choice of worker cannot drift mid-turn.

        `machine` strips the turn back to the device state and the rules
        for reading it. Nothing conversational is assembled - no history,
        no recalled facts, no vision, no identity anchor, no style hint and
        no memory pipeline - and no UserInputEvent is published, because
        "agent_tick" is not something the user said.

        The clock is the one exception, and the rule the rest of the list
        obeys is what makes it one: everything above is withheld for
        existing to make Aura sound like herself, and the time is not that.
        It is a fact about the present, like the device state the tick
        already carries. The request reaches the model in the owner's own
        words, so "hôm nay" and "tomorrow morning" arrive with it, and a
        model asked to type a date with no date in its prompt invents one
        (section 16). `_temporal_lines` returns nothing when no clock was
        injected, so a manager built without one still produces the tick
        prompt byte-for-byte.
        """

        user_msg = Message(role="user", content=user_message)

        if machine:
            self._absorb(context, session_id)

            return user_msg, self.builder.build(
                history=[],
                user_message=user_msg,
                contexts=[],
                context=context,
                plan=self._plan(context, session_id),
                temporal=self._temporal_lines(),
            ), None, classify_task(user_message, context)

        self._emit(UserInputEvent(text=user_msg.content, source=source))

        history = self.history(session_id=session_id)

        # Phase 4: the request-scoped ledger. Built once per turn, when a
        # verifier is wired up, so the final reply is checked against the
        # evidence this exact turn gathered - never a previous turn's.
        ledger = self._new_ledger(session_id)

        turn = _Turn(
            user_msg=user_msg,
            contexts=list(contexts or []),
            context=context,
            history=history,
            persona=persona_of(self.persona, history, user_msg),
            vision=self._vision_context(),
            knowledge=self._knowledge_for(user_msg.content),
            temporal=self._temporal_lines(),
            task=classify_task(
                user_message,
                context,
                [message.content for message in history],
            ),
            ledger=ledger,
        )

        # Recalled lines become memory evidence in the ledger. The
        # knowledge port returns rendered lines, not rows, so no
        # provenance metadata survives - which means the honest
        # confidence and recency are the *unknown* values, not guesses.
        # A line the pipeline did not describe is treated as old and
        # uncertain, never as recent and verified.
        if ledger is not None:
            for line in turn.knowledge:
                try:
                    ledger.add_memory(
                        line=line,
                        source="recalled",
                    )
                except Exception as error:  # noqa: BLE001
                    logger.debug("Memory evidence skipped: %s", error)

        return (
            user_msg,
            self._compose(turn, offer_tools=offer_tools),
            turn,
            turn.task,
        )



    def _compose(
        self,
        turn: _Turn,
        tool_results: list[str] | None = None,
        offer_tools: bool = True,
    ) -> str:
        """
        Render this turn's prompt, optionally with tool material in it.

        Both tool sections are empty by default, and the builder omits an
        empty section entirely, so a conversation with no runner attached
        produces exactly the prompt it produced before tools existed.
        """

        return self.builder.build(
            history=turn.history,
            user_message=turn.user_msg,
            contexts=turn.contexts,
            vision=turn.vision,
            knowledge=turn.knowledge,
            identity=anchor_of(self.identity, len(turn.history)),
            persona=render_of(self.persona, turn.persona),
            style=hint_of(self.style),
            context=turn.context,
            tools=self._catalogue() if offer_tools else None,
            tool_results=tool_results,
            temporal=turn.temporal,
            capabilities=self._render_capabilities(),
        )


    def _generate(self, prompt: str, task: TaskClass | None = None) -> str:
        """
        Ask the provider, announcing a failure before re-raising it.

        Swallowing it would hide provider outages behind an empty reply.
        Emitted for a machine turn too: a provider that is down is a fact
        about Aura, not about the caller.

        `task` says what kind of work this is, and an LLM that can route
        on it will. `generate_for` falls back to plain `generate` for one
        that cannot, so every provider, mock and test fake in the tree
        keeps working unedited - and a default install, where the owner
        has configured no lanes, behaves exactly as it did before.
        """

        try:
            return generate_for(self.llm, prompt, task)

        except Exception as error:
            self._emit(
                ErrorEvent(message=str(error), source="llm")
            )
            raise


    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def _resolve_tools(self, text: str, turn: _Turn | None) -> str:
        """
        Turn a request to run something into a reply about what happened.

        The rule this exists to enforce: Aura may not tell the user an
        action succeeded unless a tool actually ran and said so. So the
        model's request never reaches the user - it is executed, the real
        result is written back into the prompt, and the model answers
        again with that result in front of it. A failure goes back just as
        plainly as a success, because a model shown nothing about a failed
        call will cheerfully describe the call it meant to make.

        Three separate bounds, because a loop here is a loop in front of a
        paying API:

            - `TOOL_CALL_LIMIT` rounds, counting malformed requests, or a
              model that only ever emits broken JSON would never stop
            - an identical repeat of a call already made this turn is
              refused rather than re-run
            - the round that ends the loop, whichever it is, offers no
              tools at all, so the turn always finishes on a sentence
              rather than on another request

        Nothing is executed here. `self.tools` is the injected runner -
        every permission gate, risk check and timeout lives behind its
        `execute`, and a denial comes back as an ordinary failed result.
        """

        if turn is None or not self._catalogue():
            return text

        results: list[str] = []
        seen: set[str] = set()
        calls = 0

        while calls < TOOL_CALL_LIMIT:

            request = read_tool_call(text)

            if request is None:
                # An ordinary reply. Far and away the common case.
                return text

            calls += 1
            settled = False

            if isinstance(request, Malformed):
                results.append(
                    f"No tool ran. {request.reason}"
                )

            elif call_key(request) in seen:
                results.append(
                    f"{request.name} was not run again: that exact call, "
                    "with those exact arguments, has already run this turn "
                    "and its result is above. Answer the user with it."
                )
                settled = True

            else:
                seen.add(call_key(request))
                results.append(self._run_tool(request, turn))

            offer = not settled and calls < TOOL_CALL_LIMIT

            text = self._generate(
                self._compose(turn, tool_results=results, offer_tools=offer),
                TaskClass.TOOL_PLANNING if offer else turn.task,
            )

            if not offer:
                return text

        return text


    def _run_tool(self, call: ToolCall, turn=None) -> str:
        """
        Hand one request to the runner and describe what came back.

        Never raises and never reports a success it was not given. The
        executor is documented as not raising, but this is the boundary to
        an injected object: an unexpected exception is a failed call, not
        a successful one and not a broken turn.
        """

        try:
            result = self.tools.execute(call.name, dict(call.arguments))

        except Exception as error:
            logger.debug("Tool runner raised for %s: %s", call.name, error)
            return (
                f"{call.name} FAILED: {type(error).__name__}: {error}\n"
                "Nothing happened. Tell the user it did not work."
            )

        self._record_tool_evidence(call, result, turn)

        return self._render_result(call.name, result)

    def _record_tool_evidence(self, call, result, turn) -> None:
        """
        Phase 4: capture the outcome into the turn's evidence ledger.

        Only real `tools.base.ToolResult` objects carry Phases 3's
        status/evidence triplet; anything duck-typed records the bare
        ok/FAILED fact. The ledger is the verifier's only source of
        truth about what actually ran - never the rendered prose.
        """

        ledger = None

        if turn is not None:
            ledger = getattr(turn, "ledger", None)

        if ledger is None:
            return

        try:
            from tools.base import ToolResult

            if isinstance(result, ToolResult):
                ledger.add_tool(
                    tool=call.name,
                    status=str(getattr(result, "status", "") or (
                        "SUCCESS" if result.ok else "FAILED"
                    )),
                    evidence=tuple(result.evidence or ()),
                    outcome=(getattr(result, "output", "") or "")[:240],
                    capability=getattr(result, "capability", "") or "",
                    side_effect=getattr(result, "side_effect", "") or "",
                )
            else:
                ledger.add_tool(
                    tool=call.name,
                    status="SUCCESS" if getattr(result, "ok", False)
                    else "FAILED",
                    evidence=(),
                    outcome=str(getattr(result, "output", "") or "")[:240],
                )
        except Exception as error:
            logger.debug("Tool evidence not recorded: %s", error)

    def _new_ledger(self, request_id: str = "") -> object | None:
        """
        One request-scoped evidence ledger, or None without a verifier.

        Built lazily so `brain/` never hard-imports `tools/` unless the
        verifier is actually wired up - the same seam Phase 3's lazy
        `tools.base` import uses.
        """

        if self.verifier is None:
            return None

        try:
            from brain.verify import EvidenceLedger

            return EvidenceLedger(request_id=request_id or "conversation")

        except Exception as error:
            logger.debug("Evidence ledger unavailable: %s", error)
            return None

    def _verify_final(
        self, text: str, turn,
    ) -> tuple[str, dict | None]:
        """
        Phase 4: the deterministic response-grounding boundary.

        Verification never raises and never blocks: an internal problem
        passes the text through untouched, because truthfulness must
        never cost a conversation. Returns (text, summary) where the
        summary is counts + decision for events, or None when no
        verifier is wired up.
        """

        ledger = None

        if turn is not None:
            ledger = getattr(turn, "ledger", None)

        if self.verifier is None or ledger is None:
            return text, None

        try:
            result = self.verifier.verify(text, ledger)

            summary = {
                "decision": result.decision.value,
                "claims": int(result.counts.get("claims", 0)),
                "contradicted": int(result.counts.get("contradicted", 0)),
                "unsupported": int(result.counts.get("unsupported", 0)),
                "repairs": len(result.repairs),
            }

            if result.changed:
                return result.repaired_text, summary

            return text, summary

        except Exception as error:
            logger.debug("Verifier bypassed for turn: %s", error)
            return text, None


    @staticmethod
    def _render_result(name: str, result: ToolResultLike | None) -> str:
        """
        One outcome, written for the model rather than for a log.

        Phase 3: the rendering is now the deterministic serializer from
        tools/base.py - fixed STATUS/TOOL/EVIDENCE/RETRY lines, then the
        outcome sentence the model is already calibrated to. A structured
        ToolResult gets the full contract; a duck-typed result from an
        injected runner still gets the legacy prose, because the adapter
        must not assume fields a foreign object never promised.

        Success and failure are worded so they cannot be mistaken for each
        other, and the failure line says outright what Aura must do about
        it. A model reading "error: no such file" with no framing has been
        known to summarise it as "done!".
        """

        if result is None:
            return (
                f"{name} FAILED: the tool layer returned nothing.\n"
                "Nothing happened. Tell the user it did not work."
            )

        from tools.base import ToolResult, serialize_for_model

        if isinstance(result, ToolResult):
            return serialize_for_model(result)

        if not getattr(result, "ok", False):

            reason = str(getattr(result, "error", "") or "").strip()

            return (
                f"{name} FAILED: {reason or 'no reason given'}\n"
                "This did not happen. Tell the user it failed, and why."
            )

        output = str(getattr(result, "output", "") or "").strip()

        return f"{name} ran successfully. It returned: {output or '(nothing)'}"


    def _render_capabilities(self) -> str:
        """
        The live, authoritative capability inventory rendered for the prompt.
        """
        try:
            from core.capabilities.introspection import get_introspection_service
            return get_introspection_service().render_summary()
        except Exception as error:
            logger.debug("Could not render live capabilities for prompt: %s", error)
            return ""


    def _catalogue(self) -> str:
        """
        What the runner would currently allow, described, or "".

        Empty covers three cases that must stay indistinguishable from one
        another: no runner injected, tools switched off, and nothing on the
        allow list. In all three the prompt has no TOOLS section and the
        conversation behaves exactly as it did before tools existed.
        """

        if self.tools is None:
            return ""

        try:
            return self.tools.catalogue() or ""
        except Exception as error:
            logger.debug("Tool catalogue unavailable: %s", error)
            return ""


    # ------------------------------------------------------------------
    # _machine_turn_notes
    #
    # Why a machine turn skips styling, memory and the bus, kept here
    # rather than repeated at each of the three call sites:
    #
    #   style   AuraStyle rewrites a reply to sound like Aura. Applied to
    #           `{"action": "click", ...}` it can strip or reword the very
    #           characters the phone's parser needs.
    #
    #   memory  A saved JSON action comes back on the next real turn as
    #           something Aura said out loud, and the transcript fills up
    #           with one entry per agent step - up to ten per request -
    #           crowding out the actual conversation.
    #
    #   bus     ResponseEvent is what a UI prints and what TTS speaks.
    #           Publishing an agent step means the user sees, or hears,
    #           the internal JSON. ErrorEvent is the exception: a provider
    #           outage is worth announcing whoever asked.
    # ------------------------------------------------------------------


    def _remember(self, user_msg: Message, reply: str, session_id: str = "default") -> None:
        """
        Persist a completed turn.

        Only after a successful generation, so a provider failure never
        leaves a user turn stranded without a reply.

        Two destinations with different rules. The transcript takes both
        sides, because a conversation is both sides. Long term memory is
        offered *only* the user's turn: Aura's own replies are not facts
        about the user, and a system that remembers its own output starts
        citing itself as evidence within a few turns.

        This method is not reached at all on a machine turn - `chat`
        returns before it, and `chat_stream` returns before it - which is
        what keeps device agent steps out of both stores.
        """

        try:
            self.memory.save(user_msg.role, user_msg.content, session_id=session_id)
            self.memory.save("assistant", reply, session_id=session_id)
        except TypeError:
            self.memory.save(user_msg.role, user_msg.content)
            self.memory.save("assistant", reply)

        self._observe(user_msg.role, user_msg.content)


    def _observe(self, role: str, content: str) -> None:
        """
        Offer one turn to the memory pipeline.

        The pipeline decides whether it is worth keeping; most turns are
        not. Wrapped like every other optional collaborator: a memory
        subsystem that raises must cost Aura the memory, not the reply
        the user is waiting on.
        """

        if self.pipeline is None:
            return

        try:
            self.pipeline.observe(role, content)
        except Exception as error:
            logger.debug("Memory pipeline failed on a %s turn: %s", role, error)


    def history(self, limit: int | None = None, session_id: str = "default") -> list[Message]:
        """
        Recent conversation as pipeline Messages, oldest first.

        The store returns newest-first; the flip happens here so the
        ordering contract is resolved at the boundary rather than
        being re-derived by every consumer.
        """

        if limit is None:
            limit = self.history_limit

        try:
            records = self.memory.get_recent(limit, session_id=session_id)
        except TypeError:
            records = self.memory.get_recent(limit)

        messages = records_to_messages(records)

        messages.reverse()

        return messages



    # ------------------------------------------------------------------
    # Optional collaborators
    #
    # Each is wrapped so that a broken subsystem degrades the reply
    # instead of breaking the conversation.
    #
    # Degrading is not the same as saying nothing. Every failure below is
    # logged at debug level, because "the reply arrived without memory"
    # and "the reply arrived and there was nothing to remember" are
    # indistinguishable from the outside, and the first one is a bug
    # somebody has to be able to find. Debug rather than warning: these
    # paths are expected to fire on a machine with a subsystem switched
    # off, and a warning per turn would train the user to ignore them.
    # ------------------------------------------------------------------

    def _emit(self, event) -> None:
        if self.events is None:
            return

        try:
            self.events.publish(event)
        except Exception as error:
            logger.debug(
                "Event publish failed (%s): %s",
                type(event).__name__,
                error,
            )

    def _absorb(self, context: dict | None, session_id: str) -> None:
        """
        Record what an agent tick reported, if anyone is keeping track.

        Same shape as `_emit` and `_vision_context`, and for the same
        reason: this is bookkeeping alongside the turn, not part of it. A
        store that raised would take down the action the device is waiting
        for, and an agent that stops mid-task because its notebook tore is
        worse than one working from a stale note.
        """

        if self.cognitive is None:
            return

        try:
            absorb(self.cognitive.for_session(session_id), context)
        except Exception as error:
            logger.debug("Cognitive absorb failed: %s", error)

    def _plan(self, context: dict | None, session_id: str) -> list[str] | None:
        """
        What remains of this request, as prompt lines.

        Called immediately after `_absorb`, and the order matters: the plan
        is marked against what the tick just reported, so absorbing second
        would render a plan one step behind the device.

        Kept separate from `_absorb` rather than folded into it because the
        two answer different questions - what the device said happened,
        versus what we derive from it - and a fault in the derivation
        should not read as a fault in the ingest.

        Nothing is stored except on the state that already owns it. The
        plan's *structure* is recomputed here every tick instead of being
        persisted, which is safe precisely because `plan_for` is pure: the
        request does not change mid-task, so two calls cannot disagree, and
        there is no second copy to drift. What does get written is the
        state's own record of the plan and the current node - `set_plan`
        and `enter_node`, which phase 4 built for this and which nothing
        had called until now.

        Returns None rather than an empty list when there is nothing to
        say, so the prompt omits the section entirely and a request the
        planner cannot parse costs the tick nothing.
        """

        if self.cognitive is None:
            return None

        try:
            state = self.cognitive.for_session(session_id)
            plan = plan_for((context or {}).get("user_request"))
            graph = build(plan, state)

            # Recovery is asked only when the graph says there is nothing
            # left to try, and it is asked before the current node is read
            # because opening or closing recovery changes which node that
            # is. Rebuilt rather than patched when it moves: the graph is a
            # reading of the state, so the honest way to reflect a changed
            # state is to read it again.
            if reconcile(plan, state, graph.is_stuck):
                graph = build(plan, state)

            # Read before writing, because the edge is the whole point.
            # `_plan` runs every tick and rebuilds the same graph from the
            # same pure `plan_for`, so "what step is current" is usually
            # the answer it already was. Publishing that every time would
            # put the repetition section 10 exists to prevent onto the bus
            # as noise; publishing the difference says something moved.
            #
            # Both reads happen before both writes. `had_plan` is needed
            # because "no current node" has two causes that look identical
            # in the state - a task not started and a task finished are
            # both `task_node == ""` - and a device can report a task
            # already complete on its very first tick.
            was = state.task_node
            had_plan = bool(state.plan)

            state.set_plan(step.kind.value for step in plan.steps)

            node = graph.current.step.kind.value if graph.current else ""

            state.enter_node(node)

            self._announce(plan, graph, was, node, had_plan)

            return render(graph) or None
        except Exception as error:
            # The same bargain `_absorb` makes. A device is waiting for an
            # action, and an agent that stops mid-task because it could not
            # describe the task is worse than one working without a plan.
            logger.debug("Planning failed: %s", error)

            return None

    def _announce(
        self, plan, graph, was: str, node: str, had_plan: bool
    ) -> None:
        """
        Tell the bus what moved, if anything did.

        Called from inside `_plan`'s try block on purpose, so a fault here
        is caught by the same handler for the same reason: a device is
        waiting for an action, and announcing a task is worth strictly
        less than performing it.

        Three edges, and nothing on a tick that repeats itself:

            the current step changed      -> TaskStepChangedEvent
            the graph settled every node  -> TaskFinishedEvent
            nothing left to try           -> TaskStuckEvent

        Finished and stuck are both "no current node", which is why
        `graph.current` alone cannot be the signal - `is_finished` and
        `is_stuck` are what separate a completed task from an abandoned
        one, and a subscriber that confused them would congratulate the
        owner on a search that never happened.
        """

        if node:

            if node == was:
                # The ordinary tick: an action is still in flight and the
                # graph reads the same as it did a moment ago.
                return

            self._emit(
                TaskStepChangedEvent(
                    goal=plan.goal,
                    step=node,
                    index=next(
                        (
                            position
                            for position, candidate in enumerate(graph.nodes)
                            if candidate is graph.current
                        ),
                        0,
                    ),
                    total=len(plan.steps),
                )
            )

            return

        # No current node, which is where the two-causes problem lands. It
        # is worth saying once when the agent *arrives* here - either
        # because it just left a step (`was`), or because this is the
        # first tick of a plan and the device reported a task that was
        # already complete (`not had_plan`). On every later tick both are
        # false and nothing is said, which is how a finished task is
        # announced once instead of forever.
        if not (was or not had_plan):
            return

        if graph.is_finished:
            self._emit(
                TaskFinishedEvent(goal=plan.goal, steps=len(plan.steps))
            )

        elif graph.is_stuck:
            # `was` rather than the current node, because there isn't one.
            # The step it gave up on is the useful half.
            self._emit(TaskStuckEvent(goal=plan.goal, step=was))

    def _vision_context(self):
        if self.vision is None:
            return None

        try:
            return self.vision.get_context()
        except Exception as error:
            logger.debug("Vision context unavailable: %s", error)
            return None

    def _knowledge_for(self, query: str) -> list[str]:
        """
        What Aura should recall for this turn.

        Two sources, kept in this order because they answer different
        questions: the knowledge provider holds the durable profile and
        the older keyword recall, and the pipeline holds ranked episodic
        memory, the structured user model and the temporary context.
        Both are bounded by their own limits; neither can return the
        whole database.
        """

        lines: list[str] = []

        if self.knowledge is not None:
            try:
                lines.extend(self.knowledge.get_knowledge(query) or [])
            except Exception as error:
                logger.debug("Knowledge lookup failed: %s", error)

        if self.pipeline is not None:
            try:
                lines.extend(self.pipeline.memory_lines(query) or [])
            except Exception as error:
                logger.debug("Memory pipeline recall failed: %s", error)

        return lines

    def _temporal_lines(self) -> list[str]:
        """
        The TIME section, or nothing at all.

        Nothing when no clock was injected, which is how every prompt
        built before Phase 8 stays byte-identical.
        """

        if self.clock is None:
            return []

        try:
            return self.clock.context().render()
        except Exception as error:
            logger.debug("Temporal context unavailable: %s", error)
            return []

    def _voiced(self, text: str, turn: "_Turn | None") -> str:
        """
        Hold the reply to the register the prompt asked for.

        Sections 13 and 14 both say prompt instructions alone are
        insufficient, and this is where that stops being an observation. The
        prompt already tells the model which pronouns to use and asks it not
        to spam emoji; what was missing was any consequence for ignoring it.

        Runs after the style filter, not before. Style is subtractive over a
        whole reply and can delete a clause, which changes what "the address
        term in every sentence" means - so validating last is what makes the
        thing the user reads the thing that was checked.

        A validator that raises loses its correction, not the answer, for
        the same reason `_styled` is written this way: a bad regex must cost
        a pronoun, never a turn.
        """

        if turn is None or turn.persona is None:
            return text

        try:
            return validate(text, turn.persona)
        except Exception as error:
            logger.debug("Persona validation failed, returning reply: %s", error)
            return text

    def _styled(self, text: str) -> str:
        """
        Apply the style layer, or return the reply untouched.

        A styler that raises loses its polish, not the answer. The reply
        is the thing the user asked for; its wording is not worth failing
        a turn over.
        """

        if self.style is None:
            return text

        try:
            return self.style.style(text)
        except Exception as error:
            logger.debug("Style pass failed, returning raw reply: %s", error)
            return text
