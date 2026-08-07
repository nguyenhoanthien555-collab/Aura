"""
Personality, style, configuration and the roadmap interfaces.

The rule this file mostly exists to defend is the one from the brief:

    style may change tone, wording and friendliness. It may not change
    technical accuracy.

So several tests below assert that a technical sentence comes out of the
style layer byte-identical. If someone later teaches it to paraphrase,
those are the tests that should stop them.
"""

import pytest

from brain.conversation import ConversationManager
from brain.message import Message
from brain.mood import MoodTracker, parse_mood
from brain.personality import Personality
from brain.prompt_builder import PromptBuilder
from brain.prompt_sections import HISTORY, PERSONALITY, STYLE, USER
from brain.style import (
    DEFAULT_HINT,
    AuraStyle,
    NullStyler,
    ResponseStyler,
    build_styler,
    hint_of,
)

from core.config import DEFAULT_CONFIG, deep_merge, load_config

from events.bus import EventBus
from events.types import Mood, MoodChangedEvent, SpeakingEvent, ThinkingEvent

from avatar.controller import AvatarController
from avatar.renderer import NullRenderer
from avatar.state import AvatarStateMachine

from memory.companion import (
    CodingStyle,
    CompanionMemory,
    Highlight,
    InMemoryCodingStyle,
    InMemoryHighlights,
    InMemoryPreferences,
    InMemoryProjects,
    Preference,
    Project,
)


# ======================================================================
# Configuration
# ======================================================================

def test_the_voice_settings_the_brief_asked_for_are_all_there():
    """enabled / provider / voice / rate / pitch, none of them hardcoded."""

    tts = DEFAULT_CONFIG["voice"]["tts"]

    assert set(tts) >= {"enabled", "provider", "voice", "rate", "pitch"}


def test_the_shipped_voice_is_slightly_faster_and_slightly_higher():
    tts = DEFAULT_CONFIG["voice"]["tts"]

    assert tts["rate"] == "+5%"
    assert tts["pitch"] == "+10Hz"


def test_speech_is_off_until_asked_for():
    """
    A companion that starts talking out loud on first run is a surprise,
    not a feature. Same reason vision and tools ship off.
    """

    assert DEFAULT_CONFIG["voice"]["tts"]["enabled"] is False


def test_auto_is_the_default_provider_not_edge():
    """Edge needs a network round trip, so it is opt in by name."""

    assert DEFAULT_CONFIG["voice"]["tts"]["provider"] == "auto"


def test_the_style_layer_ships_on():
    style = DEFAULT_CONFIG["personality"]["style"]

    assert style["enabled"] is True
    assert style["strip_filler"] is True
    assert style["hint"] == ""


def test_a_sprint_4_config_still_loads_and_gains_the_new_sections():
    """
    The reason load_config deep merges: an old config.yaml written before
    any of this existed must keep working and pick up the new defaults.
    """

    old = {"llm": {"provider": "gemini"}, "memory": {"history_limit": 5}}

    merged = deep_merge(DEFAULT_CONFIG, old)

    assert merged["llm"]["provider"] == "gemini"
    assert merged["memory"]["history_limit"] == 5

    assert merged["voice"]["tts"]["pitch"] == "+10Hz"
    assert merged["personality"]["style"]["enabled"] is True

    # Untouched keys inside an overridden section survive.
    assert merged["llm"]["temperature"] == DEFAULT_CONFIG["llm"]["temperature"]


def test_what_the_user_wrote_wins():
    merged = deep_merge(
        DEFAULT_CONFIG,
        {"voice": {"tts": {"provider": "edge", "pitch": "+25Hz"}}},
    )

    assert merged["voice"]["tts"]["provider"] == "edge"
    assert merged["voice"]["tts"]["pitch"] == "+25Hz"

    # and the keys they did not write are still there
    assert merged["voice"]["tts"]["rate"] == "+5%"


def test_a_written_list_is_taken_literally():
    """
    Unioning a user's list with a default would grant permissions they
    never wrote down. It matters most for tools.allowed.
    """

    merged = deep_merge(DEFAULT_CONFIG, {"tools": {"auto_approve": []}})

    assert merged["tools"]["auto_approve"] == []


def test_loading_a_real_file_fills_in_everything_it_omits(tmp_path, monkeypatch):

    path = tmp_path / "config.yaml"
    path.write_text(
        "voice:\n  tts:\n    enabled: true\n    provider: edge\n",
        encoding="utf-8",
    )

    monkeypatch.setattr("core.config.CONFIG_PATH", path)

    config = load_config()

    assert config["voice"]["tts"]["enabled"] is True
    assert config["voice"]["tts"]["provider"] == "edge"
    assert config["voice"]["tts"]["voice"] == ""
    assert config["voice"]["tts"]["pitch"] == "+10Hz"


