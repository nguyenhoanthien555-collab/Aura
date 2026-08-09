"""
Chat request/response models.

Every inbound model carries explicit size limits. The API is exposed to a
phone over a network, so "how big can this be" is a server-side decision,
not something the client gets to choose.
"""
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from uuid import uuid4

from server.config import settings


class ChatRequest(BaseModel):
    """Chat request."""
    session_id: Optional[str] = Field(
        default_factory=lambda: str(uuid4()),
        max_length=128,
    )
    message: str = Field(
        min_length=1,
        max_length=settings.max_message_length,
    )
    context: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    """Chat response."""
    session_id: str
    reply: str
    message_id: str = Field(default_factory=lambda: str(uuid4()))
    metadata: Dict[str, Any] = Field(default_factory=dict)


class StreamChunk(BaseModel):
    """Single chunk of a streaming response."""
    session_id: str
    chunk: str
    index: int
    is_final: bool = False
    message_id: Optional[str] = None
