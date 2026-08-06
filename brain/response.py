"""
LLM response abstraction.

Represents AI output for the whole pipeline.

This is intentionally a dataclass with a single field. Future systems
(voice, avatar, tools) will attach to it by adding optional fields with
defaults - e.g. emotion, audio, tool_calls, metadata - so that existing
callers constructing Response(text=...) keep working unchanged.
"""

from dataclasses import dataclass


@dataclass
class Response:

    text: str