def test_a_broken_config_file_does_not_stop_aura_starting(tmp_path, monkeypatch):
    """Refusing to start over a stray tab in YAML helps nobody."""

    path = tmp_path / "config.yaml"

    # A literal tab is illegal as YAML indentation, so this is
    # unambiguously unparseable rather than merely surprising.
    path.write_text("voice:\n\ttts:\n\t\tenabled: true\n", encoding="utf-8")

    monkeypatch.setattr("core.config.CONFIG_PATH", path)

    config = load_config()

    assert config["voice"]["tts"]["pitch"] == "+10Hz"
    assert config["voice"]["tts"]["enabled"] is False


def test_a_config_file_holding_nothing_is_treated_as_empty(tmp_path, monkeypatch):
    """An empty file parses to None, which is not a dict."""

    path = tmp_path / "config.yaml"
    path.write_text("", encoding="utf-8")

    monkeypatch.setattr("core.config.CONFIG_PATH", path)

    assert load_config()["app"]["name"] == "Aura"


def test_a_missing_config_file_is_written(tmp_path, monkeypatch):

    path = tmp_path / "config.yaml"

    monkeypatch.setattr("core.config.CONFIG_PATH", path)

    config = load_config()

    assert path.exists()
    assert config["app"]["name"] == "Aura"


# ======================================================================
# Personality
# ======================================================================

@pytest.fixture
def personality_text() -> str:
    return Personality().load()


def test_the_personality_file_loads(personality_text):
    assert personality_text.strip()


def test_she_is_still_aura(personality_text):
    """Identity is fixed: name, gender, role."""

    assert "Aura" in personality_text
    assert "Female" in personality_text
    assert "Local AI Companion" in personality_text


@pytest.mark.parametrize(
    "trait",
    ["Calm", "Curious", "Playful", "Supportive", "teasing", "Cozy"],
)
def test_every_trait_the_brief_listed_is_present(personality_text, trait):
    assert trait in personality_text


@pytest.mark.parametrize(
    "interest",
    ["programming", "AI", "Minecraft modding", "game development"],
)
def test_what_she_is_into(personality_text, interest):
    assert interest in personality_text


def test_the_brief_s_own_examples_are_in_there(personality_text):
    """Verbatim, because they define the target voice better than a rule."""

    assert "Bro that idea is actually pretty sick. Let's cook." in personality_text
    assert "Oops, my bad bro, something went sideways." in personality_text


@pytest.mark.parametrize(
    "banned",
    [
        "I understand your request",
        "I apologize for the inconvenience.",
        "As an AI language model",
        "Is there anything else I can help you with?",
    ],
)
def test_the_corporate_phrases_are_named_and_banned(personality_text, banned):
    """
    Naming them is the point. A model follows "never say X" far better
    than "avoid corporate tone".
    """

    assert banned in personality_text


def test_honesty_survives_the_new_tone(personality_text):
    """
    The one section that does not bend for style. Casual is a voice;
    it is not permission to make things up.
    """

    lowered = personality_text.lower()

    assert "never pretend to know" in lowered
    assert "unsure" in lowered
    assert "never invent" in lowered


def test_she_is_told_to_be_concise(personality_text):
    assert "Concise by default" in personality_text


def test_bro_is_allowed_but_not_compulsory(personality_text):
    assert "bro" in personality_text
    assert "not as punctuation on every line" in personality_text


def test_the_personality_reaches_the_prompt():
    """
    Loading the file is only half of it - it has to arrive under its own
    header, where the model will read it as instructions.
    """

    prompt = PromptBuilder().build(
        history=[],
        user_message=Message(role="user", content="hey"),
    )

    assert PERSONALITY in prompt
    assert "Local AI Companion" in prompt
    assert prompt.index(PERSONALITY) < prompt.index(USER)


# ======================================================================
# Style: what it removes
# ======================================================================

