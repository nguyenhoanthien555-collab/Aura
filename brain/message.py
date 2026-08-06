"""
Message abstraction.

Represents a single conversation message.
"""

from dataclasses import dataclass


@dataclass
class Message:

    role: str
    content: str