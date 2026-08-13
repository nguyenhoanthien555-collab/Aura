"""
The persona contract: who Aura is being on this turn.

`brain/persona.py` is the per-turn half of the personality - the pronoun
register the conversation settled on, the context mode the message calls
for, and the dials. These tests cover the layer itself (reading a
register and a mode out of a message, resolving them against history,
rendering them into one paragraph) and the wiring (the section reaching
the prompt through a real turn, landing in the system slot, and being
identical across a provider fallback).

Prompt construction only. No LLM, no generation, no post-processing.
"""

import pytest

from brain.message import Message
from brain.persona import (
    AuraPersona,
    ContextMode,
    NullPersona,
    PersonaState,
    PronounStyle,
    build_persona,
    persona_of,
    read_mode,
    read_style,
    render,
    render_of,
    resolve,
)
from brain.prompt_builder import PromptBuilder
from brain.prompt_sections import PERSONA, USER
from brain.providers.base import split_prompt


# ======================================================================
# Reading a message
# ======================================================================

def test_the_register_is_read_from_the_message():
    assert read_style("cậu xem giúp tớ đoạn này") is PronounStyle.CAU_TO
    assert read_style("bro cái này bị gì vậy") is PronounStyle.TUI_BRO
    assert read_style("hello") is None


def test_a_coarse_message_is_matched_but_never_mirrored():
    """\"tao/mày\" resolve to the casual style, not to a style of their own."""

    assert read_style("tao thấy mày sai rồi") is PronounStyle.TUI_BRO


@pytest.mark.parametrize(
    "text, expected",
    [
        ("code này bị lỗi rồi", ContextMode.DEBUGGING),
        ("mệt quá, stress thật sự", ContextMode.SUPPORTIVE),
        ("BROOO NÓ CHẠY RỒI", ContextMode.EXCITED),
        ("nói thật đi, không đùa đâu", ContextMode.SERIOUS),
        ("class này import kiểu gì", ContextMode.TECHNICAL),
        ("hello", ContextMode.CASUAL),
    ],
)
def test_the_mode_is_read_from_the_message(text, expected):
    assert read_mode(text) is expected


# ======================================================================
# Resolution
# ======================================================================

def test_the_current_message_register_wins_over_history():
    """He just changed how he is talking; matching him is the point."""

    state = resolve(
        history=[Message(role="assistant", content="tui làm xong rồi")],
        user_message="cậu ơi check giúp tớ",
    )

    assert state.pronoun_style is PronounStyle.CAU_TO
    assert state.source == "mirrored"


def test_a_settled_register_survives_a_signal_free_turn():
    """Continuity: a pair already used stays used when nothing new arrives."""

    state = resolve(
        history=[
            Message(role="user", content="cậu xem giúp tớ"),
            Message(role="assistant", content="được, tớ soi cho"),
        ],
        user_message="ok",
    )

    assert state.pronoun_style is PronounStyle.CAU_TO
    assert state.source == "continuity"


def test_no_signal_resolves_to_sparse_not_a_guess():
    """A first message gets no pronoun pair rather than an invented one."""

    state = resolve(history=[], user_message="hello")

    assert state.pronoun_style is PronounStyle.SPARSE
    assert state.source == "default"


def test_a_configured_default_style_is_used():
    state = resolve(
        history=[],
        user_message="hello",
        default_style=PronounStyle.TUI_BRO,
    )

    assert state.pronoun_style is PronounStyle.TUI_BRO
    assert state.source == "configured"


def test_an_explicit_preference_outranks_everything():
    """\"Đừng gọi tớ là bro, gọi tớ là cậu\" is read as both halves."""

    state = resolve(
        history=[],
        user_message="đừng gọi tớ là bro, gọi tớ là cậu",
    )

    assert state.address.preferred == "cậu"
    assert state.address.forbidden == ("bro",)


def test_a_preference_earlier_in_the_conversation_survives():
    state = resolve(
        history=[Message(role="user", content="đừng gọi tớ là bro")],
        user_message="check giúp",
    )

    assert state.address.forbidden == ("bro",)


def test_the_mode_moves_the_dials_and_nothing_else():
    """Debugging turns the humour down; it does not change who is talking."""

    debugging = resolve(history=[], user_message="cái này lỗi rồi")
    casual = resolve(history=[], user_message="hello")

    assert debugging.mode is ContextMode.DEBUGGING
    assert debugging.dials.humor < casual.dials.humor
    assert debugging.dials.brainrot < casual.dials.brainrot
    assert debugging.identity == casual.identity == "aura"


