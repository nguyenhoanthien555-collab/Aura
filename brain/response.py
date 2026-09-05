"""
LLM response abstraction.

Represents AI output for the whole pipeline.

This is intentionally a dataclass with a single field. Future systems
(voice, avatar, tools) will attach to it by adding optional fields with
defaults - e.g. emotion, audio, tool_calls, metadata - so that existing
callers constructing Response(text=...) keep working unchanged.
"""

from dataclasses import dataclass, field


@dataclass
class Response:

    text: str
    # Phase 4: the verifier's summary (decision + counts) when one ran,
    # or None when no verifier is wired up. Never the claim text.
    verifier: dict | None = None