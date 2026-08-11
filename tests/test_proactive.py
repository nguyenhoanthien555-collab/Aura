"""
Proactive system tests.

Every clock is injected; nothing here waits, sleeps, or depends on when
the suite is run. The decision engine is pure, so its tests construct a
context and assert on the answer with no fixtures at all.

The properties that matter most, and what each one prevents:

    silence is the default              a companion that talks over you
    the gate cannot be overridden       a good reason bypassing cooldowns
    no task is ever invented            confident lies about the user
    an appreciation needs a referent    hollow "thanks for everything"
    publishing is not delivery          claiming a phone showed something
"""

from datetime import datetime, timedelta

import pytest

from companion.decision import Priority
from core.temporal import TemporalClock, TemporalContext
from events.types import CompanionNotificationEvent
from memory.episodic import EpisodicStore
from proactive.context import PendingTask, ProactiveContext
from proactive.decision import (
    ACTIVE_CONVERSATION_SECONDS,
    Category,
    ProactiveDecision,
    should_proactively_message,
)
from proactive.engine import ProactiveEngine, build_proactive_engine
from proactive.messages import MessageComposer
from proactive.policy import (
    ProactivePolicy,
    ProactiveSettings,
    similarity,
)
from proactive.tasks import EpisodicTaskSource


# A Tuesday afternoon, well outside the default quiet hours.
NOW = datetime(2026, 8, 11, 14, 0, 0)


def context_at(
    now: datetime = NOW,
    last_user=None,
    tasks=(),
    memories=(),
    greeted=False,
    sent_today=0,
) -> ProactiveContext:
    """A context built to order, with sane defaults."""

    return ProactiveContext(
        temporal=TemporalContext(now=now),
        last_user_message_at=last_user,
        pending_tasks=tuple(tasks),
        relevant_memories=tuple(memories),
        greeted_this_part=greeted,
        sent_today=sent_today,
    )


@pytest.fixture
def enabled_settings():
    """Enabled, but with every default gate intact."""

    return ProactiveSettings(enabled=True)


@pytest.fixture
def policy(enabled_settings):
    return ProactivePolicy(settings=enabled_settings, clock=lambda: NOW)


class Recorder:
    """Captures published events, standing in for the bus."""

    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


# ======================================================================
# The decision engine - pure, no fixtures
# ======================================================================

def test_nothing_to_say_is_the_default():
    """A fresh context with no absence and no work means silence."""

    decision = should_proactively_message(context_at(last_user=NOW))

    assert decision.send is False
    assert decision.reason


def test_never_interrupts_an_active_conversation():

    decision = should_proactively_message(
        context_at(last_user=NOW - timedelta(seconds=30))
    )

    assert decision.send is False
    assert "recent interaction" in decision.reason


def test_a_decision_always_explains_itself():
    """Silence is the common case, so silence has to be debuggable."""

    silent = should_proactively_message(context_at(last_user=NOW))
    speaking = should_proactively_message(
        context_at(last_user=NOW - timedelta(hours=9))
    )

    assert silent.reason
    assert speaking.reason


def test_greeting_after_a_real_absence():

    decision = should_proactively_message(
        context_at(last_user=NOW - timedelta(hours=9))
    )

    assert decision.send is True
    assert decision.category == Category.GREETING.value
    assert "away 9h" in decision.reason


def test_no_second_greeting_in_the_same_part_of_day():

    decision = should_proactively_message(
        context_at(last_user=NOW - timedelta(hours=9), greeted=True)
    )

    assert decision.category != Category.GREETING.value


def test_a_short_absence_is_not_a_greeting():

    decision = should_proactively_message(
        context_at(last_user=NOW - timedelta(minutes=30))
    )

    assert decision.send is False


def test_pending_work_outranks_a_greeting():
    """The only category that tells the user something they didn't know."""

    decision = should_proactively_message(
        context_at(
            last_user=NOW - timedelta(hours=9),
            tasks=[PendingTask(description="finish the retriever")],
        )
    )

    assert decision.category == Category.TASK.value
    assert decision.detail == "finish the retriever"


