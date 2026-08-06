"""
Vision context.

The only vision type the rest of Aura ever sees. Everything upstream -
screenshots, window handles, pixel buffers, future OCR or an image model
- collapses into these two strings before it leaves the package.

That collapse is the whole point. brain/ reads `.source` and
`.description` through a structural protocol, so swapping the entire
vision implementation changes nothing outside this package.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class VisionContext:
    """
    One visual observation.

    source:      where it came from  ("screen", "window", "camera")
    description: plain language, written to be dropped into a prompt
                 ("User is editing Python code")
    """

    source: str
    description: str

    def is_empty(self) -> bool:
        return not self.description.strip()

    def render(self) -> str:
        """Single line form, used for prompts and logs."""

        return f"[{self.source}] {self.description}"
