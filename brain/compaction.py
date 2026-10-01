"""
Conversational context compaction.

Condenses older messages in a long conversation transcript into a structured
synopsis ([COMPACTED CONTEXT]) while keeping the latest turns verbatim.
Prevents context window bloat, reduces token costs and API latency on cloud
models (e.g. Gemini), and preserves conversational intent.
"""

from typing import Any, Optional
from brain.message import Message
from core.logger import logger


DEFAULT_COMPACTION_THRESHOLD = 20
DEFAULT_KEEP_RECENT = 6


def _extract_text(msg: Any) -> str:
    if hasattr(msg, "content"):
        return str(msg.content or "")
    if isinstance(msg, dict):
        return str(msg.get("content") or "")
    return str(msg or "")


def _extract_role(msg: Any) -> str:
    if hasattr(msg, "role"):
        return str(msg.role or "user")
    if isinstance(msg, dict):
        return str(msg.get("role") or "user")
    return "user"


class ConversationCompactor:
    """
    Manages conversational transcript compaction.

    Splits long histories into older messages to condense and recent turns
    to preserve verbatim.
    """

    def __init__(
        self,
        threshold: int = DEFAULT_COMPACTION_THRESHOLD,
        keep_recent: int = DEFAULT_KEEP_RECENT,
        llm: Any = None,
    ):
        self.threshold = max(4, int(threshold))
        self.keep_recent = max(2, int(keep_recent))
        self.llm = llm

    def should_compact(self, history: list[Any]) -> bool:
        """True if history message count strictly exceeds threshold."""
        return len(history) > self.threshold

    def compact(
        self,
        history: list[Any],
        force: bool = False,
    ) -> tuple[list[Message], bool]:
        """
        Compact history if needed or if forced.

        Returns (compacted_history, was_compacted).
        """
        if not self.should_compact(history) and not force:
            return [self._to_message(m) for m in history], False

        if len(history) <= self.keep_recent:
            return [self._to_message(m) for m in history], False

        to_condense = history[:-self.keep_recent]
        to_keep = history[-self.keep_recent:]

        summary = self._summarize(to_condense)

        compacted_banner = (
            f"[COMPACTED CONTEXT]\n"
            f"Earlier conversation synopsis ({len(to_condense)} messages condensed):\n"
            f"{summary}"
        )

        compacted_message = Message(role="system", content=compacted_banner)
        result = [compacted_message] + [self._to_message(m) for m in to_keep]

        logger.info(
            "Context compacted: %d messages -> %d messages (1 synopsis + %d verbatim)",
            len(history),
            len(result),
            len(to_keep),
        )

        return result, True

    def _summarize(self, messages: list[Any]) -> str:
        """Generate a concise synopsis of messages to condense."""
        # 1. Attempt LLM-driven synthesis if provider is present
        if self.llm is not None:
            prompt = self._build_summary_prompt(messages)
            try:
                if hasattr(self.llm, "generate"):
                    reply = self.llm.generate(prompt)
                    if reply and str(reply).strip():
                        return str(reply).strip()
            except Exception as exc:
                logger.debug("LLM summary generation failed, falling back to extractive summary: %s", exc)

        # 2. Rule-based / extractive fallback summary
        points: list[str] = []
        user_queries: list[str] = []
        assistant_points: list[str] = []

        for msg in messages:
            role = _extract_role(msg)
            text = _extract_text(msg).strip()
            if not text:
                continue

            # Skip existing compacted banner if re-compacting
            if text.startswith("[COMPACTED CONTEXT]"):
                clean = text.replace("[COMPACTED CONTEXT]", "").strip()
                points.append(f"Prior context: {clean[:200]}...")
                continue

            if role == "user":
                user_queries.append(text[:120])
            elif role == "assistant":
                first_line = text.split("\n")[0][:120]
                assistant_points.append(first_line)

        if user_queries:
            topics_sample = "; ".join(user_queries[:4])
            points.append(f"- User topics & requests: {topics_sample}")
        if assistant_points:
            replies_sample = "; ".join(assistant_points[:3])
            points.append(f"- Concluded actions & replies: {replies_sample}")

        return "\n".join(points) if points else "- Previous conversational turns discussed and acknowledged."

    @staticmethod
    def _build_summary_prompt(messages: list[Any]) -> str:
        formatted = []
        for m in messages:
            role = _extract_role(m)
            text = _extract_text(m)
            formatted.append(f"{role.upper()}: {text[:250]}")
        dialogue = "\n".join(formatted)
        return (
            "Summarize the following prior conversation into 2-3 concise bullet points "
            "highlighting user intents, key decisions, and main outcomes. "
            "Keep it factual and brief:\n\n"
            f"{dialogue}\n\nSummary:"
        )

    @staticmethod
    def _to_message(item: Any) -> Message:
        if isinstance(item, Message):
            return item
        return Message(role=_extract_role(item), content=_extract_text(item))