def test_no_task_reminder_while_the_user_is_working_on_it():
    """A reminder must not fire while they are actively doing the thing."""

    decision = should_proactively_message(
        context_at(
            last_user=NOW - timedelta(minutes=10),
            tasks=[PendingTask(description="finish the retriever")],
        )
    )

    assert decision.category != Category.TASK.value


def test_appreciation_requires_something_to_appreciate():

    without = should_proactively_message(
        context_at(last_user=NOW - timedelta(hours=9), greeted=True)
    )
    with_context = should_proactively_message(
        context_at(
            last_user=NOW - timedelta(hours=9),
            greeted=True,
            memories=["you started learning Japanese"],
        )
    )

    assert without.send is False
    assert with_context.category == Category.APPRECIATION.value


def test_wellbeing_only_late_at_night():

    at_night = should_proactively_message(
        context_at(
            now=datetime(2026, 8, 11, 23, 30),
            last_user=datetime(2026, 8, 11, 23, 0),
        )
    )
    in_daylight = should_proactively_message(
        context_at(now=NOW, last_user=NOW - timedelta(minutes=30))
    )

    assert at_night.category == Category.WELLBEING.value
    assert in_daylight.send is False


def test_the_decision_engine_is_pure():
    """Same context, same answer. No clock, no database, no state."""

    context = context_at(last_user=NOW - timedelta(hours=9))

    first = should_proactively_message(context)
    second = should_proactively_message(context)

    assert first == second


def test_unknown_last_message_does_not_trigger_a_burst():
    """
    A missing "last seen" must make the rules hold back, not fire. It is
    infinity, and infinity is read as "away", so a greeting is allowed -
    but nothing that requires an active session may fire.
    """

    decision = should_proactively_message(context_at(last_user=None))

    assert decision.category in ("", Category.GREETING.value)


# ======================================================================
# Anti-spam policy - the mandatory gate
# ======================================================================

def test_disabled_by_default():
    """Unprompted messaging is acquired deliberately, never by upgrading."""

    allowed, reason = ProactivePolicy(clock=lambda: NOW).allows(
        Category.GREETING.value, "hello"
    )

    assert allowed is False
    assert "off" in reason


def test_quiet_hours_veto_everything(enabled_settings):

    policy = ProactivePolicy(
        settings=enabled_settings,
        clock=lambda: datetime(2026, 8, 11, 3, 0),
    )

    allowed, reason = policy.allows(Category.TASK.value, "your build is broken")

    assert allowed is False
    assert "quiet hours" in reason


def test_default_quiet_hours_cover_the_night(enabled_settings):
    """Aura does not wake people up."""

    for hour in (22, 23, 0, 3, 7):

        policy = ProactivePolicy(
            settings=ProactiveSettings(enabled=True),
            clock=lambda h=hour: datetime(2026, 8, 11, h, 0),
        )

        allowed, _reason = policy.allows(Category.GREETING.value, "hey")

        assert allowed is False, hour


def test_global_cooldown_blocks_a_different_category(policy):
    """One unprompted message per window, whatever it is about."""

    assert policy.allows(Category.GREETING.value, "morning")[0] is True

    policy.note_sent(Category.GREETING.value, "morning")

    allowed, reason = policy.allows(
        Category.TASK.value, "your migration is unfinished"
    )

    assert allowed is False
    assert "cooldown" in reason


def test_category_cooldown_is_longer_than_the_global_one():

    ticks = {"now": NOW}

    policy = ProactivePolicy(
        settings=ProactiveSettings(enabled=True),
        clock=lambda: ticks["now"],
    )

    policy.note_sent(Category.GREETING.value, "morning")

    # Past the 2h global cooldown, still inside the 6h greeting cooldown.
    ticks["now"] = NOW + timedelta(hours=3)

    allowed, reason = policy.allows(Category.GREETING.value, "afternoon")

    assert allowed is False
    assert "greeting cooldown" in reason


