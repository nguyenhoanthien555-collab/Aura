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

# A live server, for the tests that have to prove a PATCH reached the
# running gate rather than only the disk.
from tests.test_settings_api import api, AUTH  # noqa: F401


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


# ----------------------------------------------------------------------
# Section 20 - "Do not spam notifications."
#
# Phase 13 fixed this exact shape one floor up: every proactive limit was
# derived from a RAM-only deque, so a restart handed back a clean slate.
# The companion policy had it too, and worse - its clock is
# `time.monotonic`, which is not even comparable across processes.
#
# `max_per_hour` is documented in the policy as "a hard ceiling that
# survives a bad relevance score". An hour is longer than a process, so
# until this it survived nothing of the kind.
# ----------------------------------------------------------------------


class FakeWallClock:
    """Wall time, for the durable record only. Intervals stay monotonic."""

    def __init__(self, now=None):
        from datetime import datetime
        self.now = now or datetime(2026, 8, 24, 14, 0, 0)

    def __call__(self):
        return self.now

    def advance(self, seconds: float) -> None:
        from datetime import timedelta
        self.now += timedelta(seconds=seconds)


class TestTheCompanionCeilingSurvivesARestart:

    def ledger(self, tmp_path):
        from proactive.ledger import SendLedger
        return SendLedger(tmp_path / "companion.json")

    def policy(self, ledger, clock, wall, **overrides):
        return CompanionPolicy(
            settings=enabled_settings(**overrides),
            clock=clock,
            ledger=ledger,
            wall_clock=wall,
        )

    def test_the_hourly_ceiling_is_still_there_after_a_restart(self, tmp_path):
        ledger = self.ledger(tmp_path)
        clock, wall = FakeClock(), FakeWallClock()

        first = self.policy(ledger, clock, wall, max_per_hour=3)

        for index in range(3):
            assert first.allows(0.9, f"Thing {index}").should_notify
            first.note_notified(f"Thing {index}")
            clock.advance(301.0)
            wall.advance(301.0)

        # A new process, same ledger, and only two minutes have passed.
        clock.advance(120.0)
        wall.advance(120.0)
        second = self.policy(ledger, clock, wall, max_per_hour=3)

        decision = second.allows(0.9, "A fourth thing")

        assert not decision.should_notify
        assert "hourly limit reached (3)" in decision.reason

    def test_the_cooldown_is_still_running_after_a_restart(self, tmp_path):
        ledger = self.ledger(tmp_path)
        clock, wall = FakeClock(), FakeWallClock()

        self.policy(ledger, clock, wall).note_notified("Your build failed.")

        clock.advance(30.0)
        wall.advance(30.0)

        decision = self.policy(ledger, clock, wall).allows(0.9, "Something else")

        assert not decision.should_notify
        assert "cooling down" in decision.reason

    def test_she_does_not_repeat_herself_after_a_restart(self, tmp_path):
        ledger = self.ledger(tmp_path)
        clock, wall = FakeClock(), FakeWallClock()

        self.policy(ledger, clock, wall).note_notified("Your build failed.")

        clock.advance(400.0)
        wall.advance(400.0)

        decision = self.policy(ledger, clock, wall).allows(0.9, "your build FAILED.")

        assert not decision.should_notify
        assert "already said this" in decision.reason

    def test_a_persisted_history_is_not_a_permanent_ban(self, tmp_path):
        ledger = self.ledger(tmp_path)
        clock, wall = FakeClock(), FakeWallClock()

        first = self.policy(ledger, clock, wall, max_per_hour=2)
        first.note_notified("One")
        first.note_notified("Two")

        # Two hours later the ceiling is an hour behind, not a life sentence.
        clock.advance(7200.0)
        wall.advance(7200.0)

        assert self.policy(
            ledger, clock, wall, max_per_hour=2
        ).allows(0.9, "Three").should_notify

    def test_the_owner_dropping_the_limit_clears_the_file_too(self, tmp_path):
        ledger = self.ledger(tmp_path)
        clock, wall = FakeClock(), FakeWallClock()

        policy = self.policy(ledger, clock, wall, max_per_hour=1)
        policy.note_notified("One")
        policy.reset()

        assert ledger.load() == ()
        assert self.policy(
            ledger, clock, wall, max_per_hour=1
        ).allows(0.9, "Two").should_notify

    def test_an_old_send_ages_out_across_the_restart_too(self, tmp_path):
        """Every row keeps its own age, not the age of the newest one.

        Both clocks advance together here, so nothing is testing the
        translation unless the two sends are far apart: an implementation
        that stamped every row "just now" on the way out would pass a
        one-entry test and then, after a restart, count an hour-old send
        against a ceiling it had already left.
        """

        ledger = self.ledger(tmp_path)
        clock, wall = FakeClock(), FakeWallClock()

        first = self.policy(ledger, clock, wall, max_per_hour=2)
        first.note_notified("An hour ago")

        clock.advance(3500.0)
        wall.advance(3500.0)
        first.note_notified("Recently")

        clock.advance(400.0)
        wall.advance(400.0)

        # The first send is now 3900s old and the second 400s. One of them
        # is inside the hour, so a ceiling of two has room.
        second = self.policy(ledger, clock, wall, max_per_hour=2)

        assert second.allows(0.9, "A third thing").should_notify

    def test_the_rules_see_nothing_from_two_days_ago(self, tmp_path):
        """Rows too old for any rule to consult do not come back at all.

        Not merely tidiness. The history is a 32-slot deque, so a file that
        accumulated every send Aura ever made would restore dead rows into
        slots the live ones need, and the ceiling would then be counted
        against whatever happened to fit.
        """

        ledger = self.ledger(tmp_path)
        clock, wall = FakeClock(), FakeWallClock()

        self.policy(ledger, clock, wall).note_notified("Ancient history")

        clock.advance(172800.0)
        wall.advance(172800.0)

        assert self.policy(ledger, clock, wall).history() == ()

    def test_a_clock_that_moved_backwards_does_not_wedge_the_gate(self, tmp_path):
        """A send stamped in the future is discarded, not obeyed.

        Wall time is not monotonic on a real machine - DST, an NTP
        correction, an owner fixing their timezone. Trusting a negative age
        would place the send ahead of now, every interval the rules compare
        would come out negative, and Aura would go silent until real time
        caught up with the bad stamp.
        """

        ledger = self.ledger(tmp_path)
        clock, wall = FakeClock(), FakeWallClock()

        self.policy(ledger, clock, wall, max_per_hour=1).note_notified("Before")

        clock.advance(600.0)
        wall.advance(-3600.0)      # the owner's clock went back an hour

        decision = self.policy(
            ledger, clock, wall, max_per_hour=1
        ).allows(0.9, "After")

        assert decision.should_notify, decision.reason

    def test_a_hand_edited_row_is_still_matched_as_a_duplicate(self, tmp_path):
        """The file is plain JSON, so what comes out of it is normalised too.

        `note_notified` normalises before storing, which makes this look
        redundant from inside. It is not: the ledger is a text file with the
        same format as the proactive one, and a row an owner pasted in or an
        older build wrote is under nobody's control here.
        """

        from datetime import datetime

        ledger = self.ledger(tmp_path)
        wall = FakeWallClock()
        ledger.save([(wall.now, "companion", "  Your Build   FAILED. ")])

        clock = FakeClock()
        clock.advance(400.0)       # past the cooldown, inside the window
        wall.advance(400.0)

        decision = self.policy(ledger, clock, wall).allows(0.9, "your build failed.")

        assert not decision.should_notify
        assert "already said this" in decision.reason

    def test_the_cooldown_runs_from_the_newest_send(self, tmp_path):
        """Two sends, and it is the recent one the cooldown measures from.

        With one entry in the history every reading of it agrees. With two
        an implementation that reached for the wrong end of the deque would
        time the cooldown from something Aura said fifty minutes ago and
        let her speak twice in a minute.
        """

        ledger = self.ledger(tmp_path)
        clock, wall = FakeClock(), FakeWallClock()

        policy = self.policy(ledger, clock, wall, max_per_hour=6)
        policy.note_notified("Fifty minutes ago")

        clock.advance(3000.0)
        wall.advance(3000.0)
        policy.note_notified("Just now")

        clock.advance(60.0)
        wall.advance(60.0)

        decision = policy.allows(0.9, "Something else")

        assert not decision.should_notify
        assert "cooling down" in decision.reason

    def test_without_a_ledger_nothing_is_written(self, tmp_path):
        clock, wall = FakeClock(), FakeWallClock()

        policy = CompanionPolicy(
            settings=enabled_settings(), clock=clock, wall_clock=wall,
        )
        policy.note_notified("One")

        assert list(tmp_path.iterdir()) == []
        assert policy.history()


