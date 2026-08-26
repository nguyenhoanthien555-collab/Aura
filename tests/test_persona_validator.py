"""
The persona output validator: what reaches the user, not what was asked for.

`brain/persona.py` states the register in the prompt; this is the layer
that checks the answer came back in it. Section 14 exists because
"prompt instructions alone are insufficient" - a model that agrees to
call the owner "cậu" and then writes "mày" once in paragraph three has
broken the contract, and nothing downstream noticed.

What is tested here is the promise the prompt already makes, and nothing
wider. Every correction below maps to a sentence in `_pronoun_line`,
`RESTRAINT` or `CONTRACT`:

    "Never use tao or mày back at him, whatever he uses himself."
    "He has asked you not to call him X; never do."
    "do not mix in any other first-person pronoun"
    "never put the address term in every sentence"
    "Slang, emoji and brainrot are seasoning"

Two hard limits, both from section 14 and both tested by trying to break
them: code is never touched, and quoted text is never touched. A
validator that reached into a stack trace or edited the owner's own words
back at him would be worse than the drift it fixes.

The owner's own configuration outranks all of it (section 2). If he has
said "call me mày", the validator does not know better.
"""

from brain.message import Message
from brain.persona import (
    AddressPreference,
    PersonaState,
    PronounStyle,
    resolve,
)
from brain.persona_validator import CORRECTABLE, validate


def state_for(
    style=PronounStyle.CAU_TO,
    preferred="",
    forbidden=(),
) -> PersonaState:
    return PersonaState(
        pronoun_style=style,
        address=AddressPreference(preferred=preferred, forbidden=tuple(forbidden)),
    )


CAU = state_for()
TUI = state_for(PronounStyle.TUI_BRO)
SPARSE = state_for(PronounStyle.SPARSE)


# ======================================================================
# The address term, which is what section 13 names
# ======================================================================

class TestCoarseAddress:

    def test_the_mandates_own_example(self):
        # Section 14's worked example: "Mày thu..." -> "Cậu thu...".
        assert validate("Mày thử lại đi", CAU) == "Cậu thử lại đi"

    def test_a_coarse_address_becomes_the_established_one(self):
        assert validate("để mày xem", CAU) == "để cậu xem"
        assert validate("để mày xem", TUI) == "để bro xem"

    def test_a_coarse_self_reference_becomes_the_established_one(self):
        assert validate("tao đã sửa rồi", CAU) == "tớ đã sửa rồi"
        assert validate("tao đã sửa rồi", TUI) == "tui đã sửa rồi"

    def test_capitalisation_survives_the_correction(self):
        assert validate("Tao xong rồi", CAU) == "Tớ xong rồi"
        assert validate("Mày ổn không", CAU) == "Cậu ổn không"

    def test_with_no_register_established_the_word_is_dropped_not_guessed(self):
        # SPARSE is the style with no pronouns in it. `address_word`
        # returns "" there rather than inventing a pair, and this has to
        # agree with it - substituting a guess would be the validator
        # establishing a register the conversation never did.
        assert validate("mày thử lại đi", SPARSE) == "thử lại đi"

    def test_every_word_section_13_names_is_correctable(self):
        # mày, tao, ông, bà - the four the mandate lists.
        for word in ("mày", "tao", "ông", "bà"):
            assert word in CORRECTABLE

    def test_a_vocative_ong_is_corrected(self):
        assert validate("ông thử lại đi", CAU) == "cậu thử lại đi"

    def test_all_of_them_in_one_reply(self):
        assert validate("Mày nghe tao nói này ông", CAU) == "Cậu nghe tớ nói này cậu"


class TestWordsThatOnlyLookCoarse:

    def test_a_third_person_ong_is_left_alone(self):
        # "ông ấy" is "he". Correcting it would rewrite a fact about
        # someone else into an address to the owner.
        assert validate("ông ấy đã đóng issue đó", CAU) == "ông ấy đã đóng issue đó"
        assert validate("bà ấy là maintainer", CAU) == "bà ấy là maintainer"

    def test_a_kinship_ong_is_left_alone(self):
        assert validate("ông nội cậu gọi kìa", CAU) == "ông nội cậu gọi kìa"

    def test_tao_with_its_diacritic_is_a_different_word(self):
        # "tạo" is "create". Nothing about it is a pronoun.
        assert validate("tớ sẽ tạo file mới", CAU) == "tớ sẽ tạo file mới"

    def test_may_without_its_diacritic_is_a_different_word(self):
        assert validate("may là chưa deploy", CAU) == "may là chưa deploy"

    def test_a_coarse_word_inside_a_longer_word_is_left_alone(self):
        assert validate("cái taobao đó", CAU) == "cái taobao đó"


