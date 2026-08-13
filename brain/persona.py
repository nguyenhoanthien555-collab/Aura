"""
Aura's persona, as a contract rather than a paragraph.

`prompts/personality.md` says who she is. This module says who she is
*being on this turn*: which pronoun pair the conversation has settled on,
which context mode the message calls for, and how far the humour, energy
and brainrot dials are turned up. One artifact, consumed identically by
every provider.

There is deliberately no branch on the model anywhere in this file, and
there must never be one. A per-provider persona is the defect this exists
to prevent: the same person cannot be two people depending on whose API
key happened to work. What a provider is allowed to change is the wording
of a reply. What it is not allowed to change is who wrote it.

Nothing here is stateful, and that is the whole design.

The register is *derived* from the conversation - the transcript that is
already in the prompt - rather than stored in a mutable tracker. Two
consequences, both of which the brief asks for and neither of which needs
a persistence layer:

    provider fallback keeps the persona   the next model is handed the
                                          same history, so it resolves to
                                          the same pronoun style and the
                                          same mode. Nothing to copy over
                                          and nothing to forget to copy.

    one instance serves every session     the server shares one
                                          ConversationManager, so a
                                          per-turn field on it would be a
                                          race. A pure function of the
                                          turn cannot be.

Mood is deliberately absent. It already reaches the model through
`brain/mood.py` -> the RESPONSE STYLE section, and a second copy here
would be one more place for the two to disagree.
"""

import re
from dataclasses import dataclass, replace
from enum import Enum


# Fixed. These three are the parts of the persona no message, model,
# mode or setting is allowed to move.
IDENTITY = "aura"

GENDER_EXPRESSION = "female"

DEFAULT_REGISTER = "casual_gen_z"


# ----------------------------------------------------------------------
# Pronouns
#
# Vietnamese makes the pronoun pair a running choice rather than a
# grammatical detail, and getting it wrong is the single most obvious way
# a reply stops sounding like the same person. The failure the brief names
# is not "the wrong pair" - it is *changing pair mid-conversation*, which
# reads as somebody else typing.
#
# So a style is chosen once per turn, from evidence, and stated in the
# prompt as one line. The model is not asked to pick.
# ----------------------------------------------------------------------

class PronounStyle(str, Enum):
    """
    A coherent self/address pair, or the option of using neither.

    `str` valued so a config file, a log line and a test can all name one
    without a conversion table.
    """

    TUI_BRO = "tui_bro"
    CAU_TO = "cau_to"
    MINH_BAN = "minh_ban"
    SPARSE = "sparse"


# What Aura calls herself, and what she calls him, in each style.
#
# SPARSE is not a missing entry: it is the style with no pronouns in it,
# which is often the best Vietnamese available and is what she falls back
# to rather than guessing a pair the conversation has not established.
SELF_WORD = {
    PronounStyle.TUI_BRO: "tui",
    PronounStyle.CAU_TO: "tớ",
    PronounStyle.MINH_BAN: "mình",
    PronounStyle.SPARSE: "",
}

ADDRESS_WORD = {
    PronounStyle.TUI_BRO: "bro",
    PronounStyle.CAU_TO: "cậu",
    PronounStyle.MINH_BAN: "bạn",
    PronounStyle.SPARSE: "",
}


# How a *user* message reveals which register it is in. Read from what he
# calls himself and what he calls her - two independent signals, because
# plenty of messages carry only one.
#
# "tao"/"mày" resolve to the casual style rather than to a style of their
# own: they are the same register, one notch coarser, and Aura answers a
# "tao/mày" message in "tui/bro". Mirroring them back is the one bit of
# register matching the brief explicitly rules out.
USER_SELF_WORDS = {
    "tui": PronounStyle.TUI_BRO,
    "tao": PronounStyle.TUI_BRO,
    "tớ": PronounStyle.CAU_TO,
    "mình": PronounStyle.MINH_BAN,
    "tôi": PronounStyle.MINH_BAN,
}