# ----------------------------------------------------------------------
# Section 21 - Aura must not speak over a present owner.
#
# `_last_chat` was volatile, and `suppress_after_chat_seconds` reads a
# missing value as "no recent chat", which the gate reads as "go ahead".
# So a restart mid-conversation let the very next screen observation
# interrupt someone who was plainly right there. Phase 13 fixed the same
# defect in the greeting rule; the answer already exists in the messages
# table, so this reuses it rather than inventing a second presence source.
# ----------------------------------------------------------------------


class TestTheCompanionKnowsTheOwnerIsHere:

    def policy(self, clock, wall, last_user_message=None, **overrides):
        return CompanionPolicy(
            settings=enabled_settings(**overrides),
            clock=clock,
            wall_clock=wall,
            last_user_message=last_user_message,
        )

    def test_a_restart_mid_conversation_does_not_interrupt(self):
        clock, wall = FakeClock(), FakeWallClock()

        spoke_at = wall.now
        wall.advance(20.0)
        clock.advance(20.0)

        # Nothing called note_chat on this policy - it is a fresh process.
        decision = self.policy(
            clock, wall, last_user_message=lambda: spoke_at,
        ).allows(0.95, "Your build failed.")

        assert not decision.should_notify
        assert "mid-conversation" in decision.reason

    def test_an_owner_who_left_an_hour_ago_is_not_present(self):
        clock, wall = FakeClock(), FakeWallClock()

        spoke_at = wall.now
        wall.advance(3600.0)
        clock.advance(3600.0)

        assert self.policy(
            clock, wall, last_user_message=lambda: spoke_at,
        ).allows(0.9, "Your build failed.").should_notify

    def test_the_live_signal_wins_over_the_stored_one(self):
        clock, wall = FakeClock(), FakeWallClock()

        stale = wall.now
        wall.advance(3600.0)
        clock.advance(3600.0)

        policy = self.policy(clock, wall, last_user_message=lambda: stale)
        policy.note_chat()

        decision = policy.allows(0.95, "Your build failed.")

        assert not decision.should_notify
        assert "mid-conversation" in decision.reason

    def test_no_source_and_no_chat_is_not_treated_as_present(self):
        clock, wall = FakeClock(), FakeWallClock()

        assert self.policy(clock, wall).allows(0.9, "Your build failed.").should_notify

    def test_a_source_that_answers_nothing_is_not_presence(self):
        clock, wall = FakeClock(), FakeWallClock()

        assert self.policy(
            clock, wall, last_user_message=lambda: None,
        ).allows(0.9, "Your build failed.").should_notify

    def test_a_source_that_raises_does_not_take_the_gate_with_it(self):
        clock, wall = FakeClock(), FakeWallClock()

        def broken():
            raise RuntimeError("database is gone")

        assert self.policy(
            clock, wall, last_user_message=broken,
        ).allows(0.9, "Your build failed.").should_notify

    def test_a_source_returning_nonsense_is_ignored(self):
        clock, wall = FakeClock(), FakeWallClock()

        assert self.policy(
            clock, wall, last_user_message=lambda: "yesterday",
        ).allows(0.9, "Your build failed.").should_notify

    def test_the_suppression_can_be_turned_off_by_the_owner(self):
        clock, wall = FakeClock(), FakeWallClock()

        spoke_at = wall.now
        wall.advance(1.0)
        clock.advance(1.0)

        assert self.policy(
            clock, wall,
            last_user_message=lambda: spoke_at,
            suppress_after_chat_seconds=0.0,
        ).allows(0.9, "Your build failed.").should_notify


