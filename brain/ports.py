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


@runtime_checkable
class LLM(Protocol):
    """
    Anything that can turn a rendered prompt into text.

    Satisfied by BrainRouter and by every brain.providers.* provider.
    """

    def generate(self, prompt: str) -> str:
        ...


@runtime_checkable
class StreamingLLM(Protocol):
    """
    An LLM that can also yield its reply in pieces.

    A separate protocol rather than a second method on LLM, and that is
    load bearing: LLM is runtime_checkable, so adding `stream` to it
    would make isinstance fail for every provider that does not
    implement one - including a user's own. Streaming is a capability
    some providers have, not part of the contract all of them meet.

    `brain.streaming.stream_of` is the usual way to consume either kind
    without branching. The canonical definition lives there, next to the
    helpers; it is re-exported here so the full set of brain contracts is
    readable in one file.
    """

    def generate(self, prompt: str) -> str:
        ...

    def stream(self, prompt: str) -> Iterator[str]:
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

    def save(self, role: str, content: str) -> None:
        ...

    def get_recent(self, limit: int = 10) -> Sequence[MessageRecord]:
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
