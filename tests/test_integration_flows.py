"""
The five flows the brief asks for, end to end.

    1. text        ->  brain            ->  Response
    2. microphone   ->  STT   ->  text  ->  brain
    3. brain        ->  Response         ->  TTS
    4. screen       ->  VisionContext    ->  PromptBuilder
    5. event        ->  avatar state

Each subsystem has unit tests of its own. What is checked here is the
wiring between them - the part that unit tests cannot see.

Nothing below touches a microphone, a camera, a speaker, a display, a
model or a network. Every boundary is crossed with a double, which is
also the argument that these subsystems really are decoupled: if the
seams were leaky, doubles would not fit.
"""

import pytest

from brain.chat_engine import ChatEngine
from brain.conversation import ConversationManager
from brain.prompt_builder import PromptBuilder
from brain.prompt_sections import HISTORY, MEMORY, USER, VISION
from brain.response import Response

from events.bus import EventBus
from events.types import (
    AuraState,
    ListeningEvent,
    ResponseEvent,
    SpeakingEvent,
    ThinkingEvent,
    TranscriptEvent,
    UserInputEvent,
    VisionUpdateEvent,
)

from avatar.controller import AvatarController
from avatar.renderer import NullRenderer

from vision.capture import MockWindowReader
from vision.manager import VisionManager

from voice.stt.engine import KeywordWakeWord, SpeechToTextEngine
from voice.stt.microphone import MockMicrophone
from voice.stt.provider import AudioChunk
from voice.stt.providers.mock import MockSTTProvider
from voice.tts.engine import TTSEngine
from voice.tts.providers.mock import MockTTSProvider


# ----------------------------------------------------------------------
# Doubles
# ----------------------------------------------------------------------

class FakeRecord:
    """A stored row as far as the brain is concerned: role and content."""

    def __init__(self, role, content):
        self.role = role
        self.content = content


class FakeStore:
    """In-memory ConversationStore. Newest first, like the real one."""

    def __init__(self):
        self.records = []
        self.saved = []

    def save(self, role, content):
        self.saved.append((role, content))
        self.records.append(FakeRecord(role, content))

    def get_recent(self, limit=10):
        return list(reversed(self.records))[:limit]


class RecordingLLM:
    """Keeps the prompt it was given, so the test can inspect it."""

    def __init__(self, reply="Hey bro, I'm here."):
        self.reply = reply
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.reply

    @property
    def prompt(self):
        return self.prompts[-1]


class FixedKnowledge:
    """A KnowledgeProvider that always remembers the same thing."""

    def __init__(self, lines):
        self.lines = list(lines)
        self.queries = []

    def get_knowledge(self, query):
        self.queries.append(query)
        return list(self.lines)


@pytest.fixture
def bus():
    return EventBus()


def brain_for(store=None, llm=None, **kwargs) -> ConversationManager:
    """A conversation with real building blocks and no I/O underneath."""

    return ConversationManager(
        memory=store if store is not None else FakeStore(),
        builder=PromptBuilder(),
        llm=llm if llm is not None else RecordingLLM(),
        **kwargs,
    )


def vision_for(title: str) -> VisionManager:
    """A vision manager reading a scripted window title, never the desktop."""

    return VisionManager(
        window_reader=MockWindowReader(title=title),
        enabled=True,
    )


def spoken_audio() -> AudioChunk:
    return AudioChunk(data=b"\x01\x02" * 100)


# ----------------------------------------------------------------------
# Flow 1: text -> brain -> response
# ----------------------------------------------------------------------

def test_text_reaches_the_brain_and_a_response_comes_back():
    llm = RecordingLLM(reply="Hey bro.")

    response = brain_for(llm=llm).chat("hello Aura")

    assert isinstance(response, Response)
    assert response.text == "Hey bro."


def test_the_prompt_carries_the_user_message():
    llm = RecordingLLM()

    brain_for(llm=llm).chat("hello Aura")

    assert USER in llm.prompt
    assert "hello Aura" in llm.prompt


def test_both_halves_of_the_turn_are_stored():
    store = FakeStore()

    brain_for(store=store, llm=RecordingLLM(reply="Hey bro.")).chat("hello")

    assert store.saved == [("user", "hello"), ("assistant", "Hey bro.")]


def test_the_second_turn_can_see_the_first():
    store = FakeStore()
    llm = RecordingLLM()

    brain = brain_for(store=store, llm=llm)

    brain.chat("my cat is called Muối")
    brain.chat("what is my cat called")

    assert HISTORY in llm.prompt
    assert "Muối" in llm.prompt