def test_daily_limit_survives_a_stuck_decision_engine():

    ticks = {"now": NOW}

    policy = ProactivePolicy(
        settings=ProactiveSettings(
            enabled=True,
            cooldown_seconds=0,
            category_cooldown_seconds={},
            duplicate_window_seconds=0,
        ),
        clock=lambda: ticks["now"],
    )

    for index in range(4):
        allowed, _reason = policy.allows(Category.TASK.value, f"message {index}")
        assert allowed is True
        policy.note_sent(Category.TASK.value, f"message {index}")
        ticks["now"] = ticks["now"] + timedelta(minutes=1)

    allowed, reason = policy.allows(Category.TASK.value, "message five")

    assert allowed is False
    assert "daily limit" in reason


def test_the_daily_count_resets_the_next_day():

    ticks = {"now": NOW}

    policy = ProactivePolicy(
        settings=ProactiveSettings(
            enabled=True,
            cooldown_seconds=0,
            category_cooldown_seconds={},
            duplicate_window_seconds=0,
        ),
        clock=lambda: ticks["now"],
    )

    for index in range(4):
        policy.note_sent(Category.TASK.value, f"message {index}")

    assert policy.sent_today() == 4

    ticks["now"] = NOW + timedelta(days=1)

    assert policy.sent_today() == 0
    assert policy.allows(Category.TASK.value, "a new day")[0] is True


def test_exact_duplicates_are_suppressed(policy):

    policy.note_sent(Category.TASK.value, "your migration is unfinished")

    allowed, reason = policy.allows(
        Category.TASK.value, "Your migration is unfinished"
    )

    assert allowed is False
    assert "already said this" in reason or "cooldown" in reason


def test_near_duplicates_are_suppressed():
    """
    What string equality cannot catch. Two messages differing by one
    word are not duplicates to a computer and absolutely are to a person.
    """

    ticks = {"now": NOW}

    policy = ProactivePolicy(
        settings=ProactiveSettings(
            enabled=True, cooldown_seconds=0, category_cooldown_seconds={}
        ),
        clock=lambda: ticks["now"],
    )

    policy.note_sent(Category.TASK.value, "you left the migration unfinished")

    allowed, reason = policy.allows(
        Category.TASK.value, "you left the migration unfinished, still?"
    )

    assert allowed is False
    assert "similar" in reason


def test_a_genuinely_different_message_is_allowed():

    ticks = {"now": NOW}

    policy = ProactivePolicy(
        settings=ProactiveSettings(
            enabled=True, cooldown_seconds=0, category_cooldown_seconds={}
        ),
        clock=lambda: ticks["now"],
    )

    policy.note_sent(Category.TASK.value, "you left the migration unfinished")

    allowed, _reason = policy.allows(
        Category.WELLBEING.value, "long stretch, worth a break"
    )

    assert allowed is True


def test_duplicate_suppression_expires():

    ticks = {"now": NOW}

    policy = ProactivePolicy(
        settings=ProactiveSettings(
            enabled=True,
            cooldown_seconds=0,
            category_cooldown_seconds={},
            duplicate_window_seconds=3600,
        ),
        clock=lambda: ticks["now"],
    )

    policy.note_sent(Category.TASK.value, "the migration is unfinished")

    ticks["now"] = NOW + timedelta(hours=2)

    assert policy.allows(Category.TASK.value, "the migration is unfinished")[0]


def test_empty_messages_are_never_sent(policy):

    assert policy.allows(Category.GREETING.value, "")[0] is False
    assert policy.allows(Category.GREETING.value, "   ")[0] is False


def test_the_gate_always_gives_a_reason(policy):

    for message in ("", "hello there friend"):
        _allowed, reason = policy.allows(Category.GREETING.value, message)
        assert reason


@pytest.mark.parametrize(
    "left, right, expected",
    [
        ("the migration is unfinished", "the migration is unfinished", 1.0),
        ("completely different words", "nothing alike whatsoever", 0.0),
    ],
)
def test_similarity_endpoints(left, right, expected):

    assert similarity(left, right) == pytest.approx(expected, abs=0.01)


def test_similarity_ignores_filler_words():

    assert similarity("the migration", "a migration") == pytest.approx(1.0)


def test_settings_from_config_is_tolerant():

    settings = ProactiveSettings.from_config(
        {"proactive": {"enabled": True, "max_per_day": 2}}
    )

    assert settings.enabled is True
    assert settings.max_per_day == 2
    assert settings.cooldown_seconds > 0          # default kept