# ----------------------------------------------------------------------
# Section 2 - the owner configures Aura through the settings.
#
# Six companion knobs exist in config.yaml and exactly one of them,
# `enabled`, was reachable from the app. The other five are the anti-spam
# dial section 20 is about: how relevant, how often, how soon after
# talking, and through which hours. An owner being notified too much could
# turn the whole feature off and nothing in between.
#
# The proactive engine next door exposes six equivalents with validators
# and a live-apply handler. This is that, for its sibling.
# ----------------------------------------------------------------------


COMPANION_PATHS = (
    "server.companion.relevance_threshold",
    "server.companion.cooldown_seconds",
    "server.companion.max_per_hour",
    "server.companion.quiet_hours",
    "server.companion.suppress_after_chat_seconds",
    "server.companion.duplicate_window_seconds",
)


class TestTheOwnerCanTuneTheNotificationGate:

    def test_every_knob_is_in_the_settings_contract(self):
        from core.settings_store import ALLOWED

        missing = [p for p in COMPANION_PATHS if p not in ALLOWED]

        assert missing == []

    def test_every_knob_applies_without_a_restart(self):
        from server.settings_service import LIVE_PATHS

        missing = [p for p in COMPANION_PATHS if p not in LIVE_PATHS]

        assert missing == []

    def test_each_knob_names_a_real_field_on_the_settings_object(self):
        settings = PolicySettings()

        for path in COMPANION_PATHS:
            field = path.rsplit(".", 1)[1]
            assert hasattr(settings, field), field

    def test_the_values_the_owner_sends_are_the_values_that_are_kept(self):
        from core.settings_store import validate_path

        assert validate_path("server.companion.relevance_threshold", 0.55) == 0.55
        assert validate_path("server.companion.cooldown_seconds", 900) == 900.0
        assert validate_path("server.companion.max_per_hour", 2) == 2
        assert validate_path(
            "server.companion.suppress_after_chat_seconds", 0
        ) == 0.0
        assert validate_path(
            "server.companion.duplicate_window_seconds", 3600
        ) == 3600.0
        assert validate_path(
            "server.companion.quiet_hours", [[23, 7]]
        ) == [[23, 7]]

    def test_the_gate_cannot_be_widened_into_a_spammer(self):
        from core.settings_store import SettingsError, validate_path

        # The cooldown floor is five minutes, which is what makes twelve an
        # hour the highest reachable ceiling - a larger number would be one
        # the cooldown never lets anybody hit.
        for path, value in (
            ("server.companion.cooldown_seconds", 5),
            ("server.companion.max_per_hour", 0),
            ("server.companion.max_per_hour", 13),
            ("server.companion.relevance_threshold", 0.0),
            ("server.companion.duplicate_window_seconds", 30),
        ):
            with pytest.raises(SettingsError):
                validate_path(path, value)

    def test_the_duplicate_window_is_a_setting_not_a_constant(self):
        clock = FakeClock()
        policy = CompanionPolicy(
            settings=enabled_settings(duplicate_window_seconds=60.0),
            clock=clock,
        )

        policy.note_notified("Your build failed.")
        clock.advance(400.0)

        assert policy.allows(0.9, "Your build failed.").should_notify