def test_a_turn_announces_itself_on_the_bus(bus):
    seen = []
    bus.subscribe(UserInputEvent, seen.append)
    bus.subscribe(ThinkingEvent, seen.append)
    bus.subscribe(ResponseEvent, seen.append)

    brain_for(events=bus).chat("hello", source="text")

    assert [type(event).__name__ for event in seen] == [
        "UserInputEvent",
        "ThinkingEvent",
        "ResponseEvent",
    ]
    assert seen[0].source == "text"


def test_the_chat_engine_composition_root_produces_the_same_flow():
    """The public entry point, with every dependency injected."""

    store = FakeStore()
    llm = RecordingLLM(reply="Hey bro.")

    engine = ChatEngine(memory=store, builder=PromptBuilder(), llm=llm)

    assert engine.chat("hello").text == "Hey bro."
    assert store.saved[0] == ("user", "hello")


# ----------------------------------------------------------------------
# Flow 2: microphone -> STT -> text -> brain
# ----------------------------------------------------------------------

def test_speech_becomes_a_transcript_and_then_a_reply(bus):
    """
    The whole voice-in path. The microphone is scripted, the recogniser
    is scripted, and the brain is real.
    """

    llm = RecordingLLM(reply="It is late, bro.")
    brain = brain_for(llm=llm, events=bus)

    stt = SpeechToTextEngine(
        provider=MockSTTProvider(transcripts=["what time is it"]),
        microphone=MockMicrophone(chunks=[spoken_audio()]),
        events=bus,
    )

    heard = stt.listen_once()

    assert heard == "what time is it"
    assert brain.chat(heard, source="voice").text == "It is late, bro."
    assert "what time is it" in llm.prompt


def test_continuous_listening_can_drive_the_brain_directly(bus):
    """
    The shape the launcher uses: hand the brain to listen_continuous and
    let the microphone loop feed it.
    """

    llm = RecordingLLM()
    brain = brain_for(llm=llm, events=bus)

    replies = []

    stt = SpeechToTextEngine(
        provider=MockSTTProvider(transcripts=["first thing", "second thing"]),
        microphone=MockMicrophone(),
        events=bus,
    )

    stt.listen_continuous(
        lambda text: replies.append(brain.chat(text, source="voice")),
        max_turns=2,
    )

    assert len(replies) == 2
    assert len(llm.prompts) == 2


def test_only_utterances_addressed_to_aura_reach_the_brain(bus):
    """
    The wake word is a privacy boundary, not a convenience. Anything it
    rejects must never be turned into a prompt.
    """

    llm = RecordingLLM()
    brain = brain_for(llm=llm, events=bus)

    stt = SpeechToTextEngine(
        provider=MockSTTProvider(
            transcripts=["reminder to buy milk", "aura what time is it"]
        ),
        microphone=MockMicrophone(),
        wake_word=KeywordWakeWord(["aura"]),
        events=bus,
    )

    stt.listen_continuous(lambda text: brain.chat(text, source="voice"), max_turns=2)

    assert len(llm.prompts) == 1
    assert "what time is it" in llm.prompt
    assert "buy milk" not in llm.prompt


def test_a_voice_turn_is_marked_as_voice(bus):
    seen = []
    bus.subscribe(UserInputEvent, seen.append)

    stt = SpeechToTextEngine(
        provider=MockSTTProvider(transcripts=["hello"]),
        microphone=MockMicrophone(),
        events=bus,
    )

    brain_for(events=bus).chat(stt.listen_once(), source="voice")

    assert seen[0].source == "voice"


def test_the_transcript_is_visible_to_everyone_not_just_the_brain(bus):
    """A subtitle overlay would subscribe here and need no other change."""

    seen = []
    bus.subscribe(TranscriptEvent, seen.append)

    SpeechToTextEngine(
        provider=MockSTTProvider(transcripts=["hello there"]),
        microphone=MockMicrophone(),
        events=bus,
    ).listen_once()

    assert [event.text for event in seen] == ["hello there"]


def test_a_dead_microphone_does_not_reach_the_brain(bus):
    """Silence is not a prompt."""

    class DeadMicrophone:
        def record(self, seconds):
            raise OSError("no input device")

    llm = RecordingLLM()
    brain = brain_for(llm=llm, events=bus)

    stt = SpeechToTextEngine(
        provider=MockSTTProvider(),
        microphone=DeadMicrophone(),
        events=bus,
    )

    heard = stt.listen_once()

    if heard:
        brain.chat(heard)

    assert heard == ""
    assert llm.prompts == []


# ----------------------------------------------------------------------
# Flow 3: brain -> TTS
# ----------------------------------------------------------------------