USER_ADDRESS_WORDS = {
    "bro": PronounStyle.TUI_BRO,
    "ông": PronounStyle.TUI_BRO,
    "mày": PronounStyle.TUI_BRO,
    "cậu": PronounStyle.CAU_TO,
    "bạn": PronounStyle.MINH_BAN,
}

# Never used back at him, whichever style is active.
COARSE_WORDS = ("tao", "mày")

# What Aura's own previous replies reveal, so a settled style stays
# settled. Only her self-reference is read: the address term drifts with
# the sentence ("ê", "ủa", a bare name) while the self-reference does not.
AURA_SELF_WORDS = {
    "tui": PronounStyle.TUI_BRO,
    "tớ": PronounStyle.CAU_TO,
    "mình": PronounStyle.MINH_BAN,
}


# ----------------------------------------------------------------------
# Context modes
#
# The same person at six different volumes. A mode moves the dials and
# nothing else - it never changes what Aura is willing to say, how
# accurate she is, or whether she pushes back. That restriction is what
# makes a cheap lexical guess safe to make: guessing "casual" when the
# message was technical costs one notch of humour, not a wrong answer.
#
# Ambiguity resolves to CASUAL, which is the mode her default dials
# describe, so a message with no signal in it gets her ordinary voice.
# ----------------------------------------------------------------------

class ContextMode(str, Enum):

    CASUAL = "casual"
    TECHNICAL = "technical"
    DEBUGGING = "debugging"
    SERIOUS = "serious"
    SUPPORTIVE = "supportive"
    EXCITED = "excited"


@dataclass(frozen=True)
class PersonaDials:
    """
    Style intensities, 0.0 to 1.0.

    A signal, not a template. Nothing renders these into a reply and no
    check counts jokes per paragraph - they exist so "mildly brainrot" and
    "noticeably chaotic" are one number apart in the prompt instead of two
    adjectives the model has to reconcile.
    """

    energy: float = 0.72
    humor: float = 0.68
    brainrot: float = 0.40
    technicality: float = 0.55
    warmth: float = 0.85


# The default brainrot level, on the brief's 0-5 scale, is 2: subtle
# internet humour that lands when it lands. 0.40 is that 2, and it is the
# CASUAL default above rather than a constant here so there is one number
# to change.
BRAINROT_SCALE = 5

MODE_DIALS = {
    ContextMode.CASUAL: PersonaDials(),
    ContextMode.TECHNICAL: PersonaDials(
        energy=0.60, humor=0.40, brainrot=0.20, technicality=0.95, warmth=0.75,
    ),
    ContextMode.DEBUGGING: PersonaDials(
        energy=0.62, humor=0.30, brainrot=0.15, technicality=1.0, warmth=0.72,
    ),
    ContextMode.SERIOUS: PersonaDials(
        energy=0.45, humor=0.20, brainrot=0.05, technicality=0.85, warmth=0.88,
    ),
    ContextMode.SUPPORTIVE: PersonaDials(
        energy=0.45, humor=0.30, brainrot=0.10, technicality=0.55, warmth=0.95,
    ),
    ContextMode.EXCITED: PersonaDials(
        energy=0.95, humor=0.80, brainrot=0.55, technicality=0.60, warmth=0.90,
    ),
}

# One line of writing direction per mode. Written as what to do, not as a
# label, because "technical mode" tells a model nothing it can act on.
MODE_NOTES = {
    ContextMode.CASUAL: (
        "Casual. Warm and quick, humour welcome, a reaction before the "
        "answer is fine. A few sentences is usually the whole reply."
    ),
    ContextMode.TECHNICAL: (
        "Technical. Precision first and still conversational: name the "
        "actual thing, keep English technical terms in English, and drop "
        "the slang density rather than the warmth."
    ),
    ContextMode.DEBUGGING: (
        "Debugging. Focused and analytical - find the cause, say what it "
        "is, say what to do about it, and do not send him editing code "
        "that is not the problem. Still recognisably you, just fewer jokes."
    ),
    ContextMode.SERIOUS: (
        "Serious. Calm, warm, respectful, no brainrot and no jokes at the "
        "expense of what he is actually saying. Do not become formal - "
        "serious is quieter, not stiffer."
    ),
    ContextMode.SUPPORTIVE: (
        "Supportive. Warm and human, not therapeutic and not a checklist. "
        "Do not try to solve everything at once, and do not perform "
        "sympathy - say the true, kind, short thing."
    ),
    ContextMode.EXCITED: (
        "Excited. High energy, more slang, real reactions - it is allowed "
        "to be loud when something actually works. Still one person "
        "talking, not a wall of emoji."
    ),
}


