"""
The agent runtime.

One authoritative multi-round loop. Every caller - the server API, the
CLI harness, benchmarks, tests - drives tasks through this module, which
is what retires the family of bugs the old design bred by having three
half-loops (device tick state, chat tool path, ad-hoc intents) that each
kept state in different places.

Shape of one run:

    goal -> [model round] -> tool calls -> policy gates -> execution
         -> structured envelopes -> fresh observations -> model round
         -> ... -> verified completion | explicit failure

Termination is enumerated, never emergent. The step ceiling exists as a
safety net only - a run that ends on it is recorded as STEP_CEILING,
which is a diagnosis, not an outcome. Convergence happens because the
goal was achieved AND its postconditions verified; anything else stops
the loop under a named reason (FAILED, CANCELLED, RETRY_EXHAUSTED,
MODEL_ERROR).

Two execution sites, one protocol:

    inline      the runtime executes through the ToolExecutor here
                (loopback bridges, adb harnesses, desktop/file tools)
    deferred    the runtime hands tool-call directives to a device that
                polls (the Android step endpoint), and folds the device's
                structured reports back as tool messages next round

Both produce identical transcripts, envelopes and observations, so a
task debugged over the CLI replays byte-for-byte against the live path.
"""

import json
import threading
import time
from dataclasses import dataclass, field
from enum import Enum

from brain.native_fc import ModelTurn, ToolCallRequest
from core.ids import new_run_id, new_task_id, new_tool_call_id
from core.logger import logger
from core.observations import ObservationKind, ObservationStore
from tools.schema import openai_tools_payload


class RunStatus(str, Enum):

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StopReason(str, Enum):
    """
    Why a run stopped. Every value is a statement someone can act on;
    there is deliberately no generic "steps ran out" success-shaped
    answer, because that ambiguity was the old loop's signature failure.
    """

    GOAL_VERIFIED = "goal_verified"
    COMPLETED_UNVERIFIED = "completed_unverified"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRY_EXHAUSTED = "retry_exhausted"
    MODEL_ERROR = "model_error"
    STEP_CEILING = "step_ceiling"


# Risk names whose results must carry verification evidence before a run
# may claim completion. Mirrors the ToolRisk ladder rather than inventing
# a second classification.
MUTATING_RISKS = frozenset({"dangerous", "sensitive"})

# How many corrective rounds a completion claim may be sent back for
# verification before it is accepted as unverified. Bounded: verification
# nudging must be able to lose gracefully.
MAX_VERIFY_ROUNDS = 2

# Consecutive failed tool calls tolerated before the run fails outright.
MAX_CONSECUTIVE_FAILURES = 4

# Transcript budget before compaction. Rounds are pairs (assistant +
# tool results); keeping the last few verbatim preserves exactly the
# facts the current decision depends on.
COMPACTION_THRESHOLD_ROUNDS = 12

KEEP_VERBATIM_ROUNDS = 6

# Anything longer than this in an old message is a payload (a tree, a
# frame reference block), and payloads are what compaction exists for.
COMPACTION_MIN_LENGTH = 600


@dataclass
class AgentRun:
    """
    One execution of the loop, with everything it owns.

    All mutable state lives here - never on the runtime, which serves
    every session concurrently, and never as loose strings on a caller.
    """

    run_id: str
    task_id: str
    session_id: str
    goal: str

    status: RunStatus = RunStatus.RUNNING
    stop_reason: StopReason | None = None
    stop_detail: str = ""

    # Wire-form transcript: roles user / assistant / tool. Compacted in
    # place by `_compact`.
    messages: list[dict] = field(default_factory=list)

    rounds: int = 0
    tool_call_count: int = 0
    consecutive_failures: int = 0
    verify_rounds: int = 0

    # (tool, tool_call_id, reason) for mutating calls lacking verified
    # postconditions - the queue a completion claim must empty.
    unverified: list[tuple] = field(default_factory=list)

    created_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict:
        """Status for APIs and logs, without the transcript bulk."""

        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "goal": self.goal,
            "status": self.status.value,
            "stop_reason": (
                self.stop_reason.value if self.stop_reason else None
            ),
            "rounds": self.rounds,
            "tool_call_count": self.tool_call_count,
            "unverified_count": len(self.unverified),
        }


