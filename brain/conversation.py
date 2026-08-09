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

Optional collaborators (events, vision, knowledge) all default to None.
With none of them supplied this behaves exactly as it did in Sprint 4.

The events it publishes are facts ("the user said X", "a reply arrived"),
never UI state. Deriving a display state from those facts is the avatar's
job, so there is exactly one owner of that state.
"""

from brain.adapters import records_to_messages
from brain.message import Message
from brain.ports import (
    ConversationStore,
    EventPublisher,
    KnowledgeProvider,
    LLM,
    VisionProvider,
)
from brain.consistency import IdentityAnchor, anchor_of
from brain.prompt_builder import PromptBuilder
from brain.response import Response
from brain.streaming import SentenceAggregator, stream_of
from brain.style import ResponseStyler, hint_of

from events.types import (
    ErrorEvent,
    ResponseEvent,
    StreamChunkEvent,
    StreamFinishedEvent,
    StreamStartedEvent,
    ThinkingEvent,
    UserInputEvent,
)


DEFAULT_HISTORY_LIMIT = 20


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


    def chat(
        self,
        user_message: str,
        contexts: list[str] | None = None,
        source: str = "text",
        context: dict | None = None,
    ) -> Response:
        """
        Process a user message and return the assistant's reply.
        """

        user_msg, prompt = self._prepare(user_message, contexts, source, context)

        self._emit(ThinkingEvent())

        try:
            response = Response(
                text=self._styled(self.llm.generate(prompt))
            )

        except Exception as error:
            # Announce the failure and let the caller decide what to do.
            # Swallowing it here would hide provider outages behind an
            # empty reply.
            self._emit(
                ErrorEvent(message=str(error), source="llm")
            )
            raise

        self._remember(user_msg, response.text)

        self._emit(ResponseEvent(text=response.text))

        return response


    def chat_stream(
        self,
        user_message: str,
        contexts: list[str] | None = None,
        source: str = "text",
        context: dict | None = None,
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
        needs the clause to have finished. Fragments therefore stream
        unstyled and `StreamFinishedEvent.text` carries the styled reply.
        A UI that printed chunks should replace its buffer with that;
        the difference is a deleted filler phrase, never a changed fact.

        A provider failure part way through raises, exactly as `chat`
        does, and the partial reply is not saved. A half turn in history
        would be indistinguishable from a real one on the next prompt.
        """

        user_msg, prompt = self._prepare(user_message, contexts, source, context)

        self._emit(ThinkingEvent())
        self._emit(StreamStartedEvent())

        pieces: list[str] = []

        try:
            for index, fragment in enumerate(stream_of(self.llm, prompt)):

                if not fragment:
                    continue

                pieces.append(fragment)

                self._emit(StreamChunkEvent(text=fragment, index=index))

                yield fragment

        except Exception as error:
            self._emit(ErrorEvent(message=str(error), source="llm"))
            self._emit(
                StreamFinishedEvent(text="".join(pieces), ok=False,
                                    chunks=len(pieces))
            )
            raise

        text = self._styled("".join(pieces))

        self._remember(user_msg, text)

        self._emit(
            StreamFinishedEvent(text=text, ok=True, chunks=len(pieces))
        )

        self._emit(ResponseEvent(text=text, streamed=True))


    def sentences(
        self,
        user_message: str,
        contexts: list[str] | None = None,
        source: str = "text",
        min_chars: int = 0,
    ):
        """
        The stream regrouped into whole sentences.

        For anything that cannot use a partial word - speech, most
        obviously. The events published are identical; only what this
        generator yields differs, so a caller choosing sentences does not
        change what a UI subscribed to the bus sees.
        """

        aggregator = SentenceAggregator(min_chars=min_chars)

        for fragment in self.chat_stream(user_message, contexts, source):
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
    ) -> tuple[Message, str]:
        """
        Everything both paths do before the provider is called.

        Shared so that streaming and non-streaming turns cannot drift
        apart in what they announce or what context they assemble.
        """

        user_msg = Message(role="user", content=user_message)

        self._emit(UserInputEvent(text=user_msg.content, source=source))

        history = self.history()

        prompt = self.builder.build(
            history=history,
            user_message=user_msg,
            contexts=contexts or [],
            vision=self._vision_context(),
            knowledge=self._knowledge_for(user_msg.content),
            identity=anchor_of(self.identity, len(history)),
            style=hint_of(self.style),
            context=context,
        )

        return user_msg, prompt


    def _remember(self, user_msg: Message, reply: str) -> None:
        """
        Persist a completed turn.

        Only after a successful generation, so a provider failure never
        leaves a user turn stranded without a reply.
        """

        self.memory.save(user_msg.role, user_msg.content)
        self.memory.save("assistant", reply)


    def history(self, limit: int | None = None) -> list[Message]:
        """
        Recent conversation as pipeline Messages, oldest first.

        The store returns newest-first; the flip happens here so the
        ordering contract is resolved at the boundary rather than
        being re-derived by every consumer.
        """

        if limit is None:
            limit = self.history_limit

        records = self.memory.get_recent(limit)

        messages = records_to_messages(records)

        messages.reverse()

        return messages


    # ------------------------------------------------------------------
    # Optional collaborators
    #
    # Each is wrapped so that a broken subsystem degrades the reply
    # instead of breaking the conversation.
    # ------------------------------------------------------------------

    def _emit(self, event) -> None:
        if self.events is None:
            return

        try:
            self.events.publish(event)
        except Exception:
            pass

    def _vision_context(self):
        if self.vision is None:
            return None

        try:
            return self.vision.get_context()
        except Exception:
            return None

    def _knowledge_for(self, query: str) -> list[str]:
        if self.knowledge is None:
            return []

        try:
            return self.knowledge.get_knowledge(query) or []
        except Exception:
            return []

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
        except Exception:
            return text