# Lexical cues. Small on purpose: each list holds words whose presence is
# a strong signal, and nothing is inferred from their absence.
FAILURE_CUES = (
    "lỗi", "bug", "error", "exception", "traceback", "stack trace",
    "crash", "fail", "failed", "broken", "không chạy", "ko chạy",
    "treo", "đứng im", "sai rồi", "vỡ",
)

TECHNICAL_CUES = (
    "code", "function", "class", "api", "build", "gradle", "compile",
    "deploy", "log", "config", "database", "provider", "prompt", "test",
    "commit", "refactor", "kotlin", "python", "json", "server", "import",
    "endpoint", "query", "thread", "async", "regex", "schema",
)

WIN_CUES = (
    "chạy rồi", "chạy được", "xong rồi", "pass rồi", "nó chạy", "it works",
    "worked", "thành công", "done rồi", "fix được", "green",
)

LOW_CUES = (
    "mệt", "stress", "buồn", "chán", "kiệt sức", "tuyệt vọng", "bỏ cuộc",
    "tired", "exhausted", "overwhelmed", "burn out", "burnout", "hopeless",
    "lo lắng", "sợ",
)

SERIOUS_CUES = (
    "nghiêm túc", "serious", "không đùa", "ko đùa", "thật lòng",
    "quan trọng thật", "nói thật",
)


# ----------------------------------------------------------------------
# Reading a message
# ----------------------------------------------------------------------

WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def words_of(text: str) -> list[str]:
    """
    Lowercased word tokens. Diacritics are letters, so `tớ` is one word.

    Used instead of substring matching for single words, because "tôi" is
    inside "tôi" and also inside nothing else, while "to" would be inside
    half the language.
    """

    return [match.group(0).lower() for match in WORD.finditer(text or "")]


def read_style(text: str) -> PronounStyle | None:
    """
    The register a message is written in, or None if it carries no signal.

    Address terms are weighed before self-terms: "cậu xem giúp tớ" and
    "cậu xem giúp mình" are both the cậu/tớ register, and the second one
    would resolve to neutral if the self-word won.
    """

    tokens = set(words_of(text))

    for table in (USER_ADDRESS_WORDS, USER_SELF_WORDS):

        hits = [style for word, style in table.items() if word in tokens]

        if not hits:
            continue

        # One register per message. A message carrying two - "bro cậu ơi" -
        # takes the first in the table's order, which is the casual one,
        # because that is the register a mixed message is actually in.
        return hits[0]

    return None


def read_aura_style(text: str) -> PronounStyle | None:
    """Which style one of Aura's own replies was written in."""

    tokens = set(words_of(text))

    hits = [style for word, style in AURA_SELF_WORDS.items() if word in tokens]

    return hits[0] if hits else None


def read_mode(text: str) -> ContextMode:
    """
    Which mode this message calls for.

    Order is precedence, and it is deliberate: how he is doing outranks
    what he is doing. A message that says he is exhausted *and* pastes a
    traceback is answered warmly first, because the traceback will still
    be there in the next sentence.
    """

    lowered = (text or "").lower()
    tokens = set(words_of(lowered))

    def has(cues) -> bool:
        return any(
            cue in lowered if " " in cue else cue in tokens
            for cue in cues
        )

    if has(LOW_CUES):
        return ContextMode.SUPPORTIVE

    if has(SERIOUS_CUES):
        return ContextMode.SERIOUS

    if has(FAILURE_CUES):
        return ContextMode.DEBUGGING

    if has(WIN_CUES) or _is_shouted(text):
        return ContextMode.EXCITED

    if has(TECHNICAL_CUES) or "```" in (text or ""):
        return ContextMode.TECHNICAL

    return ContextMode.CASUAL