@dataclass(frozen=True)
class Directive:
    """
    What one round decided.

    Either `tool_calls` carries calls to perform (with their runtime ids
    pre-assigned), or `text` is the final answer. `envelopes` carries the
    structured results when the calls were executed inline.
    """

    kind: str                      # "tool_calls" | "final"
    text: str = ""
    tool_calls: tuple = ()
    envelopes: tuple = ()

    def to_dict(self) -> dict:

        base = {"type": self.kind}

        if self.kind == "final":
            base["text"] = self.text
        else:
            base["tool_calls"] = [
                {
                    "tool_call_id": call_id,
                    "tool": request.name,
                    "arguments": request.arguments,
                }
                for call_id, request in self.tool_calls
            ]
            base["results"] = list(self.envelopes)

        return base


def _result_payload(result) -> dict:
    """Structured result values, decoded from the tool's output line."""

    if not getattr(result, "ok", False):
        return {}

    import json as _json

    try:
        decoded = _json.loads(result.output)
        return decoded if isinstance(decoded, dict) else {"value": decoded}
    except (ValueError, TypeError):
        return {"output": result.output}


def _check_target(envelope: dict) -> str:
    """
    The state a verify/wait_for checked, extracted from its arguments:
    'package_is=com.y' -> 'com.y', 'foreground=com.y' -> 'com.y'.
    Empty when the check names no comparable target.
    """

    arguments = envelope.get("arguments") or {}

    raw = (
        arguments.get("check")
        or arguments.get("condition")
        or ""
    )

    _, _, value = str(raw).partition("=")

    return value.strip()