def test_a_reply_is_spoken_without_the_brain_knowing_tts_exists(bus):
    """
    The inversion that keeps brain/ free of audio: the brain publishes a
    ResponseEvent, and the voice engine is the one listening.
    """

    provider = MockTTSProvider()
    TTSEngine(provider=provider, events=bus).attach(bus)

    brain_for(llm=RecordingLLM(reply="Hey bro, I'm here."), events=bus).chat("hello")

    assert provider.spoken == ["Hey bro, I'm here."]


def test_speaking_is_announced_around_the_reply(bus):
    seen = []
    bus.subscribe(SpeakingEvent, seen.append)

    TTSEngine(provider=MockTTSProvider(), events=bus).attach(bus)

    brain_for(events=bus).chat("hello")

    assert [event.active for event in seen] == [True, False]


def test_a_brain_with_no_bus_simply_stays_silent():
    """Text-only mode is the default, and needs no configuration."""

    provider = MockTTSProvider()
    TTSEngine(provider=provider)

    brain_for().chat("hello")

    assert provider.spoken == []


def test_detaching_the_voice_stops_the_speaking_without_touching_the_brain(bus):
    provider = MockTTSProvider()
    detach = TTSEngine(provider=provider, events=bus).attach(bus)

    brain = brain_for(events=bus)

    brain.chat("first")
    detach()
    brain.chat("second")

    assert len(provider.spoken) == 1


def test_a_broken_speaker_does_not_break_the_conversation(bus):
    """The reply still returns; it just is not heard."""

    class BrokenProvider:
        def speak(self, text):
            raise RuntimeError("audio device vanished")

    TTSEngine(provider=BrokenProvider(), events=bus).attach(bus)

    response = brain_for(llm=RecordingLLM(reply="still here"), events=bus).chat("hi")

    assert response.text == "still here"


# ----------------------------------------------------------------------
# Flow 4: screen -> VisionContext -> PromptBuilder
# ----------------------------------------------------------------------

def test_what_is_on_screen_reaches_the_prompt():
    llm = RecordingLLM()

    brain = brain_for(
        llm=llm,
        vision=vision_for("main.py - AURA - Visual Studio Code"),
    )

    brain.chat("what am I working on")

    assert VISION in llm.prompt
    assert "[screen]" in llm.prompt
    assert "Python" in llm.prompt


def test_the_observation_becomes_a_vision_context_first():
    """
    The prompt is the last step, not the first. What the brain receives
    is a VisionContext, never a frame or a window handle.
    """

    context = vision_for("bus.ts - events - Visual Studio Code").get_context()

    assert context.source == "screen"
    assert "TypeScript" in context.description


def test_a_blank_screen_adds_no_vision_section():
    llm = RecordingLLM()

    brain_for(llm=llm, vision=vision_for("")).chat("hello")

    assert VISION not in llm.prompt


def test_vision_off_means_no_vision_section():
    llm = RecordingLLM()

    brain_for(llm=llm).chat("hello")

    assert VISION not in llm.prompt


def test_a_screen_change_is_announced_on_the_bus(bus):
    """The avatar or an overlay can follow this without asking vision."""

    seen = []
    bus.subscribe(VisionUpdateEvent, seen.append)

    manager = VisionManager(
        window_reader=MockWindowReader(
            titles=[
                "main.py - AURA - Visual Studio Code",
                "Python docs - Google Chrome",
            ]
        ),
        events=bus,
        enabled=True,
    )

    manager.refresh()
    manager.refresh()

    assert [("editing" in e.description, "browsing" in e.description) for e in seen] == [
        (True, False),
        (False, True),
    ]


def test_broken_vision_costs_the_vision_section_not_the_reply():
    class BrokenVision:
        def get_context(self):
            raise RuntimeError("display server gone")

    llm = RecordingLLM(reply="still fine")

    response = brain_for(llm=llm, vision=BrokenVision()).chat("hello")

    assert response.text == "still fine"
    assert VISION not in llm.prompt


def test_memory_and_vision_can_occupy_the_same_prompt():
    """Two independent context sources, neither aware of the other."""

    llm = RecordingLLM()

    brain_for(
        llm=llm,
        vision=vision_for("main.py - AURA - Visual Studio Code"),
        knowledge=FixedKnowledge(["city: Da Nang"]),
    ).chat("where am I and what am I doing")

    assert MEMORY in llm.prompt
    assert VISION in llm.prompt
    assert "city: Da Nang" in llm.prompt


# ----------------------------------------------------------------------
# Flow 5: event -> avatar state
# ----------------------------------------------------------------------

def test_a_thinking_event_changes_the_avatar_state(bus):
    renderer = NullRenderer()
    AvatarController(renderer=renderer).attach(bus)

    bus.publish(ThinkingEvent())

    assert renderer.state is AuraState.THINKING