class TestTheOwnersPreferenceWins:

    def test_a_word_the_owner_asked_for_is_never_corrected(self):
        # Section 2: the owner configures Aura, and section 13's default
        # holds "unless the owner explicitly changes this preference".
        asked = state_for(preferred="mày")

        assert validate("mày thử lại đi", asked) == "mày thử lại đi"

    def test_a_forbidden_word_is_dropped_rather_than_swapped(self):
        # `PersonaState.address_word` already documents this bargain: the
        # honest answer to "don't call me bro" is no address term, not a
        # different one. The validator agrees with it rather than having
        # an opinion of its own.
        banned = state_for(PronounStyle.TUI_BRO, forbidden=("bro",))

        assert validate("bro thử lại đi", banned) == "thử lại đi"

    def test_a_forbidden_word_is_dropped_mid_sentence_too(self):
        banned = state_for(PronounStyle.TUI_BRO, forbidden=("bro",))

        assert validate("để bro xem đã", banned) == "để xem đã"


# ======================================================================
# One first-person pronoun, not three
# ======================================================================

class TestSelfReference:

    def test_a_drifted_self_pronoun_is_brought_back(self):
        # `_pronoun_line` says "do not mix in any other first-person
        # pronoun". This is that sentence, enforced.
        assert validate("tui đã fix xong", CAU) == "tớ đã fix xong"
        assert validate("mình đã fix xong", CAU) == "tớ đã fix xong"

    def test_the_established_pronoun_is_left_alone(self):
        assert validate("tớ đã fix xong", CAU) == "tớ đã fix xong"
        assert validate("tui đã fix xong", TUI) == "tui đã fix xong"

    def test_nothing_is_corrected_when_no_register_is_established(self):
        # There is no target to correct towards, and picking one would be
        # the validator settling a register the conversation has not.
        assert validate("mình đã fix xong", SPARSE) == "mình đã fix xong"

    def test_minh_meaning_oneself_is_left_alone(self):
        # "của mình" is "one's own", "tự mình" is "by oneself", "chúng
        # mình" is "we". None of them is Aura naming herself, and swapping
        # them produces Vietnamese nobody writes.
        assert validate("file của mình đó", CAU) == "file của mình đó"
        assert validate("cậu tự mình chạy thử nha", CAU) == "cậu tự mình chạy thử nha"
        assert validate("chúng mình thử lại đi", CAU) == "chúng mình thử lại đi"

    def test_a_drifted_pronoun_in_the_same_reply_as_a_good_one(self):
        assert validate("tớ xem rồi, mình nghĩ là do cache", CAU) == (
            "tớ xem rồi, tớ nghĩ là do cache"
        )


# ======================================================================
# Section 14's two hard limits
# ======================================================================

class TestCodeIsNeverTouched:

    def test_a_fenced_block_survives_verbatim(self):
        text = 'Thử cái này:\n```python\nuser = "mày"\nprint(tao)\n```\nxong rồi mày'
        expected = 'Thử cái này:\n```python\nuser = "mày"\nprint(tao)\n```\nxong rồi cậu'

        assert validate(text, CAU) == expected

    def test_an_inline_span_survives_verbatim(self):
        assert validate("chạy `git checkout mày` đi mày", CAU) == (
            "chạy `git checkout mày` đi cậu"
        )

    def test_an_identifier_that_looks_like_a_pronoun_survives(self):
        # The reason this layer hides code before doing anything: a
        # variable named `tao_count` is not a register violation, and a
        # validator that renamed it would break the code it was quoting.
        assert validate("`tao_count` đang sai", CAU) == "`tao_count` đang sai"


class TestQuotedTextIsNeverTouched:

    def test_the_owners_own_words_are_not_edited_back_at_him(self):
        # Section 14 states this outright. He is allowed to write however
        # he likes; quoting him is not Aura addressing him.
        assert validate('cậu vừa nói "mày làm hộ tao" đúng không', CAU) == (
            'cậu vừa nói "mày làm hộ tao" đúng không'
        )

    def test_single_quotes_and_curly_quotes_count_too(self):
        assert validate("cậu ghi 'mày' à", CAU) == "cậu ghi 'mày' à"
        assert validate("cậu ghi “mày” à", CAU) == "cậu ghi “mày” à"

    def test_text_outside_the_quotes_is_still_corrected(self):
        assert validate('mày nói "tao xong rồi" mà', CAU) == (
            'cậu nói "tao xong rồi" mà'
        )

    def test_an_unclosed_quote_protects_nothing(self):
        # A lone quote mark is punctuation, not a quotation. Treating it as
        # one would let a single stray character switch the whole validator
        # off for the rest of the reply.
        assert validate('mày ơi " thử lại đi mày', CAU) == 'cậu ơi " thử lại đi cậu'