@pytest.fixture
def styler() -> AuraStyle:
    return AuraStyle()


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Certainly! Here is the fix.", "Here is the fix."),
        ("Of course, the bug is on line 12.", "The bug is on line 12."),
        (
            "I apologize for the inconvenience. The build failed.",
            "The build failed.",
        ),
        (
            "As an AI language model, I can read this file.",
            "I can read this file.",
        ),
    ],
)
def test_opening_filler_is_removed(styler, text, expected):
    assert styler.style(text) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "The import is circular. I hope this helps!",
            "The import is circular.",
        ),
        (
            "Fixed it. Is there anything else I can help you with?",
            "Fixed it.",
        ),
        (
            "Done. Let me know if you need anything else.",
            "Done.",
        ),
    ],
)
def test_closing_filler_is_removed(styler, text, expected):
    assert styler.style(text) == expected


def test_filler_at_both_ends_goes_at_once(styler):
    result = styler.style("Certainly! The path is wrong. I hope this helps.")

    assert result == "The path is wrong."


def test_stacked_openers_are_all_removed(styler):
    assert styler.style("Certainly! Of course, it works.") == "It works."


# ======================================================================
# Style: what it must never touch
#
# These are the important ones. Every assertion here is byte equality
# against the input, because the layer is not allowed to change facts.
# ======================================================================

@pytest.mark.parametrize(
    "text",
    [
        "The error happens because the dependency injection failed.",
        "Traceback (most recent call last):\n  File \"main.py\", line 3",
        "Run pip install edge-tts to fix it.",
        "The value is 0.95, not 0.9.",
        "AttributeError: 'NoneType' object has no attribute 'speak'",
        "It fails on Python 3.11 but not 3.12.",
        "C:\\Users\\Hoan Thien\\.claude is the config directory.",
    ],
)
def test_technical_text_comes_out_exactly_as_it_went_in(styler, text):
    assert styler.style(text) == text


def test_a_fenced_code_block_is_untouched(styler):
    """
    Even when it contains something that looks exactly like filler. Code
    is held out of the filter entirely rather than filtered carefully.
    """

    text = (
        "Certainly! Here:\n\n"
        "```python\n"
        "print('I hope this helps')\n"
        "```"
    )

    result = styler.style(text)

    assert "print('I hope this helps')" in result
    assert result.startswith("Here:")


def test_an_inline_span_is_untouched(styler):
    text = "Certainly! Call `of course()` on the builder."

    assert styler.style(text) == "Call `of course()` on the builder."


def test_an_identifier_does_not_get_recapitalised(styler):
    """
    Deleting "Certainly, " leaves a lowercase word. Capitalising prose is
    cosmetic; capitalising an identifier changes what it means.
    """

    assert styler.style("Certainly, assertEquals is deprecated.") == (
        "assertEquals is deprecated."
    )


def test_a_real_sentence_that_starts_like_filler_survives(styler):
    """
    "I would be happy to help" is filler on its own and a real clause
    here, which is why only a whole first clause counts.
    """

    text = "I would be happy to help once you paste the traceback."

    assert styler.style(text) == text


def test_a_reply_that_was_entirely_filler_is_not_deleted(styler):
    """
    Silence would hide the problem. Seeing the filler is more useful.
    """

    assert styler.style("Certainly!") == "Certainly!"


def test_empty_input_stays_empty(styler):
    assert styler.style("") == ""
    assert styler.style("   ") == "   "


# ======================================================================
# Style: configuration and wiring
# ======================================================================

def test_disabled_means_untouched():
    text = "Certainly! I hope this helps."

    assert AuraStyle(enabled=False).style(text) == text
    assert AuraStyle(strip_filler=False).style(text) == text


def test_the_prompt_hint_is_what_actually_rewrites_the_tone(styler):
    """
    The filter only deletes. Turning "the dependency injection failed"
    into "it is basically tripping over itself" is a paraphrase, and the
    only thing qualified to do that safely is the model - so it is asked
    in the prompt.
    """

    hint = styler.prompt_hint()

    assert hint == DEFAULT_HINT
    assert "Aura" in hint
    assert "never the facts" in hint


def test_a_custom_hint_replaces_the_default():
    assert AuraStyle(hint="Talk like a pirate.").prompt_hint() == (
        "Talk like a pirate."
    )


def test_a_disabled_styler_asks_for_nothing():
    assert AuraStyle(enabled=False).prompt_hint() == ""


def test_build_styler_reads_the_config_section():
    styler = build_styler(DEFAULT_CONFIG["personality"]["style"])

    assert isinstance(styler, AuraStyle)
    assert styler.enabled is True
    assert styler.strip_filler is True


def test_turning_it_off_in_config_yields_a_no_op():
    styler = build_styler({"enabled": False})

    assert isinstance(styler, NullStyler)
    assert styler.style("Certainly! Hi.") == "Certainly! Hi."


