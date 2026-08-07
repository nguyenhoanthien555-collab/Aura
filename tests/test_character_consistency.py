"""
Character consistency tests.

Section 9: the guard that keeps Aura from drifting into an assistant
register when the conversation runs long. Tests the three mechanisms and
their thresholds:

    identity        restated after the transcript, once it is long enough
    drift           the four modes that happen without needing history
    contradiction   held back until there is enough to contradict

Prompt construction only, so everything here is unit tests over the text
that reaches the builder. No LLM, no generation, no post-processing.
"""

from brain.consistency import (
    CharacterAnchor,
    NullAnchor,
    CONTRADICTION,
    DEFAULT_CONTRADICTION_AFTER,
    DEFAULT_ENGAGE_AFTER,
    DRIFT,
    IDENTITY,
    anchor_of,
    build_anchor,
)


# ----------------------------------------------------------------------
# The anchor
# ----------------------------------------------------------------------


def test_a_short_conversation_costs_nothing():
    """Below the threshold, the section is empty and costs zero tokens."""

    anchor = CharacterAnchor(engage_after=6)

    assert anchor.anchor(messages=0) == ""
    assert anchor.anchor(messages=3) == ""
    assert anchor.anchor(messages=5) == ""


def test_the_guard_appears_once_the_conversation_is_long_enough():

    anchor = CharacterAnchor(engage_after=6)

    text = anchor.anchor(messages=6)

    assert text
    assert IDENTITY in text
    assert DRIFT in text


def test_contradiction_is_held_back_until_there_is_enough_to_contradict():
    """
    A five turn conversation is long enough to drift, but not long enough
    to have committed to things she could walk back.
    """

    anchor = CharacterAnchor(
        engage_after=6,
        contradiction_after=20,
    )

    early = anchor.anchor(messages=10)
    late = anchor.anchor(messages=20)

    assert CONTRADICTION not in early
    assert CONTRADICTION in late


def test_a_disabled_anchor_produces_nothing():

    anchor = CharacterAnchor(enabled=False)

    assert anchor.anchor(messages=100) == ""


def test_the_identity_line_is_overridable():
    """For a very good reason, or a user who wants to experiment."""

    custom = "You are still the same person you were at the start."

    anchor = CharacterAnchor(
        engage_after=0,
        text=custom,
    )

    assert custom in anchor.anchor(messages=10)
    assert IDENTITY not in anchor.anchor(messages=10)


def test_negative_thresholds_are_clamped_to_zero():
    """A bad config is not a crash."""

    anchor = CharacterAnchor(
        engage_after=-5,
        contradiction_after=-10,
    )

    # Engage immediately
    text = anchor.anchor(messages=1)

    assert text
    assert CONTRADICTION in text


def test_the_guard_text_grows_once_not_twice():
    """
    The contradiction clause is added at a threshold, but once it has
    been it stays. A thousand message conversation does not produce a
    longer guard than a twenty one message conversation - it costs one
    section at a fixed size.
    """

    anchor = CharacterAnchor(engage_after=6, contradiction_after=20)

    at_twenty = anchor.anchor(messages=20)
    at_hundred = anchor.anchor(messages=100)

    assert len(at_twenty) == len(at_hundred)


# ----------------------------------------------------------------------
# Construction
# ----------------------------------------------------------------------


def test_build_anchor_reads_the_config():

    config = {
        "enabled": True,
        "after_messages": 10,
        "contradiction_after": 30,
        "anchor": "Custom identity line",
    }

    anchor = build_anchor(config)

    assert isinstance(anchor, CharacterAnchor)
    assert anchor.engage_after == 10
    assert anchor.contradiction_after == 30
    assert "Custom identity line" in anchor.anchor(messages=15)


def test_build_anchor_defaults_to_enabled():

    anchor = build_anchor({})

    assert isinstance(anchor, CharacterAnchor)


def test_disabled_yields_a_null_anchor():
    """
    A NullAnchor rather than a disabled CharacterAnchor, so the off path
    is one attribute lookup and a constant return.
    """

    anchor = build_anchor({"enabled": False})

    assert isinstance(anchor, NullAnchor)
    assert anchor.anchor(messages=100) == ""


def test_malformed_thresholds_fall_back_to_defaults():

    anchor = build_anchor({
        "after_messages": "not a number",
        "contradiction_after": None,
    })

    # Defaults are used rather than raising
    assert anchor.engage_after == DEFAULT_ENGAGE_AFTER
    assert anchor.contradiction_after == DEFAULT_CONTRADICTION_AFTER


# ----------------------------------------------------------------------
# The defensive reader
# ----------------------------------------------------------------------


def test_anchor_of_reads_any_source():
    """
    Structural, like `hint_of`: a user's guard should be a method and
    three lines, not an inheritance obligation.
    """

    class Structural:
        def anchor(self, messages: int) -> str:
            return "structural"

    assert anchor_of(CharacterAnchor(), messages=10)
    assert anchor_of(NullAnchor(), messages=10) == ""
    assert anchor_of(Structural(), messages=10) == "structural"


