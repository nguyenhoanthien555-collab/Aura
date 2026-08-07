"""
Voice setting values.

One `voice.tts` section is read by three providers that disagree about
units. Edge wants "+5%" and "+10Hz"; SAPI wants -10..10; pyttsx3 wants
words per minute. Rather than make the user rewrite `rate` every time
they switch provider, each provider coerces the shared value into its own
form, and the coercion lives here so the three cannot drift apart.

Every function is forgiving on purpose. A typo in a voice setting should
cost that setting, not the reply - so anything unreadable falls back
rather than raising.

Pure text and arithmetic. Nothing here imports a provider, which is what
lets both `voice/factory.py` and `voice/tts/providers/edge.py` use it
without either importing the other.
"""

import re

from core.logger import logger

PERCENT = re.compile(r"^[+-]\d+%$")
HERTZ = re.compile(r"^[+-]\d+Hz$", re.IGNORECASE)


def normalise_percent(value, fallback: str) -> str:
    """
    Coerce a rate or volume into edge-tts's "+N%" form.

    Accepts what a person would plausibly write in YAML: "+5%", "5",
    5, -10, "5%". Anything unrecognisable falls back rather than raising,
    because a typo in a voice setting should not cost the reply.
    """

    if isinstance(value, bool):
        return fallback

    if isinstance(value, (int, float)):
        return f"{int(value):+d}%"

    text = str(value or "").strip()

    if not text:
        return fallback

    if PERCENT.match(text):
        return text

    try:
        return f"{int(float(text.rstrip('%').strip())):+d}%"
    except ValueError:
        logger.warning("Unusable TTS rate/volume %r, using %s", value, fallback)
        return fallback


def normalise_hertz(value, fallback: str) -> str:
    """Coerce a pitch into edge-tts's "+NHz" form."""

    if isinstance(value, bool):
        return fallback

    if isinstance(value, (int, float)):
        return f"{int(value):+d}Hz"

    text = str(value or "").strip()

    if not text:
        return fallback

    if HERTZ.match(text):
        return text[:-2] + "Hz"

    try:
        return f"{int(float(text.lower().rstrip('hz').strip())):+d}Hz"
    except ValueError:
        logger.warning("Unusable TTS pitch %r, using %s", value, fallback)
        return fallback


def number_in(value, fallback: int = 0) -> int:
    """
    The signed number out of "+5%", "-8Hz", 5 or "5".

    Used to shift an already normalised setting and to read the shared
    rate setting from providers that do not speak in percentages, so it
    is forgiving in the same way the normalisers are: anything unreadable
    is the fallback, which leaves the setting where it was.
    """

    if isinstance(value, bool):
        return fallback

    if isinstance(value, (int, float)):
        return int(value)

    text = str(value or "").strip().lower().rstrip("%")

    if text.endswith("hz"):
        text = text[:-2]

    try:
        return int(float(text))
    except ValueError:
        return fallback


def shift_percent(base: str, delta) -> str:
    """`base` moved by `delta` percentage points."""

    return f"{number_in(base) + number_in(delta):+d}%"


def shift_hertz(base: str, delta) -> str:
    """`base` moved by `delta` hertz."""

    return f"{number_in(base) + number_in(delta):+d}Hz"


__all__ = [
    "PERCENT",
    "HERTZ",
    "normalise_percent",
    "normalise_hertz",
    "number_in",
    "shift_percent",
    "shift_hertz",
]