# ----------------------------------------------------------------------
# The composition root, which is where all of the above either happens or
# quietly does not. Phase 11 part 2 lost an entire feature to a builder
# argument nobody tested; phase 13's `session_id="default"` was the same
# shape. These drive the real builder.
# ----------------------------------------------------------------------


class TestTheBuilderHandsTheGateWhatItNeeds:

    def config(self, **companion):
        fields = {"enabled": True}
        fields.update(companion)
        return {"server": {"companion": fields}}

    def test_the_builder_accepts_a_ledger_and_uses_it(self, tmp_path):
        from proactive.ledger import SendLedger

        ledger = SendLedger(tmp_path / "companion.json")

        engine = build_companion_engine(self.config(), ledger=ledger)

        assert engine.policy.ledger is ledger

    def test_the_builder_accepts_a_presence_source(self):
        source = lambda: None

        engine = build_companion_engine(self.config(), last_user_message=source)

        assert engine.policy.last_user_message is source

    def test_a_bare_builder_writes_no_file(self):
        engine = build_companion_engine(self.config())

        assert engine.policy.ledger is None

    def test_the_owners_window_reaches_the_policy(self):
        engine = build_companion_engine(
            self.config(duplicate_window_seconds=90, max_per_hour=2),
        )

        assert engine.policy.settings.duplicate_window_seconds == 90.0
        assert engine.policy.settings.max_per_hour == 2