def test_an_absent_config_section_still_gives_aura_her_voice():
    assert isinstance(build_styler(None), AuraStyle)
    assert isinstance(build_styler({}), AuraStyle)


def test_both_stylers_satisfy_the_protocol():
    assert isinstance(AuraStyle(), ResponseStyler)
    assert isinstance(NullStyler(), ResponseStyler)


def test_hint_of_tolerates_a_styler_without_one():
    """`prompt_hint` is optional - a three line custom styler is valid."""

    class Minimal:
        def style(self, text):
            return text

    assert hint_of(Minimal()) == ""
    assert hint_of(None) == ""
    assert hint_of(AuraStyle()) == DEFAULT_HINT


def test_hint_of_survives_a_styler_that_raises():

    class Broken:
        def style(self, text):
            return text

        def prompt_hint(self):
            raise RuntimeError("nope")

    assert hint_of(Broken()) == ""


def test_the_style_section_sits_next_to_the_user_message():
    """
    Last, on purpose: a model follows the instruction it read most
    recently far more reliably than one from the top of a long prompt.
    """

    prompt = PromptBuilder().build(
        history=[],
        user_message=Message(role="user", content="why is it slow"),
        style="Reply as Aura.",
    )

    assert HISTORY in prompt
    assert prompt.index(HISTORY) < prompt.index(STYLE) < prompt.index(USER)


def test_no_styler_costs_no_tokens():
    prompt = PromptBuilder().build(
        history=[],
        user_message=Message(role="user", content="hey"),
    )

    assert STYLE not in prompt


# ======================================================================
# Style: through a whole turn
# ======================================================================

class FakeStore:
    """Enough of ConversationStore to run one turn."""

    def __init__(self):
        self.saved: list[tuple[str, str]] = []

    def save(self, role, content):
        self.saved.append((role, content))

    def get_recent(self, limit):
        return []


class FakeLLM:

    def __init__(self, reply="Certainly! The fix is on line 12."):
        self.reply = reply
        self.prompts: list[str] = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.reply


def manager_with(style, llm=None, store=None):
    return ConversationManager(
        memory=store or FakeStore(),
        builder=PromptBuilder(),
        llm=llm or FakeLLM(),
        style=style,
    )


def test_the_reply_is_styled_before_it_leaves_the_brain():
    manager = manager_with(AuraStyle())

    assert manager.chat("why").text == "The fix is on line 12."


def test_the_styled_text_is_what_gets_remembered():
    """
    Otherwise the next turn's history would be full of the filler that
    was just removed, and the model would copy it back.
    """

    store = FakeStore()

    manager_with(AuraStyle(), store=store).chat("why")

    assert store.saved[-1] == ("assistant", "The fix is on line 12.")


def test_the_hint_reaches_the_prompt():
    llm = FakeLLM()

    manager_with(AuraStyle(), llm=llm).chat("why")

    assert STYLE in llm.prompts[0]
    assert DEFAULT_HINT in llm.prompts[0]


def test_no_styler_behaves_exactly_as_sprint_4_did():
    """The whole layer is optional. Absent, nothing changes."""

    manager = manager_with(None)

    assert manager.chat("why").text == "Certainly! The fix is on line 12."


def test_a_broken_styler_costs_the_polish_not_the_answer():

    class Broken:
        def style(self, text):
            raise RuntimeError("regex exploded")

    manager = manager_with(Broken())

    assert manager.chat("why").text == "Certainly! The fix is on line 12."


# ======================================================================
# Mood
# ======================================================================

def test_she_starts_neutral():
    assert MoodTracker().mood is Mood.NEUTRAL


def test_setting_a_mood_announces_it():
    bus = EventBus()
    seen = []
    bus.subscribe(MoodChangedEvent, seen.append)

    tracker = MoodTracker(events=bus)

    assert tracker.set(Mood.TEASING, reason="he pushed to main again") is True
    assert tracker.mood is Mood.TEASING

    assert seen[0].mood is Mood.TEASING
    assert seen[0].reason == "he pushed to main again"


def test_setting_the_same_mood_twice_announces_nothing():
    """A subscriber should be able to treat every event as a real change."""

    bus = EventBus()
    seen = []
    bus.subscribe(MoodChangedEvent, seen.append)

    tracker = MoodTracker(events=bus)
    tracker.set(Mood.HAPPY)

    assert tracker.set(Mood.HAPPY) is False
    assert len(seen) == 1