def test_anchor_of_tolerates_none():

    assert anchor_of(None, messages=10) == ""


def test_anchor_of_tolerates_a_broken_source():
    """A source that raises must cost a prompt section, not the reply."""

    class Broken:
        def anchor(self, messages: int) -> str:
            raise RuntimeError("anchor is broken")

    assert anchor_of(Broken(), messages=10) == ""


def test_anchor_of_tolerates_a_source_with_no_anchor_method():

    assert anchor_of(object(), messages=10) == ""


# ----------------------------------------------------------------------
# Integration: the section flows through the builder
# ----------------------------------------------------------------------


def test_the_section_reaches_the_builder():
    """
    Not a real integration test - no LLM, no conversation - but it
    exercises the path from anchor to builder to prompt string.
    """

    from brain.message import Message
    from brain.prompt_builder import PromptBuilder

    anchor = CharacterAnchor(engage_after=0, contradiction_after=0)

    builder = PromptBuilder()

    history = [
        Message(role="user", content="hello"),
        Message(role="assistant", content="hi"),
    ]

    prompt = builder.build(
        history=history,
        user_message=Message(role="user", content="what's up"),
        identity=anchor.anchor(messages=len(history)),
    )

    # The section header appears, and the guard text is present
    assert "===== WHO YOU ARE =====" in prompt
    assert IDENTITY in prompt
    assert DRIFT in prompt
    assert CONTRADICTION in prompt


def test_an_empty_anchor_is_omitted():
    """
    Below the threshold the section costs nothing, not even a header.
    """

    from brain.message import Message
    from brain.prompt_builder import PromptBuilder

    builder = PromptBuilder()

    prompt = builder.build(
        history=[],
        user_message=Message(role="user", content="hello"),
        identity="",
    )

    assert "===== WHO YOU ARE =====" not in prompt


def test_the_three_instructions_are_ordered_by_permanence():
    """
    HISTORY, then identity, then style, then the user's message.

    The order is the design: a model follows what it read most recently,
    so the least permanent instruction sits closest to the question.
    Identity is more permanent than the style of one reply, so it goes
    first of the two - and both go after the transcript they are there
    to counteract.
    """

    from brain.message import Message
    from brain.prompt_builder import PromptBuilder
    from brain.prompt_sections import HISTORY, IDENTITY as IDENTITY_HEADER
    from brain.prompt_sections import STYLE, USER

    prompt = PromptBuilder().build(
        history=[Message(role="user", content="earlier")],
        user_message=Message(role="user", content="now"),
        identity="Still Aura.",
        style="Reply as Aura.",
    )

    assert (
        prompt.index(HISTORY)
        < prompt.index(IDENTITY_HEADER)
        < prompt.index(STYLE)
        < prompt.index(USER)
    )


# ----------------------------------------------------------------------
# Integration: the anchor reaches the prompt through a whole turn
# ----------------------------------------------------------------------


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


class Record:
    """A memory record: role and content is all the adapter reads."""

    def __init__(self, role, content):
        self.role = role
        self.content = content


class CapturingLLM:
    """Keeps the prompt it was given."""

    def __init__(self):
        self.prompt = ""

    def generate(self, prompt: str) -> str:
        self.prompt = prompt
        return "sure"


def test_a_long_conversation_gets_the_guard():
    """
    The whole point of Section 9, exercised end to end: the same manager,
    the same anchor, two conversation lengths, two different prompts.
    """

    from brain.conversation import ConversationManager
    from brain.prompt_builder import PromptBuilder

    long_history = [
        Record("user" if i % 2 == 0 else "assistant", f"message {i}")
        for i in range(24)
    ]

    llm = CapturingLLM()

    manager = ConversationManager(
        memory=FakeStore(long_history),
        builder=PromptBuilder(),
        llm=llm,
        history_limit=24,
        identity=CharacterAnchor(engage_after=6, contradiction_after=20),
    )

    manager.chat("still there?")

    assert IDENTITY in llm.prompt
    assert DRIFT in llm.prompt
    assert CONTRADICTION in llm.prompt


def test_a_fresh_conversation_does_not():

    from brain.conversation import ConversationManager
    from brain.prompt_builder import PromptBuilder

    llm = CapturingLLM()

    manager = ConversationManager(
        memory=FakeStore([]),
        builder=PromptBuilder(),
        llm=llm,
        identity=CharacterAnchor(engage_after=6, contradiction_after=20),
    )

    manager.chat("hey")

    assert "===== WHO YOU ARE =====" not in llm.prompt


def test_no_anchor_behaves_exactly_as_before():
    """
    Backward compatibility: `identity` defaults to None, and a manager
    built without one produces the prompt it always did.
    """

    from brain.conversation import ConversationManager
    from brain.prompt_builder import PromptBuilder

    llm = CapturingLLM()

    manager = ConversationManager(
        memory=FakeStore([]),
        builder=PromptBuilder(),
        llm=llm,
    )

    manager.chat("hey")

    assert "===== WHO YOU ARE =====" not in llm.prompt
