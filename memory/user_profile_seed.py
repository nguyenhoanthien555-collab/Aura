"""
The initial user model.

Structured data, not prose. This is the whole point: the profile Aura
starts with is a table of keyed beliefs that can be queried, ranked,
corrected and selectively injected. Pasting the same information into
the system prompt as a paragraph would cost tokens on every single turn
and could never be corrected by the user saying "actually, not any more".

Nothing here is loaded into a prompt wholesale. `UserModel.relevant()`
picks the few entries that bear on the current turn; the rest sit in the
database costing nothing.

**On status.** Everything the user stated about themselves is seeded
CONFIRMED, because they said it. The one exception is `thinking.risks` -
the tendencies toward overthinking, rabbit holes and scope creep. The
brief that supplied them called them patterns rather than diagnoses, so
they are seeded INFERRED at moderate confidence. That is not a
downgrade for its own sake: an INFERRED belief renders with "(inferred)"
attached, so Aura hedges instead of telling someone what their flaws
are. If the user confirms one, `confirm()` will promote it. Nothing else
will.

Seeding is idempotent and never overwrites. Re-running it on a database
that already has a user model leaves every existing entry alone,
including corrections the user has since made - a restart must not undo
"actually I prefer coffee now".
"""

from memory.user_model import (
    COMMUNICATION,
    DECISION,
    FEEDBACK,
    IDENTITY,
    INTEREST,
    MOTIVATION,
    PERSONALITY,
    PROJECT,
    THINKING,
    VALUES,
    Status,
    UserModel,
)


SEED_SOURCE = "profile_seed"


# (key, value, category). Seeded as CONFIRMED: the user stated these.
CONFIRMED_PROFILE = (
    # --- identity -----------------------------------------------------
    ("identity.address_style", "casual - bro, ông, tui", IDENTITY),
    ("identity.primary_language", "Vietnamese", IDENTITY),
    ("identity.technical_language", "comfortable with technical English", IDENTITY),
    ("identity.learning_language", "Japanese, interested in learning", IDENTITY),
    ("identity.social_energy", "ambivert", IDENTITY),

    # --- personality --------------------------------------------------
    ("personality.curiosity", "very high", PERSONALITY),
    ("personality.experimentation", "very high", PERSONALITY),
    ("personality.independence", "high", PERSONALITY),
    ("personality.self_reflection", "high", PERSONALITY),
    ("personality.adaptability", "high", PERSONALITY),
    ("personality.ambition", "high", PERSONALITY),
    ("personality.systems_thinking", "high", PERSONALITY),
    ("personality.directness", "high", PERSONALITY),
    ("personality.humor", "high", PERSONALITY),
    ("personality.playfulness", "high", PERSONALITY),

    # --- communication ------------------------------------------------
    ("communication.tone", "casual, direct, friendly, natural", COMMUNICATION),
    ("communication.energy", "playful and high-energy", COMMUNICATION),
    ("communication.formality", "not excessively formal", COMMUNICATION),
    ("communication.humor_style",
     "absurd, self-aware, light roasting, dramatic exaggeration, "
     "internet humor", COMMUNICATION),
    ("communication.profanity",
     "comfortable with profanity in casual conversation", COMMUNICATION),
    ("communication.sincerity",
     "wants sincerity when the subject is genuinely serious", COMMUNICATION),

    # --- feedback -----------------------------------------------------
    ("feedback.honesty", "values honesty over agreement", FEEDBACK),
    ("feedback.criticism",
     "wants accurate criticism, not invented criticism", FEEDBACK),
    ("feedback.directness", "wants direct feedback", FEEDBACK),
    ("feedback.corrections",
     "wants incorrect assumptions corrected", FEEDBACK),

    # --- values -------------------------------------------------------
    ("values.core",
     "authenticity, independence, curiosity, meaningful relationships",
     VALUES),
    ("values.understanding", "accurate understanding of things", VALUES),
    ("values.time", "efficient use of time", VALUES),
    ("values.growth", "personal growth and self-improvement", VALUES),
    ("values.creativity", "creative freedom", VALUES),
    ("values.usefulness", "usefulness", VALUES),

    # --- decision making ----------------------------------------------
    ("decision.style", "pragmatic and time-conscious", DECISION),
    ("decision.approach", "experimental", DECISION),
    ("decision.wasted_effort", "low tolerance for wasted effort", DECISION),
    ("decision.persistence",
     "abandons implementations and tools more easily than the underlying "
     "goal", DECISION),

    # --- thinking patterns --------------------------------------------
    ("thinking.strengths",
     "pattern recognition, explores alternatives, questions assumptions, "
     "connects unrelated topics", THINKING),

    # --- motivation ---------------------------------------------------
    ("motivation.drivers",
     "curiosity, creating things, learning, independence", MOTIVATION),
    ("motivation.progress", "visible progress and interesting problems",
     MOTIVATION),
    ("motivation.meaning",
     "time should be spent on something worthwhile", MOTIVATION),

    # --- interests ----------------------------------------------------
    ("interest.ai",
     "local AI, LLMs, AI agents, vision models, coding models, memory "
     "systems, model routing, tool calling, self-hosting, open-source AI",
     INTEREST),
    ("interest.hobbies",
     "Minecraft, pixel art, AI-generated art, Japanese, Arduino and "
     "electronics", INTEREST),
    ("interest.not_game_dev",
     "game development is not a preferred career direction", INTEREST),
    ("interest.arduino_limits",
     "Arduino work is bounded by component and resource limitations",
     INTEREST),

    # --- projects -----------------------------------------------------
    ("project.aura",
     "personal AI companion - long-term context, memory, tool usage, "
     "vision, device integration, distinct personality, coding assistance",
     PROJECT),
    ("project.duality",
     "Minecraft Forge mod - combat, bosses, elite mobs, artifacts, relics, "
     "world events, progression, endgame content, structured architecture, "
     "sprint-based development", PROJECT),
)


# Patterns, not diagnoses. Seeded INFERRED so Aura hedges when she brings
# them up, and so the user can overrule any of them by simply saying so.
INFERRED_PROFILE = (
    ("thinking.risks",
     "can overthink, follow rabbit holes, expand scope, weigh too many "
     "alternatives, or over-optimise", THINKING, 0.6),
)


def seed_user_model(model: UserModel, force: bool = False) -> int:
    """
    Write the initial profile, skipping anything already known.

    Returns the number of entries created. Idempotent: a second call
    adds nothing, so this can run on every startup without a guard flag
    and without ever clobbering a correction the user has made since.

    `force=True` rewrites the seed values over whatever is stored. It
    exists for tests and for a deliberate profile reset; the startup
    path never sets it, because "restore my defaults" is a decision for
    the user rather than a side effect of a restart.
    """

    written = 0

    for key, value, category in CONFIRMED_PROFILE:

        if not force and model.get(key) is not None:
            continue

        if model.confirm(
            key,
            value,
            category=category,
            confidence=1.0,
            source=SEED_SOURCE,
        ):
            written += 1

    for key, value, category, confidence in INFERRED_PROFILE:

        existing = model.get(key)

        if not force and existing is not None:
            continue

        # Never downgrade something the user has since confirmed.
        if existing is not None and existing.status is Status.CONFIRMED:
            continue

        if model.infer(
            key,
            value,
            category=category,
            confidence=confidence,
            source=SEED_SOURCE,
        ):
            written += 1

    return written