def test_settings_from_missing_config_is_disabled():

    assert ProactiveSettings.from_config(None).enabled is False
    assert ProactiveSettings.from_config({}).enabled is False


def test_malformed_category_cooldowns_are_skipped():

    settings = ProactiveSettings.from_config(
        {"proactive": {"category_cooldown_seconds": {"greeting": "soon"}}}
    )

    assert settings.category_cooldown_seconds["greeting"] > 0


# ======================================================================
# Message composition
# ======================================================================

@pytest.fixture
def composer():
    return MessageComposer()


def test_a_silent_decision_composes_nothing(composer):

    assert composer.compose(ProactiveDecision.silent("no"), context_at()) == ""


def test_greetings_match_the_part_of_day(composer):

    morning = composer.compose(
        ProactiveDecision(send=True, reason="", category=Category.GREETING.value),
        context_at(now=datetime(2026, 8, 11, 8, 0)),
    )
    evening = composer.compose(
        ProactiveDecision(send=True, reason="", category=Category.GREETING.value),
        context_at(now=datetime(2026, 8, 11, 19, 0)),
    )

    assert "Morning" in morning
    assert "Evening" in evening


def test_greetings_rotate_so_they_do_not_read_like_a_recording(composer):

    decision = ProactiveDecision(
        send=True, reason="", category=Category.GREETING.value
    )

    first = composer.compose(decision, context_at(), rotation=0)
    second = composer.compose(decision, context_at(), rotation=1)

    assert first != second


def test_composition_is_deterministic(composer):
    """Same context and rotation, same words. Testable, not spontaneous."""

    decision = ProactiveDecision(
        send=True, reason="", category=Category.GREETING.value
    )

    assert composer.compose(decision, context_at(), 3) == composer.compose(
        decision, context_at(), 3
    )


def test_a_task_reminder_without_a_task_says_nothing(composer):
    """The guard that makes an invented reminder impossible."""

    decision = ProactiveDecision(
        send=True, reason="", category=Category.TASK.value, detail=""
    )

    assert composer.compose(decision, context_at()) == ""


def test_an_appreciation_without_a_referent_says_nothing(composer):

    decision = ProactiveDecision(
        send=True, reason="", category=Category.APPRECIATION.value, detail=""
    )

    assert composer.compose(decision, context_at()) == ""


def test_a_task_reminder_quotes_the_users_own_words(composer):

    decision = ProactiveDecision(
        send=True,
        reason="",
        category=Category.TASK.value,
        detail="I'm going to rewrite the retriever",
    )

    message = composer.compose(decision, context_at())

    assert "rewrite the retriever" in message


def test_an_unknown_category_composes_nothing(composer):

    decision = ProactiveDecision(send=True, reason="", category="nonsense")

    assert composer.compose(decision, context_at()) == ""


# ======================================================================
# The engine
# ======================================================================

def engine_at(now=NOW, enabled=True, **kwargs) -> tuple:

    events = Recorder()

    clock = TemporalClock(now=lambda: now)

    engine = ProactiveEngine(
        policy=ProactivePolicy(
            settings=ProactiveSettings(enabled=enabled), clock=clock.now
        ),
        clock=clock,
        events=events,
        **kwargs,
    )

    return engine, events


def test_a_tick_with_nothing_to_say_publishes_nothing():

    engine, events = engine_at()
    engine.note_chat()

    decision = engine.tick()

    assert decision.send is False
    assert events.events == []


def test_ticking_repeatedly_changes_nothing():
    """A trigger is an invitation to consider, not a reason to speak."""

    engine, events = engine_at()
    engine.note_chat()

    for _ in range(100):
        engine.tick()

    assert events.events == []


def test_a_greeting_is_published_to_the_existing_transport():

    engine, events = engine_at()

    engine._last_user_message_at = NOW - timedelta(hours=9)

    decision = engine.tick()

    assert decision.send is True
    assert len(events.events) == 1

    event = events.events[0]

    assert isinstance(event, CompanionNotificationEvent)
    assert event.source == "proactive"
    assert event.message