class AgentRuntime:
    """
    Owns runs, tools, observations and the loop itself.

    Constructed once per process with its dependencies injected; all
    per-task state lives on AgentRun objects, so one instance serves
    every session without sharing a mutable field between them.
    """

    def __init__(
        self,
        llm,
        executor=None,
        registry=None,
        observations: ObservationStore | None = None,
        system_prompt: str = "",
        max_steps: int = 25,
        deferred: bool = False,
        clock=time.time,
    ):
        """
        `llm` is anything with generate_with_tools(system, messages,
        tools) -> ModelTurn. `executor` is a ToolExecutor (policy gates
        included); when omitted, only deferred mode works.

        `deferred=True` selects the device-polling execution site: tool
        calls are returned as directives instead of being executed here,
        and the reports arrive through `fold_tool_reports`.
        """

        self.llm = llm
        self.executor = executor
        self.registry = registry
        self.observations = observations or ObservationStore(clock=clock)
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.deferred = deferred
        self._clock = clock

        self._runs: dict[str, AgentRun] = {}
        self._lock = threading.Lock()

        if not deferred and executor is None:
            raise ValueError(
                "AgentRuntime needs an executor for inline mode, or "
                "deferred=True for device-driven execution"
            )

        self._tools_payload = (
            openai_tools_payload(registry.all()) if registry is not None else []
        )

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start_run(
        self,
        goal: str,
        session_id: str,
        task_id: str | None = None,
    ) -> AgentRun:
        """
        A new run for one goal in one session.

        A run never reuses another's transcript: the goal becomes this
        run's first and only user message, which is the structural reason
        a previous task's response cannot satisfy a new request.
        """

        run = AgentRun(
            run_id=new_run_id(),
            task_id=task_id or new_task_id(),
            session_id=session_id,
            goal=goal,
        )

        run.messages.append({"role": "user", "content": f"Goal: {goal}"})

        with self._lock:
            self._runs[run.run_id] = run

        return run

    def get_run(self, run_id: str) -> AgentRun | None:

        with self._lock:
            return self._runs.get(run_id)

    def cancel(self, run_id: str) -> bool:
        """User-initiated stop. Takes effect at the next round boundary."""

        run = self.get_run(run_id)

        if run is None or run.status is not RunStatus.RUNNING:
            return False

        self._stop(run, StopReason.CANCELLED, "cancelled by owner")
        return True

    # ------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------

    def advance(self, run: AgentRun) -> Directive:
        """
        One round: ask the model, then either execute what it asked for
        (inline) or hand the calls back (deferred).
        """

        if run.status is not RunStatus.RUNNING:
            raise RuntimeError(f"run {run.run_id} is {run.status.value}")

        try:
            turn = self._model_round(run)
        except Exception as error:
            logger.warning("Model round failed for %s: %s", run.run_id, error)
            self._stop(run, StopReason.MODEL_ERROR, str(error))
            return Directive(kind="final", text=f"model error: {error}")

        if turn.is_empty:
            # Silence is not completion. One retry with an explicit
            # correction, then fail under a name.
            run.messages.append({
                "role": "user",
                "content": "Your last reply was empty. Either call a tool "
                           "or state the final answer.",
            })
            run.consecutive_failures += 1

            if run.consecutive_failures >= 2:
                self._stop(run, StopReason.FAILED, "model produced empty rounds")
                return Directive(kind="final", text="failed: empty model reply")

            return self.advance(run)

        if turn.wants_tools:
            return self._take_tool_calls(run, turn)

        return self._consider_completion(run, turn)

    def run_to_completion(self, run: AgentRun) -> AgentRun:
        """
        Drive rounds until the run stops, inline.

        The step ceiling bounds runaway runs but no path to success runs
        through it: every other exit is a named verdict first.
        """

        while run.status is RunStatus.RUNNING:

            if run.rounds >= self.max_steps:
                self._stop(
                    run,
                    StopReason.STEP_CEILING,
                    f"reached the {self.max_steps}-round safety ceiling",
                )
                break

            self.advance(run)

        return run

    # ------------------------------------------------------------------
    # Deferred (device-driven) mode
    # ------------------------------------------------------------------

    def fold_tool_reports(self, run: AgentRun, envelopes: list[dict]) -> None:
        """
        Turn device-reported results into transcript messages.

        The deferred twin of inline execution: the phone performed the
        calls this runtime handed out and sends back structured reports
        shaped exactly like inline envelopes. Folding is bookkeeping
        only - verification verdicts are read here just as they are from
        an inline run.
        """

        assistant_calls = [
            {
                "id": f"{envelope['tool_call_id']}|"
                      f"{envelope.get('call_id', '')}",
                "type": "function",
                "function": {
                    "name": envelope["tool"],
                    "arguments": json.dumps(
                        envelope.get("arguments", {}), ensure_ascii=False
                    ),
                },
            }
            for envelope in envelopes
        ]

        run.messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": assistant_calls,
        })
        run.messages.append({
            "role": "tool",
            "content": json.dumps(envelopes, ensure_ascii=False, default=str),
        })

        self._absorb_envelopes(run, envelopes)

    # ------------------------------------------------------------------
    # Rounds
    # ------------------------------------------------------------------

    def _model_round(self, run: AgentRun) -> ModelTurn:

        self._compact(run)

        turn = self.llm.generate_with_tools(
            self.system_prompt,
            list(run.messages),
            self._tools_payload,
        )

        run.rounds += 1

        if not turn.wants_tools:
            run.consecutive_failures = 0

        return turn

    def _take_tool_calls(self, run: AgentRun, turn) -> Directive:

        assigned = []
        raw_calls = []

        for request in turn.tool_calls:
            call_id = new_tool_call_id()
            assigned.append((call_id, request))
            raw_calls.append({
                "id": f"{call_id}|{request.call_id}",
                "type": "function",
                "function": {
                    "name": request.name,
                    "arguments": json.dumps(
                        request.arguments, ensure_ascii=False
                    ),
                },
            })

        run.tool_call_count += len(assigned)
        # Deliberately NO reset of consecutive_failures here: it is what
        # lets failures accumulate across rounds. Only a successful
        # envelope (see _absorb_envelopes) proves progress worth
        # resetting on.

        if self.deferred:
            run.messages.append({
                "role": "assistant",
                "content": turn.text or "",
                "tool_calls": raw_calls,
            })
            return Directive(kind="tool_calls", tool_calls=tuple(assigned))

        envelopes = [
            self._execute_inline(call_id, request, run)
            for call_id, request in assigned
        ]

        run.messages.append({
            "role": "assistant",
            "content": turn.text or "",
            "tool_calls": raw_calls,
        })
        run.messages.append({
            "role": "tool",
            "content": json.dumps(envelopes, ensure_ascii=False, default=str),
        })

        self._absorb_envelopes(run, envelopes)

        return Directive(kind="tool_calls", tool_calls=tuple(assigned),
                         envelopes=tuple(envelopes))

    def _execute_inline(
        self,
        call_id: str,
        request: ToolCallRequest,
        run: AgentRun | None = None,
    ) -> dict:

        result = self.executor.execute(request.name, request.arguments)

        report = dict(getattr(result, "data", {}) or {})
        report.setdefault("ok", bool(result.ok))
        report.setdefault("tool", request.name)
        report.setdefault("result", _result_payload(result))

        if not result.ok:
            report.setdefault(
                "error", {"code": "TOOL_ERROR", "message": result.error}
            )

        return self._build_envelope(call_id, request, report, run=run)

    # ------------------------------------------------------------------
    # Envelopes, verification, termination
    # ------------------------------------------------------------------

    def _build_envelope(
        self,
        call_id: str,
        request: ToolCallRequest,
        report: dict,
        run: AgentRun | None,
    ) -> dict:
        """
        The PART 8 shape every tool produces, whoever executed it.

        `observation_id` links the post-state evidence; `postcondition`
        carries the tool's own verification claim when it made one.
        """

        envelope = {
            "tool_call_id": call_id,
            "call_id": request.call_id,
            "tool": request.name,
            "arguments": request.arguments,
            "ok": bool(report.get("ok")),
        }

        if envelope["ok"]:
            envelope["result"] = report.get("result") or {}
        else:
            error = report.get("error") or {}
            envelope["error"] = {
                "code": str(error.get("code", "TOOL_ERROR")),
                "message": str(error.get("message", "")),
            }

        postcondition = report.get("postcondition")

        if postcondition is not None:
            envelope["postcondition"] = postcondition

        observation = report.get("observation")

        if isinstance(observation, dict):
            stored = self.observations.create(
                kind=str(observation.get("kind")
                         or ObservationKind.DEVICE_STATE),
                source=f"tool:{request.name}",
                data=observation.get("data") or {},
            )

            if run is not None:
                stored = stored.with_scope(
                    task_id=run.task_id,
                    run_id=run.run_id,
                    session_id=run.session_id,
                )
                self.observations.record(stored)

            envelope["observation_id"] = stored.observation_id

        return envelope

    def _absorb_envelopes(self, run: AgentRun, envelopes: list[dict]) -> None:

        for envelope in envelopes:

            if not envelope.get("ok"):
                run.consecutive_failures += 1
                continue

            run.consecutive_failures = 0

            risk = self._risk_of(envelope["tool"])
            postcondition = envelope.get("postcondition")

            # A verified wait_for / verify is positive evidence: it can
            # retire an earlier mutation's pending verification (the
            # canonical case - launch_app reports unmet while settling,
            # then wait_for('foreground=x') proves the settle landed).
            if (
                envelope["tool"] in ("android.verify", "android.wait_for")
                and isinstance(postcondition, dict)
                and postcondition.get("verified")
            ):
                target = _check_target(envelope)

                if target:
                    run.unverified = [
                        entry for entry in run.unverified
                        if entry[3] != target
                    ]
                continue

            if risk not in MUTATING_RISKS:
                continue

            if not isinstance(postcondition, dict):
                run.unverified.append((
                    envelope["tool"],
                    envelope["tool_call_id"],
                    "no postcondition reported",
                    "",
                ))
            elif not postcondition.get("verified", True):
                run.unverified.append((
                    envelope["tool"],
                    envelope["tool_call_id"],
                    "postcondition reported unmet",
                    str(postcondition.get("expected_package")
                        or postcondition.get("expected_text") or ""),
                ))

        if run.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self._stop(
                run,
                StopReason.RETRY_EXHAUSTED,
                f"{run.consecutive_failures} consecutive tool failures",
            )

    def _risk_of(self, tool_name: str) -> str:
        """The declared risk of a tool, as a lowercase string."""

        if self.registry is None:
            return ""

        tool = self.registry.get(tool_name)

        if tool is None:
            return ""

        return getattr(getattr(tool, "risk", None), "value", "")

    def _consider_completion(self, run: AgentRun, turn) -> Directive:

        text = turn.text.strip()

        if run.unverified and run.verify_rounds < MAX_VERIFY_ROUNDS:
            # The model does not get to declare success over an
            # unverified mutation. One bounded corrective round asks for
            # evidence; if it never arrives, completion is accepted but
            # labelled unverified - honest rather than blocked forever.
            run.verify_rounds += 1

            pending = ", ".join(
                f"{tool} ({reason})"
                for tool, _, reason, _ in run.unverified
            )
            run.messages.append({"role": "assistant", "content": text})
            run.messages.append({
                "role": "user",
                "content": (
                    f"These actions were not verified: {pending}. Use "
                    "android.verify or android.wait_for to confirm them, "
                    "then finish."
                ),
            })
            return Directive(kind="final", text="verification required")

        reason = (
            StopReason.GOAL_VERIFIED
            if not run.unverified
            else StopReason.COMPLETED_UNVERIFIED
        )
        self._stop(run, reason, "")
        run.messages.append({"role": "assistant", "content": text})

        return Directive(kind="final", text=text)

    def _stop(
        self,
        run: AgentRun,
        reason: StopReason,
        detail: str,
    ) -> None:

        if reason is StopReason.CANCELLED:
            run.status = RunStatus.CANCELLED
        elif reason in (StopReason.GOAL_VERIFIED,
                        StopReason.COMPLETED_UNVERIFIED):
            run.status = RunStatus.COMPLETED
        else:
            run.status = RunStatus.FAILED

        run.stop_reason = reason
        run.stop_detail = detail

        logger.info(
            "Agent run %s stopped: %s%s",
            run.run_id,
            reason.value,
            f" ({detail})" if detail else "",
        )

    # ------------------------------------------------------------------
    # Context compaction
    # ------------------------------------------------------------------

    def _compact(self, run: AgentRun) -> None:
        """
        Shrink old payload-heavy messages without losing facts.

        Rounds are (assistant + tool results) pairs. Beyond the verbatim
        window, any message whose content is a large payload becomes a
        one-line summary that keeps the outcome and drops the bulk. The
        goal (first user message), recent rounds and small messages are
        never touched - compaction must never delete what finishing the
        task depends on.
        """

        total_pairs = sum(
            1 for message in run.messages if message["role"] == "assistant"
        )

        if total_pairs <= COMPACTION_THRESHOLD_ROUNDS:
            return

        cutoff_pair = total_pairs - KEEP_VERBATIM_ROUNDS
        seen_assistants = 0

        for index, message in enumerate(run.messages):

            if message["role"] == "assistant":
                seen_assistants += 1

            if seen_assistants > cutoff_pair:
                break

            content = message.get("content")

            if (
                not isinstance(content, str)
                or len(content) < COMPACTION_MIN_LENGTH
            ):
                continue

            summary = self._summarize(content)

            if summary is not None:
                run.messages[index] = {**message, "content": summary}

    @staticmethod
    def _summarize(content: str) -> str | None:

        try:
            envelopes = json.loads(content)
        except (ValueError, TypeError):
            return None

        if not isinstance(envelopes, list):
            return None

        lines = []

        for envelope in envelopes:
            if isinstance(envelope, dict):
                verdict = "ok" if envelope.get("ok") else "failed"
                lines.append(f"{envelope.get('tool')}: {verdict}")

        return "[compacted] " + "; ".join(lines)