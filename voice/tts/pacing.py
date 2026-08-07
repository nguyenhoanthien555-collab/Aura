"""
Speech pacing.

A mood should be audible. A sleepy Aura talking at the same rate and
pitch as an excited one is a character with a face that moves and a voice
that does not.

This is the voice layer's own reading of Mood, and it is the only place
that reading exists. brain/mood.py turns a mood into writing direction;
avatar/expression.py turns it into a face; this turns it into two
numbers. None of the three imports the others.

    Mood.SLEEPY  ->  brain    "shorter sentences, softer"
                     avatar   Expression.SLEEPY
                     voice    -12% rate, -8Hz pitch

Offsets, not absolutes. The user's configured rate is the voice they
chose; a mood nudges it. Reading `voice.tts.rate` and replacing it would
mean a mood silently overrode a setting, and the setting would appear
broken rather than adjusted.

The numbers are small on purpose. Speech that changes character between
replies reads as a bug; speech that shifts a few percent reads as a
person.

Importing this pulls in one enum and nothing else - no audio library, no
provider, no network. Everything here is arithmetic on strings.
"""

from dataclasses import dataclass

from events.types import Mood


@dataclass(frozen=True)
class Pacing:
    """
    A mood's effect on a voice, as offsets to apply to the configured
    settings.

    `rate` and `pitch` are percent and hertz respectively, matching what
    Edge accepts, because that is the provider that supports them. A
    provider that cannot vary pitch reads `rate` and ignores the rest;
    one that can vary neither ignores both. Nothing here requires a
    provider to implement anything.
    """

    rate: int = 0
    pitch: int = 0

    @property
    def neutral(self) -> bool:
        return self.rate == 0 and self.pitch == 0


# How each mood sounds.
#
# TEASING is faster and higher because teasing lands on timing; SLEEPY is
# slower and lower for the same reason in reverse. FOCUSED is slightly
# slower and flat - concentration sounds level, not fast.
#
# NEUTRAL is present rather than omitted so `MOOD_PACING[mood]` is total
# over the enum and a caller does not need a fallback.
MOOD_PACING: dict[Mood, Pacing] = {
    Mood.NEUTRAL: Pacing(),
    Mood.HAPPY: Pacing(rate=6, pitch=4),
    Mood.CURIOUS: Pacing(rate=2, pitch=3),
    Mood.FOCUSED: Pacing(rate=-3, pitch=0),
    Mood.TEASING: Pacing(rate=8, pitch=6),
    Mood.SLEEPY: Pacing(rate=-12, pitch=-8),
}


def pacing_for(mood) -> Pacing:
    """
    The pacing a mood implies. Neutral for anything unrecognised.

    Tolerant rather than strict: a mood arriving from a plugin or a
    future rule should cost a nudge in pacing, not an exception in the
    middle of speaking.
    """

    if not isinstance(mood, Mood):
        return Pacing()

    return MOOD_PACING.get(mood, Pacing())


__all__ = ["Pacing", "MOOD_PACING", "pacing_for"]