def test_a_value_that_is_not_a_mood_is_refused():
    """So `event.mood` is always a Mood, for everyone downstream."""

    tracker = MoodTracker()

    assert tracker.set("happy") is False
    assert tracker.set(None) is False
    assert tracker.mood is Mood.NEUTRAL


def test_reset_goes_back_to_neutral():
    tracker = MoodTracker(mood=Mood.SLEEPY)

    assert tracker.reset() is True
    assert tracker.mood is Mood.NEUTRAL


def test_nothing_infers_a_mood():
    """
    No sentiment analysis, no classifier, no reading of the user's tone.
    A whole conversation goes past and she stays neutral until something
    explicitly says otherwise.
    """

    bus = EventBus()
    tracker = MoodTracker(events=bus)

    for event in (ThinkingEvent(), SpeakingEvent(text="hey", active=True)):
        bus.publish(event)

    assert tracker.mood is Mood.NEUTRAL


@pytest.mark.parametrize(
    "name, expected",
    [
        ("happy", Mood.HAPPY),
        ("CURIOUS", Mood.CURIOUS),
        ("  focused  ", Mood.FOCUSED),
        ("teasing", Mood.TEASING),
        ("sleepy", Mood.SLEEPY),
    ],
)
def test_a_mood_can_be_read_from_a_string(name, expected):
    assert parse_mood(name) is expected


def test_an_unknown_mood_name_falls_back():
    """A typo in a config file is not worth a crash."""

    assert parse_mood("hangry") is Mood.NEUTRAL
    assert parse_mood("") is Mood.NEUTRAL
    assert parse_mood("hangry", fallback=Mood.HAPPY) is Mood.HAPPY


def test_every_mood_the_brief_named_exists():
    names = {mood.value for mood in Mood}

    assert {"happy", "curious", "focused", "teasing", "sleepy"} <= names


# ======================================================================
# Avatar preparation
# ======================================================================

def test_the_avatar_follows_a_mood_off_the_bus():
    bus = EventBus()
    machine = AvatarStateMachine()
    machine.attach(bus)

    bus.publish(MoodChangedEvent(mood=Mood.CURIOUS))

    assert machine.mood is Mood.CURIOUS


def test_mood_and_state_move_independently():
    """
    One picks the animation, the other picks the expression. She can be
    thinking and curious at the same time.
    """

    bus = EventBus()
    machine = AvatarStateMachine()
    machine.attach(bus)

    bus.publish(MoodChangedEvent(mood=Mood.CURIOUS))
    bus.publish(ThinkingEvent())

    assert machine.mood is Mood.CURIOUS
    assert machine.state.value == "thinking"


def test_a_renderer_with_expressions_is_told_about_them():
    bus = EventBus()
    renderer = NullRenderer()

    controller = AvatarController(renderer=renderer)
    controller.attach(bus)

    bus.publish(MoodChangedEvent(mood=Mood.TEASING))

    assert renderer.mood is Mood.TEASING
    assert controller.mood is Mood.TEASING


def test_a_renderer_without_expressions_is_simply_skipped():
    """
    `set_mood` is optional by absence rather than by a base class full of
    `pass`. A sprite renderer with four PNGs has no use for it.
    """

    class SpritesOnly:
        def __init__(self):
            self.states = []

        def set_state(self, state):
            self.states.append(state)

        def show(self):
            pass

        def hide(self):
            pass

        def close(self):
            pass

    bus = EventBus()
    renderer = SpritesOnly()

    AvatarController(renderer=renderer).attach(bus)

    bus.publish(MoodChangedEvent(mood=Mood.HAPPY))       # no crash
    bus.publish(ThinkingEvent())

    assert renderer.states


def test_the_speaking_event_says_enough_to_animate_with():
    """
    An avatar needs to know it started, what is being said, in whose
    voice, and for how long. `duration` of 0.0 means unknown - animate
    until the stop event arrives.
    """

    event = SpeakingEvent(text="hey bro", active=True, voice="en-US-AvaMultilingualNeural")

    assert event.text == "hey bro"
    assert event.active is True
    assert event.voice == "en-US-AvaMultilingualNeural"
    assert event.duration == 0.0


def test_the_older_speaking_event_constructor_still_works():
    """Fields were appended with defaults, so nothing existing breaks."""

    event = SpeakingEvent(text="hey", active=False)

    assert event.voice == ""
    assert event.duration == 0.0


# ======================================================================
# Companion memory
# ======================================================================

def test_a_preference_is_remembered_and_rendered():
    store = InMemoryPreferences()
    store.remember("editor", "VS Code", note="with vim keys")

    assert store.all()[0].render() == "editor: VS Code (with vim keys)"