def test_a_text_turn_takes_the_avatar_through_thinking_and_back(bus):
    renderer = NullRenderer()
    controller = AvatarController(renderer=renderer)
    controller.attach(bus)

    brain_for(events=bus).chat("hello")

    assert renderer.states == [AuraState.THINKING, AuraState.IDLE]
    assert controller.state is AuraState.IDLE


def test_listening_shows_on_the_avatar_before_a_word_is_recognised(bus):
    renderer = NullRenderer()
    AvatarController(renderer=renderer).attach(bus)

    SpeechToTextEngine(
        provider=MockSTTProvider(transcripts=["hello"]),
        microphone=MockMicrophone(),
        events=bus,
    ).listen_once()

    assert renderer.states == [AuraState.LISTENING, AuraState.IDLE]


def test_the_avatar_never_sees_what_was_said(bus):
    """
    It reacts to the fact of a reply, not to its content. Everything the
    renderer receives is an AuraState - never text. This is the "no AI
    logic in the avatar" rule, stated as a test.
    """

    renderer = NullRenderer()
    controller = AvatarController(renderer=renderer)
    controller.attach(bus)

    bus.publish(ThinkingEvent())
    bus.publish(ResponseEvent(text="something extremely specific"))

    assert renderer.states == [AuraState.THINKING, AuraState.IDLE]
    assert all(isinstance(state, AuraState) for state in renderer.states)
    assert controller.state is AuraState.IDLE


# ----------------------------------------------------------------------
# One turn through everything at once
# ----------------------------------------------------------------------

def test_one_spoken_turn_drives_every_subsystem(bus):
    """
    Microphone to avatar, in one turn:

        mic -> STT -> brain -> Response -> TTS
                            \\-> events -> avatar

    Subscription order here matches launcher.services.build_services,
    where TTS attaches before the avatar. That order decides whether
    SPEAKING is observed before or after the IDLE that follows the
    reply, so the assertions below are deliberately about the shape of
    the sequence rather than its exact interleaving.
    """

    provider = MockTTSProvider()
    TTSEngine(provider=provider, events=bus).attach(bus)

    renderer = NullRenderer()
    AvatarController(renderer=renderer).attach(bus)

    llm = RecordingLLM(reply="Half past nine, bro.")
    store = FakeStore()

    brain = brain_for(
        store=store,
        llm=llm,
        events=bus,
        vision=vision_for("main.py - AURA - Visual Studio Code"),
        knowledge=FixedKnowledge(["city: Da Nang"]),
    )

    stt = SpeechToTextEngine(
        provider=MockSTTProvider(transcripts=["what time is it"]),
        microphone=MockMicrophone(chunks=[spoken_audio()]),
        events=bus,
    )

    response = brain.chat(stt.listen_once(), source="voice")

    # The reply came back...
    assert response.text == "Half past nine, bro."

    # ...the prompt saw memory and the screen...
    assert MEMORY in llm.prompt
    assert VISION in llm.prompt

    # ...it was persisted...
    assert store.saved == [
        ("user", "what time is it"),
        ("assistant", "Half past nine, bro."),
    ]

    # ...it was spoken...
    assert provider.spoken == ["Half past nine, bro."]

    # ...and the avatar followed the whole thing without being told to.
    states = renderer.states
    assert states[0] is AuraState.LISTENING
    assert AuraState.THINKING in states
    assert AuraState.SPEAKING in states
    assert states[-1] is AuraState.IDLE


def test_the_brain_still_works_with_every_optional_subsystem_absent():
    """
    The floor this whole sprint was built against: no bus, no voice, no
    vision, no memory beyond the transcript. Sprint 4, unchanged.
    """

    store = FakeStore()
    llm = RecordingLLM(reply="Hey bro.")

    response = ConversationManager(
        memory=store,
        builder=PromptBuilder(),
        llm=llm,
    ).chat("hello")

    assert response.text == "Hey bro."
    assert VISION not in llm.prompt
    assert MEMORY not in llm.prompt


def test_an_llm_failure_is_announced_and_raised_not_swallowed(bus):
    """
    A provider outage must be visible. An empty reply that looks like a
    real one is worse than an error.
    """

    class DeadLLM:
        def generate(self, prompt):
            raise RuntimeError("provider unreachable")

    store = FakeStore()
    provider = MockTTSProvider()
    TTSEngine(provider=provider, events=bus).attach(bus)

    brain = brain_for(store=store, llm=DeadLLM(), events=bus)

    with pytest.raises(RuntimeError):
        brain.chat("hello")

    # Nothing was stored, and nothing was spoken.
    assert store.saved == []
    assert provider.spoken == []
