"""
Checking the reply came back in the register the prompt asked for.

`brain/persona.py` states the register as one unambiguous line - call
yourself "tớ", call him "cậu", never use "mày" or "tao" back at him - and
then hopes. Section 14 is the observation that hoping is not a mechanism:
"prompt instructions alone are insufficient". A model that agrees to the
register and then writes "mày" once in the third paragraph has broken the
contract, and until this module existed nothing downstream noticed.

The scope is exactly the promises already in the prompt, and deliberately
not one word wider. Every correction here maps to a sentence a model was
actually given:

    _pronoun_line   "Never use tao or mày back at him, whatever he uses
                    himself."
    _pronoun_line   "He has asked you not to call him X; never do."
    _pronoun_line   "do not mix in any other first-person pronoun"
    RESTRAINT       "never put the address term in every sentence"
    RESTRAINT       "Slang, emoji and brainrot are seasoning"

Enforcing a rule the prompt never stated would be a different thing: the
model would be corrected for obeying its instructions, and the fix would
be invisible to whoever wrote them.

*This is the one layer allowed to substitute words.* `brain/style.py`
states the opposite rule as absolute - "It never substitutes, never
reorders, never paraphrases" - and that rule is right for what it does,
because deciding whether a clause is filler is a judgement about meaning
and a regex that reached into a sentence would eventually reach into a
stack trace. A pronoun is a different kind of object: a closed vocabulary
of eight words, replaced by another word from the same closed set, chosen
by `resolve` rather than by this module. That is a lookup, not a
paraphrase. Facts are still untouchable here - the two layers agree about
that, and this file adds nothing to what `style` may do.

Two limits section 14 names outright, both enforced by construction rather
than by care:

    code is never touched     hidden behind placeholders first, the same
                              mechanism `style` uses, so an identifier
                              called `tao_count` is not a violation

    quoted text is never      he may write however he likes, and quoting
    touched                   him is not Aura addressing him

And one that section 2 names. The owner configures Aura: if he has asked
to be called "mày", that is what he is called, and this module does not
know better. `AddressPreference.preferred` carries that, and it is checked
before anything is corrected.

What this module does *not* do, deliberately:

*It does not police what model the reply came from.* A model introducing
itself by its own name is a real section 7 violation, but "tớ đang chạy
trên Qwen3" is the owner asking about his own configuration and getting an
honest answer. Telling those apart needs to understand the sentence, and
deleting the second to catch the first would make Aura lie to her owner
about what she is running on - which sections 2 and 28 both weigh against
more heavily than section 7 weighs for it. The identity anchor and the
`CONTRACT` paragraph carry that promise; this layer does not.

*It does not paraphrase towards a voice.* Section 13 wants warmth and
Gen-Z texture, and no regex produces those. The prompt asks for them and
the model writes them; what is checkable is whether the register held, and
that is what is checked.

*It does not enforce two of `RESTRAINT`'s own sentences,* and the reason is
worth stating rather than leaving as an omission to be discovered.

"Never open with the same phrase twice in a row" needs the previous reply,
which the call site has - but there is no safe subtractive fix. Deleting a
repeated opener is only harmless when the opener carries no stance, and
telling "Ok," from "Không -" apart is exactly the filler judgement
`brain/style.py` already encodes as a closed list, which it already applies
unconditionally. So for the openers where deletion is safe this would add
nothing, and for the rest it would invert an answer's meaning - which is
the one thing both layers forbid.

"Never reach for a trend word because it is available" is the same shape:
whether a word landed is a fact about the sentence around it, and a
banned-word list would delete the times it landed along with the times it
did not. Both stay in the prompt, where a model can weigh them, and out of
here, where nothing can.
"""

import re

from brain.persona import (
    ADDRESS_WORD,
    AURA_SELF_WORDS,
    PersonaState,
    SELF_WORD,
    PronounStyle,
)
from brain.style import hide_code, restore_code


# ----------------------------------------------------------------------
# What may be corrected
# ----------------------------------------------------------------------

# The four words section 13 names: "AURA must NOT address the user as: mày
# tao ông bà unless the owner explicitly changes this preference."
#
# Each maps to which half of the register it belongs to, because the
# replacement differs: "tao" is Aura naming herself and becomes the self
# word, the rest are her naming him and become the address word. Swapping
# a self-reference for an address term would turn "tao đã sửa" - I fixed
# it - into "cậu đã sửa", which says he did, and that is a changed fact
# rather than a changed register.
SELF = "self"
ADDRESS = "address"