def test_the_second_tick_is_blocked_by_the_gate():
    """A good reason must not override the anti-spam rules."""

    engine, events = engine_at()

    engine._last_user_message_at = NOW - timedelta(hours=9)

    assert engine.tick().send is True

    second = engine.tick()

    assert second.send is False
    assert len(events.events) == 1


def test_a_disabled_engine_never_publishes():

    engine, events = engine_at(enabled=False)

    engine._last_user_message_at = NOW - timedelta(hours=9)

    decision = engine.tick()

    assert decision.send is False
    assert events.events == []


def test_no_task_source_means_no_task_reminders_ever():
    """Not a gap. The correct behaviour when nothing is known."""

    engine, _events = engine_at()

    engine._last_user_message_at = NOW - timedelta(hours=9)

    assert engine.build_context().pending_tasks == ()
    assert engine.tick().category != Category.TASK.value


def test_a_broken_task_source_produces_silence_not_a_guess():

    def exploding():
        raise RuntimeError("database is gone")

    engine, events = engine_at(pending_tasks=exploding)

    engine._last_user_message_at = NOW - timedelta(hours=9)

    decision = engine.tick()

    assert decision.category != Category.TASK.value
    assert all("task" not in e.reason for e in events.events)


def test_a_broken_memory_source_does_not_raise():

    def exploding():
        raise RuntimeError("nope")

    engine, _events = engine_at(memories=exploding)

    assert engine.build_context().relevant_memories == ()


def test_a_task_source_that_returns_junk_is_ignored():
    """Only real PendingTask objects count."""

    engine, _events = engine_at(pending_tasks=lambda: ["a string", None, 42])

    assert engine.build_context().pending_tasks == ()


def test_note_chat_suppresses_the_next_tick():

    engine, events = engine_at()

    engine._last_user_message_at = NOW - timedelta(hours=9)
    engine.note_chat()

    assert engine.tick().send is False
    assert events.events == []


def test_the_greeting_is_not_repeated_in_the_same_part_of_day():

    ticks = {"now": NOW}

    events = Recorder()
    clock = TemporalClock(now=lambda: ticks["now"])

    engine = ProactiveEngine(
        policy=ProactivePolicy(
            settings=ProactiveSettings(
                enabled=True, cooldown_seconds=0, category_cooldown_seconds={}
            ),
            clock=clock.now,
        ),
        clock=clock,
        events=events,
    )

    engine._last_user_message_at = NOW - timedelta(hours=9)

    assert engine.tick().send is True

    # Same afternoon, one hour later.
    ticks["now"] = NOW + timedelta(hours=1)
    engine._last_user_message_at = ticks["now"] - timedelta(hours=9)

    assert engine.tick().category != Category.GREETING.value


def test_publishing_is_not_delivery():
    """
    The honesty boundary. A decision to send means a message reached the
    outbox; it says nothing about a phone having shown it, and nothing in
    the decision may claim otherwise.
    """

    engine, events = engine_at()

    engine._last_user_message_at = NOW - timedelta(hours=9)

    decision = engine.tick()

    assert decision.send is True
    assert "delivered" not in decision.reason.lower()
    assert "delivered" not in decision.detail.lower()


def test_an_engine_with_no_bus_does_not_raise():
    """Publishing is optional; deciding is not."""

    clock = TemporalClock(now=lambda: NOW)

    engine = ProactiveEngine(
        policy=ProactivePolicy(
            settings=ProactiveSettings(enabled=True), clock=clock.now
        ),
        clock=clock,
    )

    engine._last_user_message_at = NOW - timedelta(hours=9)

    assert engine.tick().send is True


def test_build_proactive_engine_is_disabled_without_config():

    engine = build_proactive_engine(None)

    engine._last_user_message_at = NOW - timedelta(hours=9)

    assert engine.tick().send is False


def test_build_proactive_engine_reads_config():

    engine = build_proactive_engine(
        {"proactive": {"enabled": True, "max_per_day": 1}},
        clock=TemporalClock(now=lambda: NOW),
    )

    assert engine.policy.settings.enabled is True
    assert engine.policy.settings.max_per_day == 1


# ======================================================================
# Pending tasks - never invented
# ======================================================================