# ======================================================================
# Seasoning, not spam
# ======================================================================

class TestEmojiRestraint:

    def test_a_single_emoji_is_fine(self):
        assert validate("xong rồi nha 😄", CAU) == "xong rồi nha 😄"

    def test_a_pile_collapses_to_one(self):
        # Section 13 names this exact failure: "Do not blindly spam: bro 💀
        # 😭 😂 fr ngl." Collapsing a run is subtractive - the first one
        # survives, so the reply keeps its tone and loses the pile.
        assert validate("xong rồi 💀😭😂", CAU) == "xong rồi 💀"

    def test_a_pile_with_spaces_in_it_still_collapses(self):
        assert validate("xong rồi 💀 😭 😂", CAU) == "xong rồi 💀"

    def test_emoji_in_different_sentences_are_left_alone(self):
        # One per thought is seasoning. Only the run is the problem.
        assert validate("xong rồi 😄 cậu thử lại nha 🙏", CAU) == (
            "xong rồi 😄 cậu thử lại nha 🙏"
        )

    def test_emoji_inside_code_are_left_alone(self):
        assert validate("`print('💀😭😂')`", CAU) == "`print('💀😭😂')`"


class TestAddressRestraint:

    def test_the_term_in_every_sentence_is_thinned_out(self):
        # `RESTRAINT` promises "never put the address term in every
        # sentence". The first one survives, because dropping all of them
        # would make her sound colder than her register says she is.
        assert validate(
            "Cậu thử lại đi. Cậu xem log chưa. Cậu gửi tớ với.", CAU
        ) == "Cậu thử lại đi. Xem log chưa. Gửi tớ với."

    def test_two_sentences_are_not_enough_to_be_a_habit(self):
        # Twice is emphasis; a validator that policed it would be
        # rewriting a reply that kept its promise.
        assert validate("Cậu thử lại đi. Cậu xem log chưa.", CAU) == (
            "Cậu thử lại đi. Cậu xem log chưa."
        )

    def test_a_reply_that_uses_it_sometimes_is_left_alone(self):
        assert validate(
            "Cậu thử lại đi. Tớ xem log rồi. Cậu gửi tớ với.", CAU
        ) == "Cậu thử lại đi. Tớ xem log rồi. Cậu gửi tớ với."


# ======================================================================
# When it must do nothing at all
# ======================================================================

class TestRestraintOfTheValidatorItself:

    def test_no_persona_means_no_correction(self):
        # A deployment running `NullPersona` gets a byte-identical reply,
        # which is what makes this layer safe to add: with no register
        # stated in the prompt there is no promise to enforce.
        assert validate("mày thử lại đi", None) == "mày thử lại đi"

    def test_a_clean_reply_comes_back_unchanged(self):
        clean = "Tớ xem log rồi nha, lỗi ở chỗ `settings_store.py` thôi."

        assert validate(clean, CAU) == clean

    def test_empty_and_blank_text_survive(self):
        assert validate("", CAU) == ""
        assert validate("   ", CAU) == "   "
        assert validate(None, CAU) == None

    def test_correcting_twice_changes_nothing_more(self):
        # Idempotence, because this runs on a whole reply and a caller that
        # validated an already-validated string must not see it drift
        # further.
        text = "Mày nghe tao nói này 💀😭😂"
        once = validate(text, CAU)

        assert validate(once, CAU) == once

    def test_facts_are_never_edited(self):
        # The same absolute rule `brain/style.py` states: tone may change,
        # facts may not. Numbers, paths and identifiers are not pronouns.
        text = "Có 3 test fail ở tests/test_router.py dòng 214, timeout 30s."

        assert validate(text, CAU) == text


# ======================================================================
# Wiring: what actually reaches the user
# ======================================================================

class FakeStore:
    """Enough of ConversationStore to run turns with a chosen history."""

    def __init__(self, history=None):
        self.history = history or []
        self.saved: list[tuple[str, str]] = []

    def save(self, role, content):
        self.saved.append((role, content))

    def get_recent(self, limit):
        return list(reversed(self.history))[:limit]


class Recorder:
    """Enough of EventPublisher to see what a turn announced."""

    def __init__(self, into: list):
        self.into = into

    def publish(self, event) -> None:
        self.into.append(event)


class ScriptedLLM:
    """Answers with a fixed reply, whatever it was asked."""

    def __init__(self, reply: str):
        self.reply = reply
        self.prompt = ""

    def generate(self, prompt: str) -> str:
        self.prompt = prompt
        return self.reply


