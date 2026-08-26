"""
What `python main.py` actually builds.

`core/app.py::Aura` is the application composition root for the text
harness, and it is the one entry point in the codebase that had no test
at all. That matters more than the line count suggests: `ChatEngine`
deliberately defaults `clock` to None so that a bare engine stays
byte-for-byte the Sprint 4 prompt pipeline, which means every collaborator
Aura is supposed to have arrives from a composition root or not at all.
A root that quietly skips one produces a working conversation with a
missing faculty, and nothing downstream complains.

Section 16: "Never rely on the model guessing the current time." A prompt
with no TIME section is exactly that reliance, and the model will guess
confidently.
"""

from datetime import datetime

from brain.prompt_sections import TIME
from core.app import Aura


class RecordingLLM:
    """Keeps the prompt it was given, so the test can inspect it."""

    def __init__(self, reply="ok"):
        self.reply = reply
        self.prompts: list[str] = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.reply

    @property
    def prompt(self) -> str:
        return self.prompts[-1]


def real_aura() -> tuple[Aura, RecordingLLM]:
    """
    A default-built Aura with only its model replaced.

    Everything else is the production graph, because the wiring is the
    thing under test - injecting an engine would test the injection.
    The model is swapped after construction rather than before, so no
    test here reaches the network.
    """

    aura = Aura()
    llm = RecordingLLM()
    aura.engine.conversation.llm = llm

    return aura, llm


def test_a_default_conversation_knows_what_time_it_is():

    aura, llm = real_aura()

    aura.chat("hi")

    assert TIME in llm.prompt


def test_the_time_it_reports_is_now_and_not_a_literal():
    """
    The date in the prompt has to come from the clock rather than from a
    string somewhere in the prompt layer. Written as a range around the
    real present, which is the only assertion that a hardcoded date -
    today's included - cannot pass tomorrow.
    """

    aura, llm = real_aura()

    aura.chat("hi")

    today = datetime.now().strftime("%d")
    year = datetime.now().strftime("%Y")

    section = llm.prompt.split(TIME, 1)[1]

    assert year in section
    assert today in section


def test_the_clock_is_the_one_the_config_asked_for():
    """
    Built from the config the root already loaded, not from a second
    `load_config()` call and not from a default constructed in the brain.
    `temporal.timezone` is owner-settable, so a root that ignored it
    would render the setting dead.
    """

    from core.temporal import TemporalClock

    aura = Aura()

    clock = aura.engine.conversation.clock

    assert clock is not None

    # Compared against what the section itself produces rather than
    # against "" - an unresolvable name degrades to "" in the constructor,
    # so asserting the empty string would pass for a root that ignored
    # the config entirely.
    assert clock.timezone_name == TemporalClock.from_config(
        aura.config
    ).timezone_name