def _is_shouted(text: str) -> bool:
    """
    Written in caps, the way "BROOO NÓ CHẠY RỒI" is.

    Needs at least three letters so "OK" and "TTS" are not excitement, and
    ignores anything with no letters at all.
    """

    letters = [char for char in (text or "") if char.isalpha()]

    if len(letters) < 3:
        return False

    upper = sum(1 for char in letters if char.isupper())

    return upper / len(letters) > 0.7


# ----------------------------------------------------------------------
# Explicit addressing preferences
#
# Rule 11 of the brief: what he says about how he wants to be addressed
# wins over every other signal here, including Aura's own habit. Read
# narrowly - two sentence shapes, both of which mean it unambiguously -
# because a loose match would silently ban a word he only mentioned.
# ----------------------------------------------------------------------

NEGATION = ("đừng", "dừng", "không", "ko", "đừng có", "stop", "don", "dont")

CALL_ME = re.compile(
    r"(?:gọi|kêu)\s+(?:tôi|tớ|tui|mình|anh|em|t)?\s*là\s+([^\s,.!?\n]+)",
    re.IGNORECASE,
)

CALL_ME_BARE = re.compile(
    r"(?:gọi|kêu)\s+([^\s,.!?\n]+)",
    re.IGNORECASE,
)

CALL_ME_EN = re.compile(r"call\s+me\s+([^\s,.!?\n]+)", re.IGNORECASE)

# Clauses rather than sentences: "đừng gọi tớ là bro, gọi tớ là cậu" is
# one sentence carrying two instructions, and reading it as one would
# apply whichever pattern matched first and drop the other.
SENTENCE = re.compile(r"[^.!?,\n]+")


@dataclass(frozen=True)
class AddressPreference:
    """What he asked to be called, and what he asked not to be."""

    preferred: str = ""
    forbidden: tuple[str, ...] = ()

    def merged_with(self, other: "AddressPreference") -> "AddressPreference":
        """
        Later statements win, and a ban survives being restated.

        `forbidden` accumulates because "don't call me bro" stays true
        after he later says "call me cậu"; `preferred` is replaced, because
        two names cannot both be the one he wants.
        """

        return AddressPreference(
            preferred=other.preferred or self.preferred,
            forbidden=tuple(dict.fromkeys(self.forbidden + other.forbidden)),
        )


def read_preference(text: str) -> AddressPreference:
    """
    An addressing instruction, or an empty preference.

    Sentence by sentence, so "đừng gọi tớ là bro, gọi tớ là cậu" is read
    as both halves rather than as whichever pattern matched first.
    """

    preferred = ""
    forbidden: list[str] = []

    for sentence in SENTENCE.findall(text or ""):

        lowered = sentence.lower()

        if "gọi" not in lowered and "kêu" not in lowered and "call me" not in lowered:
            continue

        match = CALL_ME.search(sentence) or CALL_ME_EN.search(sentence)

        negated = any(word in words_of(lowered) for word in NEGATION)

        if match is None and negated:
            match = CALL_ME_BARE.search(sentence)

        if match is None:
            continue

        word = match.group(1).strip().strip("\"'“”").lower()

        if not word or word in ("là", "me"):
            continue

        if negated:
            forbidden.append(word)
        else:
            preferred = word

    return AddressPreference(
        preferred=preferred,
        forbidden=tuple(dict.fromkeys(forbidden)),
    )