def manager_for(reply: str, history=None, persona=True):
    from brain.conversation import ConversationManager
    from brain.persona import AuraPersona
    from brain.prompt_builder import PromptBuilder

    store = FakeStore(history)
    llm = ScriptedLLM(reply)

    return ConversationManager(
        memory=store,
        builder=PromptBuilder(),
        llm=llm,
        persona=AuraPersona() if persona else None,
    ), store, llm


class TestThroughARealTurn:

    def test_a_drifted_reply_is_corrected_before_the_user_sees_it(self):
        manager, _store, _llm = manager_for("Mày thử lại đi")

        assert manager.chat("cậu ơi giúp tớ với").text == "Cậu thử lại đi"

    def test_the_register_the_conversation_settled_on_is_the_one_enforced(self):
        # Not a hard-coded pair. He writes in tui/bro, so "mày" becomes
        # "bro" - the validator enforces whatever `resolve` decided, which
        # is what keeps section 2 intact: the owner's register, not one
        # this module prefers.
        manager, _store, _llm = manager_for("Mày thử lại đi")

        assert manager.chat("bro fix hộ tui cái").text == "Bro thử lại đi"

    def test_what_is_remembered_is_what_was_shown(self):
        # A transcript holding the uncorrected reply would feed the drift
        # back to the model next turn as an example of how Aura talks.
        manager, store, _llm = manager_for("Mày thử lại đi")

        manager.chat("cậu ơi giúp tớ với")

        assert ("assistant", "Cậu thử lại đi") in store.saved

    def test_no_persona_leaves_the_reply_exactly_as_generated(self):
        manager, _store, _llm = manager_for("Mày thử lại đi", persona=False)

        assert manager.chat("cậu ơi").text == "Mày thử lại đi"

    def test_a_machine_turn_is_not_validated(self):
        # Structural, not a flag: a machine turn resolves no persona at
        # all, so there is no register to enforce. A JSON action rewritten
        # by a pronoun pass is an action the phone's parser cannot read.
        action = '{"action": "input_text", "node_id": "n1", "text": "mày tao 💀😭😂"}'
        manager, _store, _llm = manager_for(action)

        reply = manager.chat(
            "agent_tick",
            context={"device": {}, "accessibility_tree": {}},
        )

        assert reply.text == action

    def test_a_streamed_reply_is_corrected_in_its_finished_text(self):
        # Fragments cannot be corrected as they arrive - a pronoun can
        # straddle two chunks - so the finished event carries the corrected
        # reply, the same bargain the style filter already makes.
        from brain.conversation import ConversationManager
        from brain.persona import AuraPersona
        from brain.prompt_builder import PromptBuilder
        from events.types import StreamFinishedEvent

        published: list[object] = []

        manager = ConversationManager(
            memory=FakeStore([]),
            builder=PromptBuilder(),
            llm=ScriptedLLM("Mày thử lại đi"),
            persona=AuraPersona(),
            events=Recorder(published),
        )

        fragments = list(manager.chat_stream("cậu ơi giúp tớ với"))
        finished = [e for e in published if isinstance(e, StreamFinishedEvent)]

        # The fragment is what the model said; the finished text is what
        # the user is meant to keep.
        assert "".join(fragments) == "Mày thử lại đi"
        assert finished and finished[-1].text == "Cậu thử lại đi"


class TestTheModelDoesNotDecideTheRegister:

    def test_two_models_answering_differently_reach_the_user_the_same(self):
        # Section 7: "No model switch should materially change AURA's
        # behavior." Two engines, two habits, one register.
        polite = manager_for("Mình đã xem log rồi")[0]
        coarse = manager_for("Tao đã xem log rồi")[0]

        assert (
            polite.chat("cậu ơi xem log với").text
            == coarse.chat("cậu ơi xem log với").text
            == "Tớ đã xem log rồi"
        )


class TestAgainstTheResolverItself:

    def test_the_target_words_come_from_resolve_not_from_a_constant(self):
        # The validator and the prompt must name the same pair, and the
        # prompt's pair comes from `resolve`. Asserted against the resolver
        # rather than against a literal so that a change to the registers
        # cannot leave this layer enforcing last week's vocabulary.
        for message, drifted in (
            ("bro fix hộ tui cái", "mày tao"),
            ("cậu xem hộ tớ", "mày tao"),
            ("bạn xem giúp mình", "mày tao"),
        ):
            state = resolve(history=[], user_message=Message(role="user", content=message))
            corrected = validate(drifted, state)

            assert corrected == f"{state.address_word} {state.self_word}"
