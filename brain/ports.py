"""
Brain ports (interfaces).

These are the ONLY things the brain is allowed to know about the
outside world. Concrete implementations live in other packages
(memory/, brain/providers/, ...) and are injected in.

Rules:
- The brain never imports a concrete implementation.
- Any subsystem that satisfies one of these shapes can be plugged in.
"""

from typing import Iterator, Protocol, Sequence, runtime_checkable

# The streaming capability, defined next to the helpers that consume it.
# Imported rather than restated: this file used to carry a second
# definition of StreamingLLM whose docstring claimed to be a re-export
# while actually requiring `generate` as well as `stream`, so the two
# protocols of the same name disagreed about what satisfied them.
# `brain.streaming` imports nothing from `brain`, so there is no cycle.
from brain.streaming import StreamingLLM


@runtime_checkable
class LLM(Protocol):
    """
    Anything that can turn a rendered prompt into text.

    Satisfied by BrainRouter and by every brain.providers.* provider.
    """

    def generate(self, prompt: str) -> str:
        ...


class MessageRecord(Protocol):
    """
    A stored message, as returned by a ConversationStore.

    Deliberately minimal: the brain only ever reads role/content.
    A database row satisfies this without the brain importing the ORM.

    Not runtime_checkable: protocols with non-method members cannot be
    used with isinstance(). This is a static typing aid only.
    """

    role: str
    content: str


@runtime_checkable
class ConversationStore(Protocol):
    """
    Persistence for conversation turns.

    Ordering contract:
        get_recent() returns the most recent messages NEWEST FIRST.
        Converting to pipeline order is the caller's job.
    """

    def save(self, role: str, content: str, session_id: str = "default") -> None:
        ...

    def get_recent(self, limit: int = 10, session_id: str = "default") -> Sequence[MessageRecord]:
        ...



@runtime_checkable
class EventPublisher(Protocol):
    """
    Somewhere to announce what the brain is doing.

    The brain never subscribes and never learns who is listening. This is
    how the avatar, TTS and logs observe a conversation without the brain
    importing a GUI or an audio library.
    """

    def publish(self, event: object) -> None:
        ...


class VisionContextLike(Protocol):
    """
    A visual observation, as far as the brain is concerned.

    vision.context.VisionContext satisfies this structurally, so the
    prompt can include what Aura sees without brain/ importing vision/.

    Not runtime_checkable: protocols with non-method members cannot be
    used with isinstance().
    """

    source: str
    description: str


@runtime_checkable
class VisionProvider(Protocol):
    """
    Supplies the current visual context, or None when vision is off.

    Implemented by vision.manager.VisionManager.
    """

    def get_context(self) -> VisionContextLike | None:
        ...


@runtime_checkable
class KnowledgeProvider(Protocol):
    """
    Supplies remembered facts relevant to the current turn.

    Implemented by memory.knowledge.MemoryKnowledgeProvider. Returns
    rendered lines, not rows, so the brain never sees storage shapes.
    """

    def get_knowledge(self, query: str) -> list[str]:
        ...


class ToolResultLike(Protocol):
    """
    What running a tool produced, as far as the brain is concerned.

    tools.base.ToolResult satisfies this structurally, so a conversation
    can be grounded in a real outcome without brain/ importing tools/.

    Not runtime_checkable: protocols with non-method members cannot be
    used with isinstance().
    """

    ok: bool
    output: str
    error: str
    tool: str


@runtime_checkable
class ToolRunner(Protocol):
    """
    Somewhere a requested tool can actually be run.

    Implemented by tools.executor.ToolExecutor, which is the only thing in
    Aura permitted to execute anything. The brain reads this port to learn
    what exists and to hand over a request; it never runs a tool, never
    sees the registry, and never decides whether a call is permitted -
    every gate stays behind `execute`.

    `catalogue` returns the tools policy would currently allow, already
    described. Empty means the conversation offers no tools at all, which
    is the default and must stay indistinguishable from tools never having
    been wired up.
    """

    def available(self) -> list[str]:
        ...

    def catalogue(self) -> str:
        ...

    def execute(self, name: str, arguments: dict | None = None) -> ToolResultLike:
        ...