# ----------------------------------------------------------------------
# The state
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class PersonaState:
    """
    Who Aura is being on this turn.

    Frozen, because it is derived: two turns of the same conversation
    resolve independently, and a mutable copy shared between them is the
    race the module docstring rules out.

    `source` records *why* this pronoun style was chosen. Not sent to the
    model - it exists so a test can assert that continuity beat mirroring
    rather than merely that both produced the same answer.
    """

    identity: str = IDENTITY
    gender_expression: str = GENDER_EXPRESSION
    register: str = DEFAULT_REGISTER
    pronoun_style: PronounStyle = PronounStyle.SPARSE
    mode: ContextMode = ContextMode.CASUAL
    dials: PersonaDials = PersonaDials()
    address: AddressPreference = AddressPreference()
    source: str = "default"

    @property
    def self_word(self) -> str:
        return SELF_WORD[self.pronoun_style]

    @property
    def address_word(self) -> str:
        """
        What she calls him, with his own instruction applied.

        A stated preference outranks the style's default; a banned word
        yields nothing rather than a substitute, because the honest answer
        to "don't call me bro" is to use no address term at all, not to
        invent a different one.
        """

        if self.address.preferred:
            return self.address.preferred

        word = ADDRESS_WORD[self.pronoun_style]

        if word and word.lower() in self.address.forbidden:
            return ""

        return word

    @property
    def brainrot_level(self) -> int:
        """The brainrot dial on the brief's 0-5 scale."""

        return round(self.dials.brainrot * BRAINROT_SCALE)


# ----------------------------------------------------------------------
# Resolution
# ----------------------------------------------------------------------

def resolve(
    history=None,
    user_message=None,
    default_style: PronounStyle | None = None,
    dials: PersonaDials | None = None,
) -> PersonaState:
    """
    The persona for one turn, derived from the conversation so far.

    `history` is the same oldest-first list of Messages the prompt is
    built from, and `user_message` the turn's own message. Both optional:
    with neither, this returns Aura's default register, which is what a
    first message gets.

    Precedence for the pronoun style, highest first:

        1. what the *current* message is written in, when it carries a
           register - he just changed how he is talking, and matching him
           is the point
        2. what Aura last used in this conversation - continuity, so a
           settled pair stays settled through turns that carry no signal
        3. what he last used in this conversation
        4. the configured default, or SPARSE

    Rule 1 above rule 2 is the deliberate part. Continuity is what stops
    drift *within* a register; it must not be what stops her from
    following him when he moves.
    """

    messages = list(history or [])

    text = _content_of(user_message)

    preference = AddressPreference()

    for message in messages:
        if _role_of(message) == "user":
            preference = preference.merged_with(
                read_preference(_content_of(message))
            )

    preference = preference.merged_with(read_preference(text))

    style, source = _resolve_style(messages, text, default_style)

    mode = read_mode(text)

    base = MODE_DIALS[mode]

    # A configured dial set is a ceiling, not a replacement. The mode
    # still gets to move brainrot and humour - otherwise "debugging mode"
    # would be a label with no effect - but neither may exceed what the
    # operator asked for, which is what makes `brainrot: 0.0` in config
    # mean brainrot off in every mode including EXCITED.
    if dials is not None:
        base = replace(
            base,
            brainrot=min(base.brainrot, dials.brainrot),
            humor=min(base.humor, dials.humor),
        )

    return PersonaState(
        pronoun_style=style,
        mode=mode,
        dials=base,
        address=preference,
        source=source,
    )


def _resolve_style(messages, text, default_style):
    """The pronoun style and the reason for it. See `resolve`."""

    mirrored = read_style(text)

    if mirrored is not None:
        return mirrored, "mirrored"

    for message in reversed(messages):

        if _role_of(message) != "assistant":
            continue

        settled = read_aura_style(_content_of(message))

        if settled is not None:
            return settled, "continuity"

    for message in reversed(messages):

        if _role_of(message) != "user":
            continue

        earlier = read_style(_content_of(message))

        if earlier is not None:
            return earlier, "history"

    if default_style is not None:
        return default_style, "configured"

    return PronounStyle.SPARSE, "default"


def _content_of(message) -> str:
    """The text of a Message, a dict, or a bare string."""

    if message is None:
        return ""

    if isinstance(message, str):
        return message

    if isinstance(message, dict):
        return str(message.get("content") or "")

    return str(getattr(message, "content", "") or "")


def _role_of(message) -> str:

    if isinstance(message, dict):
        return str(message.get("role") or "")

    return str(getattr(message, "role", "") or "")


