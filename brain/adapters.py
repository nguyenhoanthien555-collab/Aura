"""
Conversion between storage records and pipeline messages.

This is the ONE place where the database representation of a message
is turned into the AI pipeline representation.

    memory.models.Message  (ORM row)
                |
                v
    brain.message.Message  (pipeline dataclass)

Why this exists:

- memory/ is free to change its schema (timestamps, embeddings,
  summaries, conversation ids) without touching the brain.
- brain/ stays a pure dataclass pipeline with no ORM session attached.
- Detached ORM rows can't blow up mid-prompt with a lazy-load error.

The conversion is one-directional on purpose. The brain never
constructs storage rows; it calls store.save(role, content) and lets
memory/ decide how to persist.
"""

from typing import Iterable

from brain.message import Message
from brain.ports import MessageRecord


def record_to_message(record: MessageRecord) -> Message:
    """
    Convert a single stored record into a pipeline Message.
    """

    return Message(
        role=record.role,
        content=record.content,
    )


def records_to_messages(
    records: Iterable[MessageRecord],
) -> list[Message]:
    """
    Convert stored records into pipeline Messages, preserving order.
    """

    return [
        record_to_message(record)
        for record in records
    ]