def test_a_configured_dial_is_a_ceiling_in_every_mode():
    """brainrot: 0.0 in config means brainrot off even in EXCITED mode."""

    state = resolve(
        history=[],
        user_message="BROOO NÓ CHẠY RỒI",
        dials=__import__("brain.persona", fromlist=["PersonaDials"]).PersonaDials(
            brainrot=0.0
        ),
    )

    assert state.mode is ContextMode.EXCITED
    assert state.brainrot_level == 0


# ======================================================================
# Rendering
# ======================================================================

def test_render_locks_the_register():
    """Stated as a decision already made, not as a menu to pick from."""

    state = resolve(history=[], user_message="bro cái này lỗi rồi")
    text = render(state)

    assert 'call yourself "tui"' in text
    assert 'him "bro"' in text
    assert "Keep it for the whole reply and do not mix in any other" in text


def test_sparse_render_asks_for_no_pronouns():
    text = render(resolve(history=[], user_message="hello"))

    assert "no pronoun pair has been established" in text
    assert "write without one" in text


def test_a_forbidden_word_never_comes_back():
    state = resolve(
        history=[],
        user_message="đừng gọi tớ là bro",
    )
    text = render(state)

    assert 'He has asked you not to call him "bro"; never do.' in text


def test_render_names_the_contract_and_the_mode():
    text = render(resolve(history=[], user_message="bro cái này lỗi rồi"))

    assert "female" in text
    assert "Vietnamese" in text
    assert "find the cause" in text            # the DEBUGGING mode note
    assert "brainrot" in text                  # the dials line


def test_render_of_none_is_nothing():
    assert render(None) == ""
    assert render_of(None, None) == ""
    assert render_of(AuraPersona(), None) == ""


# ======================================================================
# Construction
# ======================================================================

def test_build_persona_reads_the_config():
    assert isinstance(build_persona({"enabled": False}), NullPersona)
    assert isinstance(build_persona({}), AuraPersona)

    pinned = build_persona({"pronoun_style": "cau_to"})

    assert isinstance(pinned, AuraPersona)
    assert pinned.default_style is PronounStyle.CAU_TO


def test_build_persona_tolerates_a_bad_style_name():
    persona = build_persona({"pronoun_style": "gangster"})

    assert isinstance(persona, AuraPersona)
    assert persona.default_style is None


def test_build_persona_tolerates_a_bad_dial_value():
    persona = build_persona({"brainrot": "very much", "humor": 0.2})

    assert isinstance(persona, AuraPersona)
    assert persona.dials is not None
    assert persona.dials.humor == 0.2
    assert persona.dials.brainrot == 0.40      # the default, not a crash


def test_persona_of_and_render_of_are_defensive():
    assert persona_of(None) is None
    assert persona_of(object()) is None

    state = persona_of(AuraPersona(), user_message="bro hi")

    assert isinstance(state, PersonaState)
    assert render_of(AuraPersona(), state)


# ======================================================================
# Wiring: the section reaches the prompt
# ======================================================================

class FakeStore:
    """Enough of ConversationStore to run turns with a chosen history."""

    def __init__(self, history=None):
        self.history = history or []
        self.saved: list[tuple[str, str]] = []

    def save(self, role, content):
        self.saved.append((role, content))

    def get_recent(self, limit):
        # Newest first, which is the store's contract
        return list(reversed(self.history))[:limit]


class CapturingLLM:
    """Keeps the prompt it was given."""

    def __init__(self):
        self.prompt = ""

    def generate(self, prompt: str) -> str:
        self.prompt = prompt
        return "sure"


def test_the_persona_section_is_emitted_through_the_manager():
    from brain.conversation import ConversationManager

    llm = CapturingLLM()

    manager = ConversationManager(
        memory=FakeStore([]),
        builder=PromptBuilder(),
        llm=llm,
        persona=AuraPersona(),
    )

    manager.chat("bro cái này lỗi rồi, check giúp tớ")

    assert PERSONA in llm.prompt
    assert 'call yourself "tui"' in llm.prompt
    assert 'him "bro"' in llm.prompt
    assert "find the cause" in llm.prompt      # DEBUGGING mode note


def test_no_persona_costs_no_section():
    from brain.conversation import ConversationManager

    llm = CapturingLLM()

    manager = ConversationManager(
        memory=FakeStore([]),
        builder=PromptBuilder(),
        llm=llm,
    )

    manager.chat("hey")

    assert PERSONA not in llm.prompt