CORRECTABLE = {
    "mày": ADDRESS,
    "tao": SELF,
    "ông": ADDRESS,
    "bà": ADDRESS,
}


# Words that follow "ông" or "bà" when they are not addressing anybody.
#
# Unlike "mày" and "tao", these two have ordinary meanings that have
# nothing to do with the owner: "ông ấy" is "he", "bà ấy" is "she", "ông
# nội" is a grandfather, "bà chủ" is a proprietor. Correcting those would
# not tidy a register - it would rewrite a sentence about somebody else
# into a sentence addressed to him, which is a changed fact.
#
# So the ambiguous pair is corrected only in the vocative, approximated as
# "not followed by one of these". A guard that guessed wrong in the other
# direction - leaving a real "ông" in place - costs one uncorrected word
# in a reply the model was told not to write; guessing wrong this way
# costs the meaning of the sentence.
NOT_VOCATIVE = (
    "ấy", "ta", "ý", "này", "đó", "kia", "nọ", "nào",
    "nội", "ngoại", "bà", "ông", "cụ", "chủ", "trùm", "già",
)


# Aura's own first-person words, so a drifted one can be brought back to
# the established pair. Read off `persona.AURA_SELF_WORDS` rather than
# listed again here - the registers live in one place, and a second copy
# would enforce last week's vocabulary after the first was changed.
SELF_WORDS = tuple(AURA_SELF_WORDS)


# "mình" is the one self word with a second life: "của mình" is "one's
# own", "tự mình" is "by oneself", "chúng mình" is "we". None of those is
# Aura naming herself, and rewriting them produces Vietnamese nobody
# writes. Checked on the preceding word, which is what distinguishes them.
NOT_SELF_BEFORE = {
    "mình": ("của", "tự", "chúng", "riêng", "bản", "quê"),
}


# ----------------------------------------------------------------------
# Protecting what must not change
# ----------------------------------------------------------------------

# A quotation, in any of the marks a model actually produces. Non-greedy
# and single-line: a quote mark left open is punctuation, not a quotation,
# and treating it as one would let one stray character switch the whole
# validator off for the rest of the reply.
QUOTED = re.compile(r'"[^"\n]*"' + r"|'[^'\n]*'" + r"|“[^”\n]*”" + r"|‘[^’\n]*’")

QUOTE_MARK = "\x00QUOTE{index}\x00"

QUOTE_PATTERN = re.compile(r"\x00QUOTE(\d+)\x00")


def _hide_quotes(text: str) -> tuple[str, list[str]]:
    """Replace quoted spans with placeholders nothing here can match."""

    spans: list[str] = []

    def take(match: re.Match) -> str:
        spans.append(match.group(0))
        return QUOTE_MARK.format(index=len(spans) - 1)

    return QUOTED.sub(take, text), spans


def _restore_quotes(text: str, spans: list[str]) -> str:

    def give(match: re.Match) -> str:
        index = int(match.group(1))

        return spans[index] if index < len(spans) else match.group(0)

    return QUOTE_PATTERN.sub(give, text)


# ----------------------------------------------------------------------
# The corrections
# ----------------------------------------------------------------------

# One Vietnamese word, whole. `\b` is Unicode-aware for `str` patterns, so
# this does not fire inside "taobao" - and because the diacritic is part of
# the character, "tạo" (create) and "may" (lucky) are simply different
# strings and never match at all.
def _word_pattern(word: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)


def _matched_case(replacement: str, original: str) -> str:
    """The replacement, capitalised the way the word it replaces was."""

    if not replacement or not original:
        return replacement

    if original[0].isupper():
        return replacement[0].upper() + replacement[1:]

    return replacement


def _followed_by(text: str, end: int) -> str:
    """The next word after this position, lowercased, or ''."""

    rest = re.match(r"\s+([^\W\d_]+)", text[end:], re.UNICODE)

    return rest.group(1).lower() if rest else ""


def _preceded_by(text: str, start: int) -> str:
    """The previous word before this position, lowercased, or ''."""

    before = re.search(r"([^\W\d_]+)\s+$", text[:start], re.UNICODE)

    return before.group(1).lower() if before else ""


