"""
The companion decision pipeline.

Everything here is about one question: when does Aura speak without being
spoken to? The bias under test is silence - almost every case below
asserts that she stays quiet, because that is the behaviour that has to
survive refactoring.
"""

import json

import pytest

from companion.decision import CompanionDecision, Priority
from companion.detector import ChangeDetector, similarity
from companion.engine import CompanionEngine, build_companion_engine
from companion.evaluator import (
    HeuristicEvaluator,
    LLMRelevanceEvaluator,
    Relevance,
    looks_sensitive,
)
from companion.policy import CompanionPolicy, PolicySettings
from events.bus import EventBus
from events.types import CompanionNotificationEvent
from vision.remote import ScreenObservation


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class ScriptedLLM:
    """An LLM port that returns whatever the test wrote."""

    def __init__(self, reply: str = "", error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls = 0

    def generate(self, prompt: str, **kwargs) -> str:
        self.calls += 1
        if self.error:
            raise self.error
        return self.reply


def relevant_reply(message: str = "Your build failed.", confidence: float = 0.9) -> str:
    return json.dumps({
        "relevant": True,
        "confidence": confidence,
        "reason": "build failure",
        "message": message,
    })


def observation(**overrides) -> ScreenObservation:
    fields = {
        "application": "Gmail",
        "package": "com.google.android.gm",
        "screen_text": "Inbox 3 unread",
        "device_id": "phone-1",
    }
    fields.update(overrides)
    return ScreenObservation(**fields)


def enabled_settings(**overrides) -> PolicySettings:
    fields = {
        "enabled": True,
        "relevance_threshold": 0.7,
        "cooldown_seconds": 300.0,
        "max_per_hour": 6,
        "suppress_after_chat_seconds": 120.0,
    }
    fields.update(overrides)
    return PolicySettings(**fields)


# ----------------------------------------------------------------------
# Change detection
# ----------------------------------------------------------------------

class TestSimilarity:

    def test_identical_text_is_identical(self):
        assert similarity("hello world", "hello world") == 1.0

    def test_two_empty_strings_are_identical(self):
        assert similarity("", "") == 1.0

    def test_empty_against_content_is_completely_different(self):
        assert similarity("", "hello") == 0.0

    def test_word_order_does_not_matter(self):
        assert similarity("a b c", "c b a") == 1.0

    def test_partial_overlap_scores_between(self):
        score = similarity("a b c d", "a b c e")

        assert 0.0 < score < 1.0


class TestChangeDetector:

    def test_the_first_observation_is_always_a_change(self):
        change = ChangeDetector().check(observation())

        assert change.changed
        assert change.reason == "first observation"

    def test_the_same_screen_twice_is_not_a_change(self):
        detector = ChangeDetector()

        detector.check(observation())
        change = detector.check(observation())

        assert not change.changed
        assert change.similarity == 1.0

    def test_a_new_app_is_always_a_change(self):
        detector = ChangeDetector()

        detector.check(observation(package="com.google.android.gm"))
        change = detector.check(observation(package="com.spotify.music"))

        assert change.changed
        assert change.app_switched
        assert change.current_app == "com.spotify.music"

    def test_a_ticking_clock_is_not_a_change(self):
        detector = ChangeDetector()

        body = "Meeting notes from the quarterly review with the whole team"

        detector.check(observation(screen_text=f"{body} 10:04"))
        change = detector.check(observation(screen_text=f"{body} 10:05"))

        # One token in thirteen changed. That is a clock, not a screen.
        assert not change.changed

    def test_a_ticking_clock_is_not_a_change_on_a_short_screen(self):
        detector = ChangeDetector()

        # The failure token overlap cannot catch on its own: with only
        # three words of content, one changed token is a third of the
        # screen and clears any threshold worth keeping.
        detector.check(observation(screen_text="Now playing 3:41"))
        change = detector.check(observation(screen_text="Now playing 3:42"))

        assert not change.changed

    def test_a_seconds_clock_is_not_a_change(self):
        detector = ChangeDetector()

        detector.check(observation(screen_text="Recording 00:01:04"))
        change = detector.check(observation(screen_text="Recording 00:01:05"))

        assert not change.changed

    def test_a_rolling_date_is_not_a_change(self):
        detector = ChangeDetector()

        detector.check(observation(screen_text="Sync completed 2026-08-09"))
        change = detector.check(
            observation(screen_text="Sync completed 2026-08-10")
        )

        assert not change.changed

    def test_an_unread_counter_is_not_a_change(self):
        detector = ChangeDetector()

        detector.check(observation(screen_text="Inbox 3 unread"))
        change = detector.check(observation(screen_text="Inbox 4 unread"))

        assert not change.changed

    def test_a_progress_percentage_is_not_a_change(self):
        detector = ChangeDetector()

        detector.check(observation(screen_text="Downloading update 41%"))
        change = detector.check(
            observation(screen_text="Downloading update 87%")
        )

        # The same screen doing the same thing. Aura has nothing new to
        # say between 41% and 87%.
        assert not change.changed

    def test_new_words_are_a_change_even_when_a_counter_moves(self):
        detector = ChangeDetector()

        detector.check(observation(screen_text="Inbox 3 unread"))
        change = detector.check(
            observation(screen_text="Build failed 4 tests did not pass")
        )

        # Collapsing counters must not collapse the words around them.
        assert change.changed

    def test_a_word_that_merely_contains_digits_still_counts(self):
        detector = ChangeDetector()

        detector.check(observation(screen_text="Deploying release v2.1.0"))
        change = detector.check(
            observation(screen_text="Deploying release v3.0.0-rc1")
        )

        assert change.changed

    def test_replacing_the_content_is_a_change(self):
        detector = ChangeDetector()

        detector.check(observation(screen_text="Inbox 3 unread"))
        change = detector.check(
            observation(screen_text="Build failed: 4 tests did not pass")
        )

        assert change.changed

    def test_inspect_does_not_record(self):
        detector = ChangeDetector()

        detector.inspect(observation())
        change = detector.inspect(observation())

        # Pure: calling it twice gives the same answer both times.
        assert change.changed
        assert detector.previous is None

    def test_an_unchanged_screen_does_not_drift_the_baseline(self):
        detector = ChangeDetector(threshold=0.5)

        first = observation(screen_text="a b c d e f g h")
        detector.check(first)

        # Each step is small enough to pass as "the same screen"...
        detector.check(observation(screen_text="a b c d e f g x"))
        detector.check(observation(screen_text="a b c d e f x y"))

        # ...so the baseline must still be the original, or a slow scroll
        # would never count as a change at all.
        assert detector.previous == first


# ----------------------------------------------------------------------
# Sensitive screens
# ----------------------------------------------------------------------

class TestSensitiveScreens:

    @pytest.mark.parametrize("text", [
        "Enter your password",
        "Your one-time code is 123456",
        "Card number ending 4242",
        "Write down your seed phrase",
    ])
    def test_sensitive_text_is_off_limits(self, text):
        assert looks_sensitive(observation(screen_text=text))

    @pytest.mark.parametrize("package", [
        "com.android.settings",
        "com.bitwarden.x8",
        "com.google.android.apps.authenticator2",
    ])
    def test_sensitive_apps_are_off_limits(self, package):
        assert looks_sensitive(observation(package=package, screen_text=""))

    def test_an_ordinary_screen_is_not_sensitive(self):
        assert not looks_sensitive(observation())

    def test_a_sensitive_screen_vetoes_rather_than_scores(self):
        relevance = HeuristicEvaluator().evaluate(
            observation(screen_text="Enter your password to continue"),
            ChangeDetector().check(observation()),
        )

        assert relevance.sensitive
        assert relevance.score == 0.0


# ----------------------------------------------------------------------
# Relevance
# ----------------------------------------------------------------------

class TestHeuristicEvaluator:

    def test_an_error_on_screen_scores_high(self):
        change = ChangeDetector().check(observation())

        relevance = HeuristicEvaluator().evaluate(
            observation(screen_text="Build failed: traceback follows"),
            change,
        )

        assert relevance.score >= 0.6

    def test_an_ordinary_screen_scores_low(self):
        change = ChangeDetector().check(observation())

        relevance = HeuristicEvaluator().evaluate(observation(), change)

        assert relevance.score < 0.4


class TestLLMRelevanceEvaluator:

    def test_a_relevant_verdict_carries_the_message(self):
        llm = ScriptedLLM(relevant_reply())

        relevance = LLMRelevanceEvaluator(llm).evaluate(
            observation(), ChangeDetector().check(observation())
        )

        assert relevance.score == 0.9
        assert relevance.message == "Your build failed."

    def test_a_negative_verdict_scores_zero(self):
        llm = ScriptedLLM(json.dumps({
            "relevant": False, "confidence": 0.9,
            "reason": "just scrolling", "message": "",
        }))

        relevance = LLMRelevanceEvaluator(llm).evaluate(
            observation(), ChangeDetector().check(observation())
        )

        assert relevance.score == 0.0

    def test_relevant_with_nothing_to_say_is_not_relevant(self):
        llm = ScriptedLLM(json.dumps({
            "relevant": True, "confidence": 0.95, "message": "  ",
        }))

        relevance = LLMRelevanceEvaluator(llm).evaluate(
            observation(), ChangeDetector().check(observation())
        )

        assert relevance.score == 0.0

    def test_unparseable_output_scores_zero(self):
        llm = ScriptedLLM("I think you should probably look at this!")

        relevance = LLMRelevanceEvaluator(llm).evaluate(
            observation(), ChangeDetector().check(observation())
        )

        assert relevance.score == 0.0

    def test_a_provider_failure_scores_zero(self):
        llm = ScriptedLLM(error=ConnectionError("provider down"))

        relevance = LLMRelevanceEvaluator(llm).evaluate(
            observation(), ChangeDetector().check(observation())
        )

        # A companion whose evaluator is down says nothing, rather than
        # everything.
        assert relevance.score == 0.0

    def test_a_sensitive_screen_never_reaches_the_model(self):
        llm = ScriptedLLM(relevant_reply())

        relevance = LLMRelevanceEvaluator(llm).evaluate(
            observation(screen_text="Enter your password"),
            ChangeDetector().check(observation()),
        )

        assert relevance.sensitive
        assert llm.calls == 0

    def test_json_wrapped_in_prose_is_still_read(self):
        llm = ScriptedLLM(f"Sure! {relevant_reply()} Hope that helps.")

        relevance = LLMRelevanceEvaluator(llm).evaluate(
            observation(), ChangeDetector().check(observation())
        )

        assert relevance.score == 0.9


# ----------------------------------------------------------------------
# Policy
# ----------------------------------------------------------------------

class TestCompanionPolicy:

    def test_disabled_by_default(self):
        decision = CompanionPolicy().allows(1.0, "something important")

        assert not decision.should_notify

    def test_a_relevant_message_is_allowed(self):
        policy = CompanionPolicy(settings=enabled_settings())

        decision = policy.allows(0.9, "Your build failed.")

        assert decision.should_notify
        assert decision.message == "Your build failed."
        assert decision.priority == Priority.HIGH

    def test_below_the_threshold_stays_quiet(self):
        policy = CompanionPolicy(settings=enabled_settings())

        decision = policy.allows(0.5, "Something happened.")

        assert not decision.should_notify
        assert "threshold" in decision.reason

    def test_nothing_to_say_stays_quiet(self):
        policy = CompanionPolicy(settings=enabled_settings())

        assert not policy.allows(1.0, "   ").should_notify

    def test_cooldown_blocks_the_second_notification(self):
        clock = FakeClock()
        policy = CompanionPolicy(settings=enabled_settings(), clock=clock)

        assert policy.allows(0.9, "First thing").should_notify
        policy.note_notified("First thing")

        clock.advance(10.0)

        decision = policy.allows(0.9, "Second thing")

        assert not decision.should_notify
        assert "cooling down" in decision.reason

    def test_cooldown_expires(self):
        clock = FakeClock()
        policy = CompanionPolicy(settings=enabled_settings(), clock=clock)

        policy.note_notified("First thing")
        clock.advance(301.0)

        assert policy.allows(0.9, "Second thing").should_notify

    def test_the_same_message_is_not_repeated(self):
        clock = FakeClock()
        policy = CompanionPolicy(settings=enabled_settings(), clock=clock)

        policy.note_notified("Your build failed.")
        clock.advance(301.0)

        decision = policy.allows(0.9, "your build FAILED.")

        # Case and spacing are not what makes a remark different.
        assert not decision.should_notify
        assert "already said this" in decision.reason

    def test_an_active_conversation_suppresses_notifications(self):
        clock = FakeClock()
        policy = CompanionPolicy(settings=enabled_settings(), clock=clock)

        policy.note_chat()
        clock.advance(10.0)

        decision = policy.allows(0.95, "Your build failed.")

        assert not decision.should_notify
        assert "mid-conversation" in decision.reason

    def test_suppression_after_a_chat_expires(self):
        clock = FakeClock()
        policy = CompanionPolicy(settings=enabled_settings(), clock=clock)

        policy.note_chat()
        clock.advance(121.0)

        assert policy.allows(0.9, "Your build failed.").should_notify

    def test_quiet_hours_silence_everything(self):
        policy = CompanionPolicy(
            settings=enabled_settings(quiet_hours=[[23, 7]]),
            local_hour=lambda: 3,
        )

        decision = policy.allows(1.0, "Your build failed.")

        assert not decision.should_notify
        assert decision.reason == "quiet hours"

    def test_a_midnight_wrapping_window_ends(self):
        policy = CompanionPolicy(
            settings=enabled_settings(quiet_hours=[[23, 7]]),
            local_hour=lambda: 9,
        )

        assert policy.allows(0.9, "Your build failed.").should_notify

    def test_a_daytime_window_is_read_as_written(self):
        policy = CompanionPolicy(
            settings=enabled_settings(quiet_hours=[[9, 17]]),
            local_hour=lambda: 12,
        )

        assert not policy.allows(1.0, "Your build failed.").should_notify

    def test_a_malformed_quiet_window_is_ignored(self):
        policy = CompanionPolicy(
            settings=enabled_settings(quiet_hours=[["nonsense"], [], None]),
            local_hour=lambda: 12,
        )

        assert policy.allows(0.9, "Your build failed.").should_notify

    def test_the_hourly_ceiling_holds(self):
        clock = FakeClock()
        policy = CompanionPolicy(
            settings=enabled_settings(max_per_hour=3, cooldown_seconds=1.0),
            clock=clock,
        )

        for index in range(3):
            clock.advance(60.0)
            assert policy.allows(0.9, f"Thing {index}").should_notify
            policy.note_notified(f"Thing {index}")

        clock.advance(60.0)
        decision = policy.allows(0.9, "Thing 4")

        assert not decision.should_notify
        assert "hourly limit" in decision.reason

    def test_the_hourly_window_slides(self):
        clock = FakeClock()
        policy = CompanionPolicy(
            settings=enabled_settings(max_per_hour=1, cooldown_seconds=1.0),
            clock=clock,
        )

        policy.note_notified("Thing 1")
        clock.advance(3601.0)

        assert policy.allows(0.9, "Thing 2").should_notify

    def test_a_zero_ceiling_means_never(self):
        policy = CompanionPolicy(settings=enabled_settings(max_per_hour=0))

        assert not policy.allows(1.0, "Your build failed.").should_notify

    @pytest.mark.parametrize("score,expected", [
        (0.95, Priority.HIGH),
        (0.80, Priority.NORMAL),
        (0.70, Priority.LOW),
    ])
    def test_confidence_maps_to_priority(self, score, expected):
        policy = CompanionPolicy(settings=enabled_settings())

        assert policy.allows(score, "Something").priority == expected


class TestPolicySettings:

    def test_it_reads_the_config_section(self):
        settings = PolicySettings.from_config({
            "enabled": True,
            "relevance_threshold": 0.8,
            "cooldown_seconds": 60,
            "max_per_hour": 2,
            "quiet_hours": [[22, 8]],
        })

        assert settings.enabled
        assert settings.relevance_threshold == 0.8
        assert settings.cooldown_seconds == 60.0
        assert settings.max_per_hour == 2
        assert settings.quiet_hours == [[22, 8]]

    def test_a_missing_section_is_disabled(self):
        assert not PolicySettings.from_config(None).enabled

    def test_a_partial_section_keeps_the_defaults(self):
        settings = PolicySettings.from_config({"enabled": True})

        assert settings.relevance_threshold == 0.7
        assert settings.cooldown_seconds == 300.0


# ----------------------------------------------------------------------
# The engine
# ----------------------------------------------------------------------

def build_engine(llm_reply: str = "", clock=None, **setting_overrides):
    """An enabled engine with a scripted model behind it."""

    clock = clock or FakeClock()

    return CompanionEngine(
        policy=CompanionPolicy(
            settings=enabled_settings(**setting_overrides),
            clock=clock,
            local_hour=lambda: 12,
        ),
        evaluator=(
            LLMRelevanceEvaluator(ScriptedLLM(llm_reply)) if llm_reply else None
        ),
        clock=clock,
    )


class TestCompanionEngine:

    def test_disabled_by_default(self):
        engine = CompanionEngine()

        decision = engine.observe(
            observation(screen_text="Build failed: traceback")
        )

        assert not decision.should_notify
        assert not engine.enabled

    def test_a_relevant_screen_notifies(self):
        engine = build_engine(relevant_reply())

        decision = engine.observe(
            observation(screen_text="Build failed: traceback follows")
        )

        assert decision.should_notify
        assert decision.message == "Your build failed."
        assert engine.notifications == 1

    def test_an_unchanged_screen_is_dropped_before_the_model(self):
        llm = ScriptedLLM(relevant_reply())
        engine = build_engine()
        engine.evaluator = LLMRelevanceEvaluator(llm)

        interesting = observation(screen_text="Build failed: traceback follows")

        engine.observe(interesting)
        calls_after_first = llm.calls

        second = engine.observe(interesting)

        assert not second.should_notify
        assert "same screen" in second.reason
        # The expensive gate ran once, for one screen.
        assert llm.calls == calls_after_first

    def test_an_ordinary_screen_never_reaches_the_model(self):
        llm = ScriptedLLM(relevant_reply())
        engine = build_engine()
        engine.evaluator = LLMRelevanceEvaluator(llm)

        decision = engine.observe(observation(screen_text="Inbox 3 unread"))

        assert not decision.should_notify
        assert llm.calls == 0

    def test_a_sensitive_screen_stays_silent(self):
        llm = ScriptedLLM(relevant_reply("You should check that code."))
        engine = build_engine()
        engine.evaluator = LLMRelevanceEvaluator(llm)

        decision = engine.observe(
            observation(screen_text="Enter your password - login failed")
        )

        assert not decision.should_notify
        assert decision.reason == "sensitive screen"
        assert llm.calls == 0

    def test_an_empty_observation_stays_silent(self):
        engine = build_engine(relevant_reply())

        decision = engine.observe(ScreenObservation())

        assert not decision.should_notify
        assert decision.reason == "nothing on screen"

    def test_it_publishes_an_event_when_it_speaks(self):
        bus = EventBus()
        seen = []
        bus.subscribe(CompanionNotificationEvent, seen.append)

        engine = build_engine(relevant_reply())
        engine.events = bus

        engine.observe(observation(screen_text="Build failed: traceback follows"))

        assert len(seen) == 1
        assert seen[0].message == "Your build failed."
        assert seen[0].device_id == "phone-1"
        assert seen[0].priority == "high"

    def test_it_publishes_nothing_when_it_stays_quiet(self):
        bus = EventBus()
        seen = []
        bus.subscribe(CompanionNotificationEvent, seen.append)

        engine = build_engine(relevant_reply())
        engine.events = bus

        engine.observe(observation(screen_text="Inbox 3 unread"))

        assert seen == []

    def test_an_active_conversation_suppresses_it(self):
        clock = FakeClock()
        engine = build_engine(relevant_reply(), clock=clock)

        engine.note_chat()
        clock.advance(5.0)

        decision = engine.observe(
            observation(screen_text="Build failed: traceback follows")
        )

        assert not decision.should_notify
        assert "mid-conversation" in decision.reason

    def test_a_broken_evaluator_does_not_break_the_engine(self):
        class Exploding:
            def evaluate(self, observation, change):
                raise RuntimeError("boom")

        engine = build_engine()
        engine.evaluator = Exploding()

        decision = engine.observe(
            observation(screen_text="Build failed: traceback follows")
        )

        # An observation is an enhancement. It must never take down the
        # endpoint that received it.
        assert not decision.should_notify

    def test_a_broken_subscriber_does_not_break_the_engine(self):
        bus = EventBus()
        bus.subscribe(CompanionNotificationEvent, lambda event: 1 / 0)

        engine = build_engine(relevant_reply())
        engine.events = bus

        decision = engine.observe(
            observation(screen_text="Build failed: traceback follows")
        )

        assert decision.should_notify

    def test_it_counts_what_it_saw_and_what_it_said(self):
        engine = build_engine(relevant_reply())

        engine.observe(observation(screen_text="Inbox 3 unread"))
        engine.observe(observation(screen_text="Build failed: traceback follows"))

        assert engine.observations == 2
        assert engine.notifications == 1

    def test_the_last_decision_is_readable(self):
        engine = build_engine()

        engine.observe(observation())

        assert isinstance(engine.last_decision, CompanionDecision)

    def test_the_model_cannot_out_confident_a_dull_screen(self):
        # The heuristic pre-filter is a floor on cost, not a ceiling on
        # judgement - but a screen it found dull never gets asked at all.
        llm = ScriptedLLM(relevant_reply(confidence=1.0))
        engine = build_engine()
        engine.evaluator = LLMRelevanceEvaluator(llm)

        decision = engine.observe(observation(screen_text="Inbox 3 unread"))

        assert not decision.should_notify
        assert llm.calls == 0


class TestBuildCompanionEngine:

    def test_it_reads_the_server_section(self):
        engine = build_companion_engine({
            "server": {"companion": {
                "enabled": True,
                "relevance_threshold": 0.8,
                "cooldown_seconds": 60,
            }},
        })

        assert engine.enabled
        assert engine.policy.settings.relevance_threshold == 0.8

    def test_a_missing_section_builds_a_disabled_engine(self):
        assert not build_companion_engine({}).enabled
        assert not build_companion_engine(None).enabled

    def test_no_llm_means_no_evaluator(self):
        engine = build_companion_engine({"server": {"companion": {"enabled": True}}})

        assert engine.evaluator is None

    def test_an_llm_becomes_the_evaluator(self):
        engine = build_companion_engine(
            {"server": {"companion": {"enabled": True}}},
            llm=ScriptedLLM(relevant_reply()),
        )

        assert isinstance(engine.evaluator, LLMRelevanceEvaluator)