# ----------------------------------------------------------------------
# Section 44 - the paths being in `LIVE_PATHS` is not the same fact as a
# handler that reapplies them. Phase 11 part 1 shipped exactly that gap
# for `memory.recall`: the path was live, no handler ran, and the PATCH
# reply still said `applied`. So these drive a real PATCH against a real
# runtime and read the policy object afterwards.
# ----------------------------------------------------------------------


class TestTuningTheGateReachesTheRunningGate:

    def live_policy(self, monkeypatch):
        """Attach a companion engine to the running runtime.

        The test runtime has none: `server.companion.enabled` is false by
        default, which on a headless server is the normal case. That is
        precisely the condition the handler has to answer for, so the
        second test below leaves it alone.
        """

        from server.runtime import get_runtime

        engine = build_companion_engine({"server": {"companion": {"enabled": True}}})

        monkeypatch.setattr(get_runtime(), "companion_engine", engine, raising=False)

        return engine.policy

    def patch(self, api, body):
        response = api.patch(
            "/api/settings", json={"settings": body}, headers=AUTH
        )
        assert response.status_code == 200, response.text
        return response.json()

    def test_a_tuned_gate_is_tuned_before_the_reply_is_sent(self, api, monkeypatch):
        policy = self.live_policy(monkeypatch)

        report = self.patch(api, {"server": {"companion": {
            "relevance_threshold": 0.55,
            "cooldown_seconds": 900,
            "max_per_hour": 2,
            "quiet_hours": [[23, 7]],
            "suppress_after_chat_seconds": 30,
            "duplicate_window_seconds": 3600,
        }}})

        assert policy.settings.relevance_threshold == 0.55
        assert policy.settings.cooldown_seconds == 900.0
        assert policy.settings.max_per_hour == 2
        assert policy.settings.quiet_hours == [[23, 7]]
        assert policy.settings.suppress_after_chat_seconds == 30.0
        assert policy.settings.duplicate_window_seconds == 3600.0

        for path in COMPANION_PATHS:
            if path == "server.companion.enabled":
                continue
            assert path in report["applied"], path
            assert path not in report["restart_required"], path

    def test_a_new_number_changes_the_next_decision(self, api, monkeypatch):
        """Not just the dataclass: the gate reads it at decision time."""

        policy = self.live_policy(monkeypatch)
        policy.clock = FakeClock()

        policy.note_notified("Your build failed.")
        policy.clock.advance(400.0)

        assert not policy.allows(0.9, "Your build failed.").should_notify

        self.patch(api, {"server": {"companion": {
            "duplicate_window_seconds": 60,
        }}})

        assert policy.allows(0.9, "Your build failed.").should_notify

    def test_with_no_gate_running_the_reply_says_restart(self, api):
        """The honest answer when there is nothing live to change.

        `applied` is what the phone renders its controls from, so a path
        listed there that reached nothing is a switch that appears to have
        taken effect and did not.
        """

        from server.runtime import get_runtime

        assert get_runtime().companion_engine is None

        report = self.patch(
            api, {"server": {"companion": {"max_per_hour": 4}}}
        )

        assert "server.companion.max_per_hour" in report["restart_required"]
        assert "server.companion.max_per_hour" not in report["applied"]
        assert report["needs_restart"]


def test_the_server_gives_the_companion_gate_a_durable_ledger():
    """
    The ledger the running server hands the companion policy must be a
    real file under `data/`, and it must NOT be the proactive one.

    Sharing the file would make each policy count the other's sends, which
    silently changes both of the owner's configured numbers - four a day
    and six an hour would become one budget neither of them asked for.

    Asserts the path rather than sending anything, so this test never
    writes to the real data directory.
    """

    import inspect

    from core.paths import DATA_DIR
    from server.runtime import ServerRuntime

    source = inspect.getsource(ServerRuntime._build_companion)

    assert "ledger=" in source
    assert "last_user_message=" in source

    from companion.policy import LEDGER_PATH
    from proactive.ledger import SendLedger

    assert LEDGER_PATH.parent == DATA_DIR
    assert LEDGER_PATH != SendLedger().path