def test_the_newest_preference_wins():
    """Two contradictory preferences are worse than one stale one."""

    store = InMemoryPreferences()
    store.remember("editor", "VS Code")
    store.remember("Editor", "Neovim")

    assert len(store) == 1
    assert store.all()[0].value == "Neovim"


def test_an_empty_preference_is_not_stored():
    store = InMemoryPreferences()
    store.remember("", "Neovim")
    store.remember("editor", "  ")

    assert len(store) == 0


def test_only_active_projects_are_offered():
    store = InMemoryProjects()
    store.record(Project(name="Aura", description="local AI companion"))
    store.record(Project(name="Old Mod", status="done"))

    assert [p.name for p in store.active()] == ["Aura"]
    assert len(store.all()) == 2


def test_a_coding_style_renders_one_line_per_convention():
    style = CodingStyle(
        language="Python",
        conventions=("comments explain why, not what", "no bare except"),
    )

    assert style.render() == [
        "Python: comments explain why, not what",
        "Python: no bare except",
    ]


def test_a_style_can_be_looked_up_by_language():
    store = InMemoryCodingStyle()
    store.learn(CodingStyle(language="Python", conventions=("no bare except",)))

    assert store.for_language("python") is not None
    assert store.for_language("rust") is None


def test_highlights_come_back_newest_first():
    store = InMemoryHighlights()

    for summary in ("first", "second", "third"):
        store.keep(Highlight(summary=summary))

    assert [h.summary for h in store.recent(2)] == ["third", "second"]


def test_highlights_do_not_grow_without_bound():
    store = InMemoryHighlights(cap=3)

    for index in range(10):
        store.keep(Highlight(summary=f"note {index}"))

    assert len(store) == 3
    assert store.recent(1)[0].summary == "note 9"


def test_companion_memory_speaks_the_language_the_brain_reads():
    """
    Rendered strings, same as the rest of memory/, so brain/ never learns
    what a Project is.
    """

    memory = CompanionMemory()
    memory.preferences.remember("editor", "Neovim")
    memory.projects.record(Project(name="Aura", description="AI companion"))
    memory.coding_style.learn(
        CodingStyle(language="Python", conventions=("no bare except",))
    )

    lines = memory.get_knowledge("what am I building")

    assert "prefers editor: Neovim" in lines
    assert "project - Aura - AI companion" in lines
    assert "code style - Python: no bare except" in lines
    assert all(isinstance(line, str) for line in lines)


def test_an_empty_companion_memory_says_nothing():
    assert CompanionMemory().get_knowledge("anything") == []


def test_it_is_capped_so_it_cannot_eat_the_prompt():
    memory = CompanionMemory(max_lines=2)

    for index in range(10):
        memory.preferences.remember(f"topic {index}", "yes")

    assert len(memory.get_knowledge("x")) == 2


def test_one_broken_store_costs_its_own_lines_only():

    class BrokenProjects:
        def record(self, project):
            pass

        def active(self, limit=None):
            raise RuntimeError("the future database is down")

    memory = CompanionMemory(projects=BrokenProjects())
    memory.preferences.remember("editor", "Neovim")

    assert memory.get_knowledge("x") == ["prefers editor: Neovim"]


def test_there_is_no_database_yet():
    """
    Deliberate. Committing to a schema before knowing how these are read
    is how a migration gets written twice. When these gain a real backing
    store they gain it behind the Protocols, and nothing reading them
    changes.
    """

    from pathlib import Path

    import memory.companion as companion

    source = Path(companion.__file__).read_text(encoding="utf-8").lower()

    assert "sqlalchemy" not in source
    assert "create_engine" not in source


def test_it_can_be_handed_straight_to_the_brain():
    """
    Same shape as MemoryKnowledgeProvider, so the day it has something
    worth saying it is one constructor argument.
    """

    memory = CompanionMemory()
    memory.preferences.remember("editor", "Neovim")

    llm = FakeLLM()

    ConversationManager(
        memory=FakeStore(),
        builder=PromptBuilder(),
        llm=llm,
        knowledge=memory,
    ).chat("what editor do I use")

    assert "prefers editor: Neovim" in llm.prompts[0]


def test_the_records_are_frozen():
    """A remembered fact should not be edited in place by a caller."""

    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        Preference(topic="editor", value="Neovim").topic = "changed"

    with pytest.raises(FrozenInstanceError):
        Project(name="Aura").status = "done"