def _drop(text: str, start: int, end: int) -> str:
    """
    Remove a word and exactly one of the spaces around it.

    Leaving both would give a double space, and stripping punctuation with
    it would change more than the word.
    """

    after = text[end:]
    before = text[:start]

    if after.startswith(" "):
        return before + after[1:]

    if before.endswith(" "):
        return before[:-1] + after

    return before + after


def _substitute(text: str, word: str, replacement: str, guard=None) -> str:
    """
    Replace one register word throughout, or drop it when there is nothing
    to replace it with.

    Right to left, so the offsets of the matches still ahead of us are the
    offsets in the string we are still looking at. `guard` is asked about
    each match before anything happens to it, which is where "this 'ông'
    means somebody's grandfather" lives.
    """

    result = text

    for match in reversed(list(_word_pattern(word).finditer(text))):

        if guard is not None and not guard(result, match):
            continue

        if replacement:
            result = (
                result[: match.start()]
                + _matched_case(replacement, match.group(0))
                + result[match.end() :]
            )
        else:
            result = _drop(result, match.start(), match.end())

    return result


def _vocative(text: str, match: re.Match) -> bool:
    """Whether this "ông"/"bà" is addressing him rather than describing."""

    return _followed_by(text, match.end()) not in NOT_VOCATIVE


def _self_reference(word: str):
    """A guard for the self words that mean something else in a phrase."""

    blocked = NOT_SELF_BEFORE.get(word)

    if not blocked:
        return None

    def guard(text: str, match: re.Match) -> bool:
        return _preceded_by(text, match.start()) not in blocked

    return guard


def _correct_address(text: str, state: PersonaState) -> str:
    """
    The four words section 13 names, brought back to the register.

    `state.address_word` is the target rather than a constant in this file,
    which is the whole point: the prompt told the model one pair, chosen by
    `resolve` from the conversation, and this enforces *that* pair. A
    hard-coded "cậu" here would override the owner's own register the
    moment he wrote in another one - which is section 2's complaint, from
    the inside.
    """

    address = state.address_word
    myself = state.self_word

    for word, role in CORRECTABLE.items():

        # Section 2. He asked for it; it is not a violation.
        if word == (state.address.preferred or "").lower():
            continue

        replacement = address if role is ADDRESS else myself

        # An empty replacement drops the word. That is right for SPARSE,
        # where no pair has been established and substituting one would
        # settle a register the conversation has not, and for a banned
        # word, where `PersonaState.address_word` already documents the
        # bargain: the honest answer to "don't call me bro" is no address
        # term, not a different one.
        text = _substitute(
            text,
            word,
            replacement,
            guard=_vocative if word in ("ông", "bà") else None,
        )

    return text


def _correct_forbidden(text: str, state: PersonaState) -> str:
    """Words he asked not to be called, removed rather than swapped."""

    for word in state.address.forbidden:
        text = _substitute(text, word, "")

    return text


def _correct_self(text: str, state: PersonaState) -> str:
    """
    One first-person pronoun for the whole reply.

    Skipped entirely when no pair is established: there is no target to
    correct towards, and picking one would be this module settling a
    register the conversation has not.
    """

    myself = state.self_word

    if not myself:
        return text

    for word in SELF_WORDS:

        if word == myself:
            continue

        text = _substitute(text, word, myself, guard=_self_reference(word))

    return text


# ----------------------------------------------------------------------
# Restraint
# ----------------------------------------------------------------------

# The emoji blocks a model actually reaches for, and the joiners that make
# several codepoints into one picture. The joiners matter more than the
# blocks: 👨‍💻 is two emoji and a zero-width joiner, and a filter that
# counted them separately would "collapse" it into a man with no laptop.
EMOJI = (
    "\U0001F000-\U0001FAFF"
    "☀-➿"
    "⬀-⯿"
)

# One emoji, however many codepoints it takes.
EMOJI_TOKEN = (
    rf"[{EMOJI}]"
    rf"(?:️|‍[{EMOJI}]|[\U0001F3FB-\U0001F3FF])*"
)

# Two or more of them in a row, whitespace between them or not.
EMOJI_RUN = re.compile(rf"({EMOJI_TOKEN})(?:[ \t]*{EMOJI_TOKEN})+")