@pytest.fixture
def store(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from memory.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield EpisodicStore(session=session)
    session.close()


def test_no_memories_means_no_pending_tasks(store):

    assert EpisodicTaskSource(store, clock=lambda: NOW).pending() == []


def test_only_plans_become_pending_tasks(store):

    store.remember(
        "I ate a sandwich", category="event", occurred_at=NOW - timedelta(days=1)
    )

    assert EpisodicTaskSource(store, clock=lambda: NOW).pending() == []


def test_an_unfinished_plan_is_pending(store):

    store.remember(
        "I'm going to rewrite the retriever",
        category="plan",
        occurred_at=NOW - timedelta(days=2),
    )

    tasks = EpisodicTaskSource(store, clock=lambda: NOW).pending()

    assert len(tasks) == 1
    assert "retriever" in tasks[0].description
    assert tasks[0].source == "episodic"


def test_a_completed_plan_is_not_pending(store):
    """The user said they finished it. Reminding them is nagging."""

    store.remember(
        "I'm going to rewrite the retriever",
        category="plan",
        occurred_at=NOW - timedelta(days=3),
    )
    store.remember(
        "I finished the retriever rewrite",
        category="event",
        occurred_at=NOW - timedelta(days=1),
    )

    assert EpisodicTaskSource(store, clock=lambda: NOW).pending() == []


def test_a_completion_before_the_plan_does_not_count(store):
    """Finishing it on Monday does not complete a plan made on Tuesday."""

    store.remember(
        "I finished the retriever rewrite",
        category="event",
        occurred_at=NOW - timedelta(days=5),
    )
    store.remember(
        "I'm going to rewrite the retriever",
        category="plan",
        occurred_at=NOW - timedelta(days=2),
    )

    assert len(EpisodicTaskSource(store, clock=lambda: NOW).pending()) == 1


def test_a_stale_plan_is_dropped(store):
    """A reminder about something from six weeks ago is an accusation."""

    store.remember(
        "I'm going to rewrite the retriever",
        category="plan",
        occurred_at=NOW - timedelta(days=60),
    )

    assert EpisodicTaskSource(store, clock=lambda: NOW).pending() == []


def test_a_just_mentioned_plan_is_not_chased(store):

    store.remember(
        "I'm going to rewrite the retriever",
        category="plan",
        occurred_at=NOW - timedelta(minutes=30),
    )

    assert EpisodicTaskSource(store, clock=lambda: NOW).pending() == []


def test_trivial_plans_are_not_tasks(store):

    store.remember(
        "I'm going to sleep", category="plan", occurred_at=NOW - timedelta(days=1)
    )

    assert EpisodicTaskSource(store, clock=lambda: NOW).pending() == []


def test_pending_tasks_are_bounded(store):

    for index in range(10):
        store.remember(
            f"I'm going to refactor module {index}",
            category="plan",
            occurred_at=NOW - timedelta(days=1),
        )

    assert len(EpisodicTaskSource(store, clock=lambda: NOW, limit=3).pending()) == 3


def test_the_task_source_is_callable_for_the_engine(store):

    store.remember(
        "I'm going to rewrite the retriever",
        category="plan",
        occurred_at=NOW - timedelta(days=2),
    )

    source = EpisodicTaskSource(store, clock=lambda: NOW)

    assert source() == source.pending()


def test_end_to_end_a_real_task_becomes_a_real_reminder(store):
    """
    The whole chain: a plan the user stated, surviving selection, dating,
    the task source, the decision engine, the composer and the gate.
    """

    store.remember(
        "I'm going to rewrite the retriever",
        category="plan",
        occurred_at=NOW - timedelta(days=2),
    )

    events = Recorder()
    clock = TemporalClock(now=lambda: NOW)

    engine = ProactiveEngine(
        policy=ProactivePolicy(
            settings=ProactiveSettings(enabled=True), clock=clock.now
        ),
        clock=clock,
        events=events,
        pending_tasks=EpisodicTaskSource(store, clock=clock.now),
    )

    engine._last_user_message_at = NOW - timedelta(hours=9)

    decision = engine.tick()

    assert decision.send is True
    assert decision.category == Category.TASK.value
    assert "rewrite the retriever" in events.events[0].message