# ----------------------------------------------------------------------
# Rendering
#
# The one paragraph every provider receives, in its system slot. Short on
# purpose: personality.md is the description, and this is the part that
# changes per turn. A long block here would be a second personality file
# competing with the first, and a model given two descriptions of the same
# character follows whichever it read last.
# ----------------------------------------------------------------------

CONTRACT = (
    "You are Aura: female, Vietnamese, Gen-Z, warm, playful, curious, "
    "technically sharp. This is who you are on every model. The model "
    "generating this reply is an engine, not a character - it does not get "
    "to reintroduce its own default assistant voice, its own name, or its "
    "own idea of how to address the user. Wording may differ between "
    "models; the person may not."
)

LANGUAGE = (
    "Vietnamese is your language. Keep English technical terms in English "
    "and let English filler in where it falls naturally - ok, nah, fair, "
    "honestly, wait, lowkey - without forcing it. Vietnamese particles "
    "(ơ, ê, ủa, khoan, trời ơi) are yours to use when they fit."
)

RESTRAINT = (
    "Slang, emoji and brainrot are seasoning: use them where they land and "
    "nowhere else. Never open with the same phrase twice in a row, never "
    "put the address term in every sentence, and never reach for a trend "
    "word because it is available. Style never costs accuracy - if being "
    "casual would make a technical answer less exact, be exact."
)


def render(state: PersonaState | None) -> str:
    """
    The PERSONA section body for one turn, or "" for no state.

    Four lines, in descending permanence: the contract, the language, the
    register this conversation is in, and the mode this message is in. The
    dials go last as numbers, because "brainrot 2/5" is one token pair
    where "mildly brainrot but not too much" is a paragraph a model has to
    interpret.
    """

    if state is None:
        return ""

    lines = [CONTRACT, LANGUAGE, _pronoun_line(state), MODE_NOTES[state.mode]]

    lines.append(
        "Dials, as a style signal and not a template: "
        f"energy {state.dials.energy:.2f}, humour {state.dials.humor:.2f}, "
        f"brainrot {state.brainrot_level}/{BRAINROT_SCALE}, "
        f"precision {state.dials.technicality:.2f}, "
        f"warmth {state.dials.warmth:.2f}."
    )

    lines.append(RESTRAINT)

    return "\n\n".join(line for line in lines if line)


def _pronoun_line(state: PersonaState) -> str:
    """
    The register, as one instruction the model cannot read two ways.

    Stated as a decision already made, not as a menu. A model asked to
    "pick a natural pronoun pair" picks a different one each turn, and that
    is precisely the drift this section exists to prevent.
    """

    self_word = state.self_word
    address = state.address_word

    if self_word:
        line = (
            f'Register for this conversation: call yourself "{self_word}"'
        )
        if address:
            line += f' and him "{address}"'
        line += (
            ". Keep it for the whole reply and do not mix in any other "
            "first-person pronoun."
        )
    else:
        line = (
            "Register for this conversation: no pronoun pair has been "
            "established, so write without one - Vietnamese carries this "
            "fine, and it reads better than guessing a pair."
        )

    if state.address.forbidden:
        banned = ", ".join(f'"{word}"' for word in state.address.forbidden)
        line += f" He has asked you not to call him {banned}; never do."

    line += (
        f" Never use {' or '.join(COARSE_WORDS)} back at him, whatever he "
        "uses himself."
    )

    return line


# One line, for the Android agent's `complete` message - the only text in
# that prompt a person ever reads. Deliberately not `render()`: the agent
# prompt is answered with JSON for a parser, and the reason AURA-P0-007
# happened is that it once asked for warm conversational prose and raw
# JSON in the same breath. This names the voice without describing it at
# length, and it comes from here so there is still one source for it.
AGENT_VOICE = (
    "in Aura's own voice - Vietnamese, casual, Gen-Z, one or two sentences, "
    "no emoji spam"
)


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------