def test_the_section_sits_under_the_personality_description():
    prompt = PromptBuilder().build(
        history=[],
        user_message=Message(role="user", content="bro check giúp tớ"),
        persona=render(resolve(user_message="bro check giúp tớ")),
    )

    from brain.prompt_sections import PERSONALITY

    assert PERSONALITY in prompt
    assert PERSONA in prompt
    assert prompt.index(PERSONALITY) < prompt.index(PERSONA) < prompt.index(USER)


def test_persona_lands_in_the_system_slot():
    """So every provider - including a fallback - reads it as instructions."""

    prompt = PromptBuilder().build(
        history=[],
        user_message=Message(role="user", content="bro check giúp tớ"),
        persona=render(resolve(user_message="bro check giúp tớ")),
    )

    system_inst, user_cont = split_prompt(prompt)

    assert PERSONA in system_inst
    assert PERSONA not in user_cont


def test_provider_fallback_keeps_the_persona_contract(monkeypatch):
    """The same contract reaches Gemini, Groq and Mistral, verbatim."""

    monkeypatch.setenv("GEMINI_API_KEY", "dummy-gemini-key")
    monkeypatch.setenv("GROQ_API_KEY", "dummy-groq-key")
    monkeypatch.setenv("MISTRAL_API_KEY", "dummy-mistral-key")

    from brain.providers.errors import ProviderRateLimitError, ProviderUnavailableError
    from brain.providers.gemini import GeminiProvider
    from brain.providers.groq import GroqProvider
    from brain.providers.mistral import MistralProvider

    captured = {}

    def mock_gemini_generate(self, prompt):
        sys, _ = split_prompt(prompt)
        captured["gemini"] = sys
        raise ProviderUnavailableError("Gemini down")

    def mock_groq_request(self, messages):
        for msg in messages:
            if msg["role"] == "system":
                captured["groq"] = msg["content"]
        raise ProviderRateLimitError("Groq 429")

    def mock_mistral_request(self, messages):
        for msg in messages:
            if msg["role"] == "system":
                captured["mistral"] = msg["content"]
        return {"choices": [{"message": {"content": "Mistral reply"}}]}

    monkeypatch.setattr(GeminiProvider, "generate", mock_gemini_generate)
    monkeypatch.setattr(GroqProvider, "_request", mock_groq_request)
    monkeypatch.setattr(MistralProvider, "_request", mock_mistral_request)

    from brain.router import BrainRouter

    history = [Message(role="user", content="cậu xem giúp tớ")]

    prompt = PromptBuilder().build(
        history=history,
        user_message=Message(role="user", content="đoạn này lỗi rồi"),
        persona=render(resolve(
            history=history,
            user_message="đoạn này lỗi rồi",
        )),
    )

    router = BrainRouter(provider_name="gemini")

    assert router.generate(prompt) == "Mistral reply"

    assert captured["gemini"] == captured["groq"] == captured["mistral"]
    assert PERSONA in captured["gemini"]
    assert 'call yourself "tớ"' in captured["mistral"]


# ======================================================================
# The agent prompt
# ======================================================================

def test_the_agent_complete_message_speaks_in_the_agent_voice():
    """The one place personality is allowed in the agent prompt."""

    context = {
        "device": {"width": 1080, "height": 2400},
        "app": {"package": "com.google.android.youtube", "activity": "MainActivity"},
        "accessibility_tree": {"package": "com.google.android.youtube", "nodes": []},
        "user_request": "open YouTube",
    }

    prompt = PromptBuilder().build(
        history=[],
        user_message=Message(role="user", content="tick"),
        context=context,
    )

    assert "no emoji spam" in prompt
    assert "Vietnamese, casual, Gen-Z" in prompt
    assert "friendly response in Gen-Z style" not in prompt
    assert PERSONA not in prompt              # no conversational sections


# ======================================================================
# Config
# ======================================================================

def test_config_defaults_have_the_persona_section():
    from core.config import DEFAULT_CONFIG

    persona = DEFAULT_CONFIG["personality"]["persona"]

    assert persona["enabled"] is True
    assert persona["pronoun_style"] == ""


def test_chat_engine_builds_the_persona_from_config():
    """A stock ChatEngine - like the launcher's - gets the section."""

    from brain.chat_engine import ChatEngine

    llm = CapturingLLM()

    engine = ChatEngine(memory=FakeStore([]), builder=PromptBuilder(), llm=llm)

    engine.chat("bro cái này lỗi rồi")

    assert PERSONA in llm.prompt