def _collapse_emoji(text: str) -> str:
    """
    A pile of emoji becomes the first one.

    `RESTRAINT` promises they are seasoning, and section 13 names the
    failure exactly: "Do not blindly spam: bro 💀 😭 😂 fr ngl." Keeping
    the first is what makes this subtractive - the reply keeps its tone and
    loses the pile - and one emoji per thought is left alone, because a run
    is the thing that reads as spam, not the presence of any at all.
    """

    return EMOJI_RUN.sub(lambda match: match.group(1), text)


# A sentence, for the purpose of "the address term in every sentence".
#
# Deliberately not `persona.SENTENCE`, which splits on commas too because
# an addressing instruction arrives clause by clause ("đừng gọi tớ là bro,
# gọi tớ là cậu"). Here a comma clause is not a sentence: "Cậu thử lại đi,
# cậu nhé" is one thought said warmly, and thinning it would be policing
# emphasis.
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")

MIN_SENTENCES_FOR_A_HABIT = 3


def _thin_address(text: str, state: PersonaState) -> str:
    """
    Drop the address term from every sentence after the first.

    Only when it really is in *every* sentence, and only past three of
    them. Twice is emphasis, and a validator that corrected emphasis would
    be rewriting a reply that kept its promise; the first one always
    survives, because dropping them all would make her colder than the
    register says she is.
    """

    address = state.address_word

    if not address:
        return text

    pieces = SENTENCE_SPLIT.split(text)
    pattern = _word_pattern(address)
    carrying = [piece for piece in pieces if pattern.search(piece)]

    if len(carrying) < MIN_SENTENCES_FOR_A_HABIT:
        return text

    if len(carrying) != len([piece for piece in pieces if piece.strip()]):
        return text

    seen = False
    rebuilt: list[str] = []

    for piece in pieces:

        if not pattern.search(piece):
            rebuilt.append(piece)
            continue

        if not seen:
            seen = True
            rebuilt.append(piece)
            continue

        rebuilt.append(_recapitalise(_substitute(piece, address, "")))

    return _rejoin(text, pieces, rebuilt)


def _recapitalise(sentence: str) -> str:
    """Start a sentence with a capital again after its first word went."""

    stripped = sentence.lstrip()

    if not stripped:
        return sentence

    lead = sentence[: len(sentence) - len(stripped)]

    return lead + stripped[0].upper() + stripped[1:]


def _rejoin(original: str, pieces: list[str], rebuilt: list[str]) -> str:
    """
    Put the sentences back with the separators they came with.

    The splitter consumes the whitespace between sentences, so joining on
    a chosen string would quietly reformat a reply - two spaces become one,
    a line break becomes a space. Read back off the original instead.
    """

    result = []
    position = 0

    for piece, replacement in zip(pieces, rebuilt):
        index = original.find(piece, position)

        if index < 0:
            return " ".join(rebuilt)

        result.append(original[position:index])
        result.append(replacement)
        position = index + len(piece)

    result.append(original[position:])

    return "".join(result)


# ----------------------------------------------------------------------
# The one entry point
# ----------------------------------------------------------------------

def validate(text: str, state: PersonaState | None) -> str:
    """
    Bring a reply into the register the resolver settled on.

    Subtractive and substitutive in one narrow way each: the four coarse
    words become the register's own address and self terms, a forbidden word
    is dropped, a pile of emoji loses everything after the first, and an
    address term repeated in every sentence keeps only its first. Nothing
    else is touched. No sentence is reordered, no fact is edited, no phrase
    is paraphrased.

    `state` of None means no persona was resolved, and the text comes back
    exactly as it went in. That is not a convenience for callers - it is
    section 14's exemption, arriving structurally. A machine turn resolves
    no persona, so a JSON action cannot be reached by a pronoun pass even
    if someone later wires this into the wrong place. The alternative, a
    flag threaded through from `is_machine_turn`, would be one boolean away
    from rewriting the field names in an action the service then fails to
    parse.

    Order matters in two places. Code is hidden before quotes, because a
    fenced block can contain quotation marks and hiding those first would
    cut it in half. And the corrections run before the two restraints,
    because thinning counts occurrences of the register's address word -
    which is a word the corrections may just have put there.
    """

    if not text or state is None:
        return text

    protected, code = hide_code(text)
    protected, quotes = _hide_quotes(protected)

    protected = _correct_address(protected, state)
    protected = _correct_forbidden(protected, state)
    protected = _correct_self(protected, state)

    protected = _collapse_emoji(protected)
    protected = _thin_address(protected, state)

    protected = _restore_quotes(protected, quotes)

    return restore_code(protected, code)