class AuraPersona:
    """
    Resolves and renders the persona. Holds settings, never a conversation.

    Stateless between turns for the same reason `CharacterAnchor` is: the
    server shares one of these across every session, so anything it
    remembered would leak from one conversation into another.
    """

    def __init__(
        self,
        default_style: PronounStyle | None = None,
        dials: PersonaDials | None = None,
    ):
        self.default_style = default_style
        self.dials = dials

    def resolve(self, history=None, user_message=None) -> PersonaState:
        return resolve(
            history=history,
            user_message=user_message,
            default_style=self.default_style,
            dials=self.dials,
        )

    def render(self, state: PersonaState | None) -> str:
        return render(state)

    def __repr__(self) -> str:
        return (
            f"AuraPersona(default_style={self.default_style}, "
            f"dials={self.dials})"
        )


class NullPersona:
    """Resolves to nothing, renders nothing. The shape of "switched off"."""

    def resolve(self, history=None, user_message=None) -> None:
        return None

    def render(self, state) -> str:
        return ""


def parse_style(name: str) -> PronounStyle | None:
    """
    A pronoun style from a config string, or None for empty/unknown.

    None means "derive it from the conversation", which is the shipped
    behaviour: pinning a pair in config is the escape hatch, not the
    default, because a hard-coded pair is one of the two failures the
    brief names (the other being a pair that changes every sentence).
    """

    text = (name or "").strip().lower()

    if not text:
        return None

    for style in PronounStyle:
        if style.value == text:
            return style

    return None


def build_persona(config: dict | None = None):
    """
    Build the persona layer from the `personality.persona` config section.

    Disabled yields a NullPersona, so the off path costs one attribute
    lookup and the prompt loses the section entirely.
    """

    settings = config or {}

    if not settings.get("enabled", True):
        return NullPersona()

    return AuraPersona(
        default_style=parse_style(settings.get("pronoun_style", "")),
        dials=_dials_from(settings),
    )


def _dials_from(settings: dict) -> PersonaDials | None:
    """
    The configured ceilings, or None when the file names none.

    None rather than a full default set, so `resolve` can tell "the
    operator asked for a cap" from "the operator said nothing" - the
    second must leave every mode's own dials untouched.
    """

    keys = ("brainrot", "humor", "humour")

    if not any(key in settings for key in keys):
        return None

    default = PersonaDials()

    return replace(
        default,
        brainrot=_as_level(settings.get("brainrot"), default.brainrot),
        humor=_as_level(
            settings.get("humor", settings.get("humour")), default.humor
        ),
    )


def _as_level(value, fallback: float) -> float:
    """A 0.0-1.0 float, or the fallback. A bad setting is not a crash."""

    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return fallback


def persona_of(source, history=None, user_message=None) -> PersonaState | None:
    """
    The state from any persona source, or None from one that has none.

    Read defensively for the same reason `hint_of` and `anchor_of` are: a
    broken collaborator must cost a prompt section, never the reply.
    """

    if source is None:
        return None

    resolver = getattr(source, "resolve", None)

    if resolver is None:
        return None

    try:
        return resolver(history, user_message)
    except Exception:
        return None


def render_of(source, state) -> str:
    """The rendered section from any persona source, or ""."""

    if source is None or state is None:
        return ""

    renderer = getattr(source, "render", None)

    if renderer is None:
        return render(state)

    try:
        return renderer(state) or ""
    except Exception:
        return ""


__all__ = [
    "IDENTITY",
    "GENDER_EXPRESSION",
    "DEFAULT_REGISTER",
    "BRAINROT_SCALE",
    "PronounStyle",
    "ContextMode",
    "PersonaDials",
    "PersonaState",
    "AddressPreference",
    "AuraPersona",
    "NullPersona",
    "MODE_DIALS",
    "MODE_NOTES",
    "CONTRACT",
    "LANGUAGE",
    "RESTRAINT",
    "AGENT_VOICE",
    "COARSE_WORDS",
    "SELF_WORD",
    "ADDRESS_WORD",
    "words_of",
    "read_style",
    "read_aura_style",
    "read_mode",
    "read_preference",
    "resolve",
    "render",
    "parse_style",
    "build_persona",
    "persona_of",
    "render_of",
]
