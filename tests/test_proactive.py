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
from proactive.memories import EpisodicMemorySource
from proactive.messages import MessageComposer
from proactive.policy import (
    DEFAULT_CATEGORY_COOLDOWN,
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


def test_build_proactive_engine_takes_a_ledger(tmp_path):
    """
    The helper passes a ledger through to the policy rather than
    inventing one. `core/app.py` states the reason for this shape: a
    dependency is handed in from a composition root so that a bare
    object touches nothing a test did not ask for.
    """

    from proactive.ledger import SendLedger

    ledger = SendLedger(path=tmp_path / "proactive.json")

    engine = build_proactive_engine(
        {"proactive": {"enabled": True}},
        clock=TemporalClock(now=lambda: NOW),
        ledger=ledger,
    )

    assert engine.policy.ledger is ledger


def test_build_proactive_engine_without_a_ledger_touches_no_files(tmp_path):
    """
    The other half of the same shape, and the reason the default is None:
    the two tests above build an engine from config and must not read or
    write the real data directory to do it.
    """

    engine = build_proactive_engine(
        {"proactive": {"enabled": True}},
        clock=TemporalClock(now=lambda: NOW),
    )

    assert engine.policy.ledger is None

    engine.tick()

    assert list(tmp_path.iterdir()) == []


def test_the_server_gives_the_proactive_engine_a_durable_ledger():
    """
    Wiring, which is the part that decides whether any of this exists in
    production. `core/app.py` records what happens when a faculty arrives
    from a composition root and one root forgets it: the code is present,
    the tests pass, and the running application does not have it. Here
    that would mean every owner-configured limit silently resetting on
    each restart while this suite stayed green.

    Asserted at the path rather than by sending a message, because
    sending one would write to the real data directory from a test.
    """

    from core.paths import DATA_DIR
    from launcher.services import build_services

    services = build_services({"proactive": {"enabled": False}})

    ledger = services.proactive.policy.ledger

    assert ledger is not None
    assert ledger.path.parent == DATA_DIR


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


# ----------------------------------------------------------------------
# Surviving a restart (section 19)
# ----------------------------------------------------------------------
#
# Everything the owner configured about *how often* Aura may speak was
# enforced from a `deque` in process memory. `max_per_day`,
# `cooldown_seconds`, `category_cooldown_seconds`, the duplicate window
# and the similarity threshold all read `ProactivePolicy._sent`, and the
# engine kept a second copy of "have I greeted them this morning" in its
# own dict. None of it survived the process.
#
# So the owner sets "no more than four a day", Aura sends four, the
# server restarts - a crash, a reboot, a reload, a closed laptop - and
# the ledger is empty. Four more are allowed the same day. Section 20
# says do not spam notifications; section 2 says AURA must not silently
# override owner configuration, and a limit that resets itself is
# exactly the owner's configuration not being honoured.
#
# The tests below are about the guarantee, not the file format: each one
# builds a policy, throws it away, builds another over the same ledger,
# and asks whether the limit still holds.


class TestTheProactiveLimitsSurviveARestart:
    """
    Every test here used the category `"check_in"`, which is not in
    `Category` and never has been. That was not deliberate: it was these
    tests leaning on the defect above, because an unlisted category had
    no per-category cooldown and so left whichever rule the test was
    about as the only one standing.

    Now that an unknown category is refused outright, they use a real
    one - and stand the per-category cooldown down explicitly, so the
    rule under test is still the only rule that can answer. Several of
    them discard the reason, and a test that asserts "not allowed" while
    a different rule than the named one is doing the refusing is a test
    that passes for the wrong reason.
    """

    CATEGORY = Category.WELLBEING.value

    def ledger(self, tmp_path):
        from proactive.ledger import SendLedger

        return SendLedger(path=tmp_path / "proactive.json")

    def test_the_daily_limit_is_still_reached_after_a_restart(self, tmp_path):
        """
        The defect, stated as the owner would experience it.
        """

        ledger = self.ledger(tmp_path)
        settings = ProactiveSettings(
            enabled=True,
            max_per_day=2,
            cooldown_seconds=0,
            category_cooldown_seconds={self.CATEGORY: 0},
        )
        moment = datetime(2026, 8, 24, 14, 0)

        before = ProactivePolicy(
            settings=settings, clock=lambda: moment, ledger=ledger
        )

        before.note_sent("greeting", "hello")
        before.note_sent(self.CATEGORY, "how goes it")

        allowed, reason = before.allows(self.CATEGORY, "again")
        assert not allowed and "daily limit" in reason

        # The process dies here. A new policy over the same ledger.
        after = ProactivePolicy(
            settings=settings, clock=lambda: moment, ledger=self.ledger(tmp_path)
        )

        allowed, reason = after.allows(self.CATEGORY, "again")

        assert not allowed
        assert "daily limit" in reason

    def test_the_cooldown_is_still_running_after_a_restart(self, tmp_path):
        settings = ProactiveSettings(
            enabled=True,
            max_per_day=99,
            cooldown_seconds=600,
            category_cooldown_seconds={self.CATEGORY: 0},
        )
        moment = datetime(2026, 8, 24, 14, 0)

        before = ProactivePolicy(
            settings=settings, clock=lambda: moment, ledger=self.ledger(tmp_path)
        )
        before.note_sent(self.CATEGORY, "how goes it")

        after = ProactivePolicy(
            settings=settings,
            clock=lambda: moment + timedelta(seconds=60),
            ledger=self.ledger(tmp_path),
        )

        allowed, reason = after.allows(self.CATEGORY, "something else")

        assert not allowed
        assert "cooldown" in reason

    def test_a_repeat_is_still_a_repeat_after_a_restart(self, tmp_path):
        """
        The duplicate window needs the message text, not just its time.
        """

        settings = ProactiveSettings(
            enabled=True, max_per_day=99, cooldown_seconds=0,
            duplicate_window_seconds=3600,
            category_cooldown_seconds={self.CATEGORY: 0},
        )
        moment = datetime(2026, 8, 24, 14, 0)

        before = ProactivePolicy(
            settings=settings, clock=lambda: moment, ledger=self.ledger(tmp_path)
        )
        before.note_sent(self.CATEGORY, "How is the migration going?")

        after = ProactivePolicy(
            settings=settings,
            clock=lambda: moment + timedelta(seconds=30),
            ledger=self.ledger(tmp_path),
        )

        allowed, _reason = after.allows(
            self.CATEGORY, "How is the migration going?"
        )

        assert not allowed

    def test_a_fresh_ledger_allows_the_first_message(self, tmp_path):
        """
        The other direction, so none of this is satisfied by a policy
        that simply refuses everything.
        """

        settings = ProactiveSettings(
            enabled=True,
            max_per_day=2,
            cooldown_seconds=0,
            category_cooldown_seconds={self.CATEGORY: 0},
        )

        policy = ProactivePolicy(
            settings=settings,
            clock=lambda: datetime(2026, 8, 24, 14, 0),
            ledger=self.ledger(tmp_path),
        )

        allowed, _reason = policy.allows(self.CATEGORY, "how goes it")

        assert allowed

    def test_yesterdays_messages_do_not_count_against_today(self, tmp_path):
        """
        A persisted ledger must not turn into a permanent ban. The daily
        limit is a question about the calendar, and the stored timestamps
        have to keep their dates for it to stay one.
        """

        settings = ProactiveSettings(
            enabled=True,
            max_per_day=1,
            cooldown_seconds=0,
            category_cooldown_seconds={self.CATEGORY: 0},
        )
        yesterday = datetime(2026, 8, 23, 14, 0)

        before = ProactivePolicy(
            settings=settings, clock=lambda: yesterday, ledger=self.ledger(tmp_path)
        )
        before.note_sent(self.CATEGORY, "how goes it")

        after = ProactivePolicy(
            settings=settings,
            clock=lambda: datetime(2026, 8, 24, 9, 0),
            ledger=self.ledger(tmp_path),
        )

        allowed, _reason = after.allows(
            self.CATEGORY, "how goes it today"
        )

        assert allowed

    def test_reset_clears_the_stored_ledger_too(self, tmp_path):
        """
        `reset` is the owner's own escape hatch. If it cleared only the
        copy in memory, the next start would restore the limit it had
        just been asked to drop - the configuration silently outliving
        the owner's instruction, which is what section 2 forbids.
        """

        settings = ProactiveSettings(
            enabled=True,
            max_per_day=1,
            cooldown_seconds=0,
            category_cooldown_seconds={self.CATEGORY: 0},
        )
        moment = datetime(2026, 8, 24, 14, 0)

        before = ProactivePolicy(
            settings=settings, clock=lambda: moment, ledger=self.ledger(tmp_path)
        )
        before.note_sent(self.CATEGORY, "how goes it")
        before.reset()

        after = ProactivePolicy(
            settings=settings, clock=lambda: moment, ledger=self.ledger(tmp_path)
        )

        allowed, _reason = after.allows(self.CATEGORY, "how goes it")

        assert allowed

    def test_a_policy_with_no_ledger_behaves_exactly_as_before(self, tmp_path):
        """
        The default has to be the old behaviour, or every existing caller
        and every existing test is a migration.
        """

        settings = ProactiveSettings(
            enabled=True,
            max_per_day=1,
            cooldown_seconds=0,
            category_cooldown_seconds={self.CATEGORY: 0},
        )
        moment = datetime(2026, 8, 24, 14, 0)

        policy = ProactivePolicy(settings=settings, clock=lambda: moment)
        policy.note_sent(self.CATEGORY, "how goes it")

        allowed, reason = policy.allows(self.CATEGORY, "again")

        assert not allowed and "daily limit" in reason

        # And nothing was written anywhere.
        assert list(tmp_path.iterdir()) == []


# ----------------------------------------------------------------------
# Greeting twice because the process restarted (sections 8 and 19)
# ----------------------------------------------------------------------
#
# `ProactiveEngine` kept its own `_greeted` dict - date to set of parts
# of day - so that it would not say good morning twice in one morning.
# That dict was a second copy of something the policy's history already
# records: a send in the GREETING category, with the time it happened.
# `core/temporal.part_of_day` is a pure function of any datetime, so
# "which part of which day was that greeting in" is a question the
# history can already answer.
#
# Section 8 says do not duplicate independent versions of this state
# across modules, and the duplicate is the copy that dies with the
# process. So the two problems are one problem: derive the answer from
# the ledger and the restart stops re-greeting, because there is no
# longer a separate thing to lose.
# ----------------------------------------------------------------------


class TestGreetingsSurviveARestart:

    def ledger(self, tmp_path):
        from proactive.ledger import SendLedger

        return SendLedger(path=tmp_path / "proactive.json")

    def engine(self, ledger, now=NOW):
        """An engine and its policy, over a given ledger, at a given time."""

        clock = TemporalClock(now=lambda: now)

        return ProactiveEngine(
            policy=ProactivePolicy(
                settings=ProactiveSettings(enabled=True),
                clock=clock.now,
                ledger=ledger,
            ),
            clock=clock,
            events=Recorder(),
        )

    def test_a_greeting_is_not_repeated_after_a_restart(self, tmp_path):
        """
        The defect, stated as behaviour: Aura says good afternoon, the
        server restarts, and she says good afternoon again.

        The assertion is on the context rather than on the second tick,
        because a second tick would be refused by the two-hour global
        cooldown as well - and a test that passes for the wrong reason
        would keep passing after the derivation was removed again.
        """

        ledger = self.ledger(tmp_path)

        first = self.engine(ledger)
        sent = first.tick()

        assert sent.send is True
        assert sent.category == Category.GREETING.value
        assert first.build_context().greeted_this_part is True

        # The process ends here. Nothing is carried over but the file.
        after = self.engine(self.ledger(tmp_path))

        assert after.build_context().greeted_this_part is True

    def test_the_greeting_itself_is_not_reconsidered_after_a_restart(
        self, tmp_path
    ):
        """
        The same fact one layer down, past the policy's cooldowns:
        the decision function itself must no longer choose a greeting.
        """

        ledger = self.ledger(tmp_path)

        self.engine(ledger).tick()

        after = self.engine(self.ledger(tmp_path))

        decision = should_proactively_message(after.build_context())

        assert decision.category != Category.GREETING.value

    def test_a_fresh_ledger_has_greeted_nobody(self, tmp_path):
        """The other direction, so the derivation cannot just return True."""

        engine = self.engine(self.ledger(tmp_path))

        assert engine.build_context().greeted_this_part is False

    def test_a_greeting_this_morning_does_not_cover_this_afternoon(
        self, tmp_path
    ):
        """
        Part of day, not merely date.

        A greeting at 08:00 and a tick at 14:00 are the same day, so a
        derivation that only compared dates would suppress the afternoon
        greeting - and the engine's own dict was keyed by part of day for
        exactly this reason. Losing that distinction while fixing the
        restart would be trading one defect for another.
        """

        morning = NOW.replace(hour=8, minute=0)

        self.engine(self.ledger(tmp_path), now=morning).tick()

        afternoon = self.engine(self.ledger(tmp_path), now=NOW)

        assert afternoon.build_context().greeted_this_part is False

    def test_yesterdays_greeting_does_not_cover_today(self, tmp_path):
        """
        Same part of day, different day. Without the date half of the
        comparison, one greeting would silence every afternoon after it.
        """

        yesterday = NOW - timedelta(days=1)

        self.engine(self.ledger(tmp_path), now=yesterday).tick()

        today = self.engine(self.ledger(tmp_path), now=NOW)

        assert today.build_context().greeted_this_part is False

    def test_a_non_greeting_send_does_not_count_as_a_greeting(self, tmp_path):
        """
        The category matters. A task reminder at 14:05 is not a hello,
        and a derivation that looked only at times would treat it as one.
        """

        ledger = self.ledger(tmp_path)

        policy = ProactivePolicy(
            settings=ProactiveSettings(enabled=True),
            clock=lambda: NOW,
            ledger=ledger,
        )
        policy.note_sent(Category.TASK.value, "the invoice is due")

        engine = self.engine(self.ledger(tmp_path))

        assert engine.build_context().greeted_this_part is False

    def test_an_engine_with_no_ledger_still_remembers_within_the_process(
        self, tmp_path
    ):
        """
        The derivation must not depend on there being a file. A bare
        engine - which is what most of this suite builds, and what the
        CLI builds - still has a policy history in memory, and that is
        the same history the derivation reads. So the guarantee that
        predates this phase has to survive it.
        """

        engine = self.engine(ledger=None)

        assert engine.build_context().greeted_this_part is False

        assert engine.tick().category == Category.GREETING.value

        assert engine.build_context().greeted_this_part is True

        assert list(tmp_path.iterdir()) == []


# ----------------------------------------------------------------------
# The file itself
# ----------------------------------------------------------------------
#
# `data/proactive.json` sits in a directory the owner can open, next to
# the settings file they already edit, so "what if it has been changed by
# hand" is a real question rather than a defensive one. Section 41 says
# do not destroy existing data, which decides the answer: read what can
# be read, ignore what cannot, and never rewrite the file just because
# part of it was unrecognisable.
# ----------------------------------------------------------------------


class TestReadingTheLedgerFile:

    def ledger(self, tmp_path):
        from proactive.ledger import SendLedger

        return SendLedger(path=tmp_path / "proactive.json")

    def write(self, tmp_path, text):
        target = tmp_path / "proactive.json"
        target.write_text(text, encoding="utf-8")
        return target

    def test_a_missing_file_is_simply_no_history(self, tmp_path):

        assert self.ledger(tmp_path).load() == ()

    def test_an_unreadable_file_is_ignored_rather_than_fatal(self, tmp_path):
        """
        A truncated write, a half-synced file, an owner who deleted one
        too many commas. Aura still has to start. The file is left alone
        rather than replaced with a valid empty one, because a file that
        cannot be parsed today might still be wanted tomorrow.
        """

        target = self.write(tmp_path, '{"version": 1, "sends": [')

        assert self.ledger(tmp_path).load() == ()

        assert target.read_text(encoding="utf-8") == '{"version": 1, "sends": ['

    def test_a_file_that_is_not_an_object_is_ignored(self, tmp_path):

        self.write(tmp_path, '["not", "a", "document"]')

        assert self.ledger(tmp_path).load() == ()

    def test_sends_that_is_not_a_list_is_ignored(self, tmp_path):

        self.write(tmp_path, '{"version": 1, "sends": "four"}')

        assert self.ledger(tmp_path).load() == ()

    def test_one_bad_row_does_not_lose_the_good_ones(self, tmp_path):
        """
        Per-row validation, the same shape `core/settings_store.py` uses
        for its overrides: an unusable entry is dropped and the rest are
        kept. Discarding the whole history over one bad row would hand
        back exactly the clean slate this phase exists to prevent.
        """

        self.write(
            tmp_path,
            """{"version": 1, "sends": [
                {"at": "2026-08-24T09:00:00", "category": "greeting",
                 "message": "morning"},
                {"at": "not a time", "category": "greeting", "message": "x"},
                {"category": "greeting", "message": "no time at all"},
                {"at": "2026-08-24T10:00:00", "category": 7, "message": "x"},
                {"at": "2026-08-24T11:00:00", "category": "task",
                 "message": 12},
                "not even a row",
                {"at": "2026-08-24T12:00:00", "category": "wellbeing",
                 "message": "afternoon"}
            ]}""",
        )

        history = self.ledger(tmp_path).load()

        assert [entry[2] for entry in history] == ["morning", "afternoon"]

    def test_history_comes_back_oldest_first(self, tmp_path):
        """
        The order is load-bearing, not cosmetic: `last_sent_at` and
        `recent_messages` both walk the history in reverse to find the
        most recent thing said, so a file in the other order would make
        every cooldown answer from the oldest send instead of the newest.

        Aura's own writes are already in order, so this is about a file
        that has been edited or assembled by hand - which is a thing that
        can happen to a JSON file in a directory the owner can open.
        """

        self.write(
            tmp_path,
            """{"version": 1, "sends": [
                {"at": "2026-08-24T18:00:00", "category": "wellbeing",
                 "message": "evening"},
                {"at": "2026-08-24T08:00:00", "category": "greeting",
                 "message": "morning"}
            ]}""",
        )

        history = self.ledger(tmp_path).load()

        assert [entry[2] for entry in history] == ["morning", "evening"]

        policy = ProactivePolicy(
            settings=ProactiveSettings(enabled=True),
            clock=lambda: datetime(2026, 8, 24, 20, 0),
            ledger=self.ledger(tmp_path),
        )

        assert policy.last_sent_at() == datetime(2026, 8, 24, 18, 0)
        assert policy.recent_messages()[0] == "evening"

    def test_a_write_leaves_no_temporary_file_behind(self, tmp_path):
        """
        The write is atomic - `.tmp` then `os.replace` - copied from
        `core/settings_store.py`, so that a start reading this file can
        never see half of one. What is checked here is that the temporary
        half does not survive as litter in the owner's data directory.
        """

        policy = ProactivePolicy(
            settings=ProactiveSettings(enabled=True),
            clock=lambda: NOW,
            ledger=self.ledger(tmp_path),
        )
        policy.note_sent("wellbeing", "something worth saying")

        assert sorted(item.name for item in tmp_path.iterdir()) == [
            "proactive.json"
        ]

    def test_a_write_into_a_directory_that_does_not_exist_still_works(
        self, tmp_path
    ):
        """
        On a first run `data/` may not be there yet. A cap that cannot be
        recorded because a directory is missing is a cap that is not
        enforced after the next restart.
        """

        from proactive.ledger import SendLedger

        nested = tmp_path / "data" / "proactive.json"

        policy = ProactivePolicy(
            settings=ProactiveSettings(enabled=True),
            clock=lambda: NOW,
            ledger=SendLedger(path=nested),
        )
        policy.note_sent("wellbeing", "something worth saying")

        assert SendLedger(path=nested).load()[0][2] == "something worth saying"

    def test_a_write_that_cannot_happen_does_not_stop_the_message(
        self, tmp_path
    ):
        """
        The failure the owner would rather have. If the ledger cannot be
        written - a full disk, a permission, a path that is a directory -
        the consequence should be a limit forgotten at the next start, not
        a message the owner allowed going undelivered now.
        """

        from proactive.ledger import SendLedger

        occupied = tmp_path / "proactive.json"
        occupied.mkdir()

        policy = ProactivePolicy(
            settings=ProactiveSettings(enabled=True, max_per_day=2),
            clock=lambda: NOW,
            ledger=SendLedger(path=occupied),
        )

        policy.note_sent("wellbeing", "said anyway")

        # In memory the limit still counts, which is what matters while
        # the process is alive.
        assert policy.sent_today() == 1


# ----------------------------------------------------------------------
# Greeting somebody who never left (sections 8, 19 and 21)
# ----------------------------------------------------------------------
#
# Making the send history durable exposed the other half of the same
# problem. `_last_user_message_at` is set by `note_chat()` and lived only
# in the process, and `seconds_since_user()` reads a missing value as
# infinity - which the greeting rule reads as "they have been away".
#
# So the owner is mid-conversation, the server restarts, and Aura opens
# with a welcome-back to somebody who was talking to her a minute ago.
# Nothing about that is a guess or a hypothetical: the probe that found
# it is the test below. Section 21 says AURA must not silently perform
# arbitrary high-impact actions merely because it detected an event, and
# an unprompted message triggered by *forgetting* is the weakest possible
# justification for one.
#
# The fix is the same shape as the greeting fix: stop keeping a private
# copy of something already recorded. Every real chat turn writes a row
# to the `messages` table, so "when did the user last speak" is already
# on disk - and section 8 says not to hold a second independent version
# of it. The in-process value stays as the freshest answer while the
# process lives; the durable source answers when it cannot.
# ----------------------------------------------------------------------


class TestPresenceSurvivesARestart:

    def engine(self, last_user_message=None, now=NOW):

        clock = TemporalClock(now=lambda: now)

        return ProactiveEngine(
            policy=ProactivePolicy(
                settings=ProactiveSettings(enabled=True), clock=clock.now
            ),
            clock=clock,
            events=Recorder(),
            last_user_message=last_user_message,
        )

    def test_a_restart_does_not_greet_somebody_who_just_spoke(self):
        """
        The defect, as behaviour. The user spoke a minute ago; the process
        forgot; without a durable answer Aura says hello.
        """

        spoke = NOW - timedelta(minutes=1)

        engine = self.engine(last_user_message=lambda: spoke)

        assert engine.build_context().seconds_since_user() == 60.0

        assert engine.tick().category != Category.GREETING.value

    def test_a_restart_still_greets_somebody_who_really_was_away(self):
        """
        The other direction, and the reason this is not simply suppressed:
        a greeting after a real absence is the feature. A durable answer
        has to be able to say "yes, they were gone" as well as "no".
        """

        spoke = NOW - timedelta(hours=9)

        engine = self.engine(last_user_message=lambda: spoke)

        assert engine.tick().category == Category.GREETING.value

    def test_the_live_signal_still_wins_while_the_process_is_alive(self):
        """
        `note_chat()` is called the moment a request arrives, before the
        reply has been written anywhere. It stays the freshest answer, and
        a durable source that is older must not talk over it.
        """

        yesterday = NOW - timedelta(hours=20)

        engine = self.engine(last_user_message=lambda: yesterday)
        engine.note_chat()

        assert engine.build_context().seconds_since_user() == 0.0

        assert engine.tick().category != Category.GREETING.value

    def test_no_source_behaves_exactly_as_before(self):
        """
        A bare engine - which is what the CLI and most of this suite build
        - keeps the old reading, infinity, and the old consequence. This
        phase adds a source; it does not change what happens when there
        isn't one.
        """

        engine = self.engine()

        assert engine.build_context().seconds_since_user() == float("inf")

    def test_a_source_that_fails_is_a_source_with_nothing_to_say(self):
        """
        Same rule the task and memory sources already follow. A database
        that cannot be read must not turn into a claim about presence in
        either direction - it falls back to not knowing.
        """

        def broken():
            raise RuntimeError("the database is locked")

        engine = self.engine(last_user_message=broken)

        assert engine.build_context().seconds_since_user() == float("inf")

    def test_a_source_returning_nonsense_is_ignored(self):
        """
        Sources are outside code. A string where a datetime was expected
        would otherwise reach `seconds_since_user()` and raise there,
        turning a bad row into a failed tick.
        """

        engine = self.engine(last_user_message=lambda: "yesterday, i think")

        assert engine.build_context().seconds_since_user() == float("inf")

    def test_the_server_gives_the_proactive_engine_a_presence_source(self):
        """
        Wiring again, and the same reason as the ledger: a faculty that
        arrives from a composition root either arrives or silently does
        not exist. Read through the real memory manager so this fails if
        the query stops answering, not merely if the callable is absent.
        """

        from launcher.services import build_services

        services = build_services({"proactive": {"enabled": False}})

        source = services.proactive.last_user_message

        assert source is not None
        assert callable(source)

        # It answers without raising against the real store. What it
        # returns depends on this machine's history, so the assertion is
        # on the type rather than the value.
        answer = source()

        assert answer is None or isinstance(answer, datetime)


class TestTheMessageStoreKnowsWhenSomebodySpoke:
    """
    The query the presence source is built on. Kept separate because it
    belongs to `MemoryManager`, which owns the `messages` table - the
    proactive package does not reach into it.
    """

    def manager(self, tmp_path):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from memory.manager import MemoryManager
        from memory.models import Base

        engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
        Base.metadata.create_all(engine)

        return MemoryManager(session=sessionmaker(bind=engine)())

    def test_an_empty_store_knows_nothing(self, tmp_path):

        assert self.manager(tmp_path).last_said_at() is None

    def test_it_finds_the_users_most_recent_turn(self, tmp_path):

        memory = self.manager(tmp_path)

        memory.save("user", "morning")
        memory.save("assistant", "morning!")

        found = memory.last_said_at()

        assert isinstance(found, datetime)

    def test_aura_talking_to_herself_is_not_the_user_speaking(self, tmp_path):
        """
        The role filter, which is the whole point. Aura's own replies
        outnumber the user's turns, and counting one as presence would
        mean a proactive message suppressing the next one by pretending
        the owner had just spoken.
        """

        memory = self.manager(tmp_path)

        memory.save("assistant", "are you still there?")

        assert memory.last_said_at() is None
        assert memory.last_said_at("assistant") is not None

    def test_it_finds_the_owner_whichever_client_they_used(self, tmp_path):
        """
        The defect this was written to catch, and it was live for a while:
        `session_id` defaulted to "default", and the Android install
        supplies its own id (`server/session.py` - "an Android install
        keeps one across launches"). So every message the owner had ever
        sent from their phone was invisible to this query, presence always
        answered None, and the wiring added for it was present and dead.

        Section 44 is the rule that catches this: audited and wired is not
        implemented. The probe that found it saved one row under a phone
        session and asked the default question.

        Not filtering is also the correct question rather than a
        workaround. `server/runtime.py` states the deployment - one
        person, one Aura, the auth token as the only identity boundary -
        and there is one proactive engine per process, not one per
        session. "Has the owner spoken lately" has no session in it.
        """

        memory = self.manager(tmp_path)

        memory.save("user", "sent from the phone", session_id="android-9f2c41")

        assert memory.last_said_at() is not None

    def test_a_session_can_still_be_named(self, tmp_path):
        """
        The narrower question stays available, because `messages` really is
        stored per session and a caller that wants one transcript should
        be able to ask for it.
        """

        memory = self.manager(tmp_path)

        memory.save("user", "sent from the phone", session_id="android-9f2c41")

        assert memory.last_said_at(session_id="android-9f2c41") is not None
        assert memory.last_said_at(session_id="default") is None

    def test_it_answers_with_the_most_recent_turn_not_the_first(
        self, tmp_path
    ):
        """
        Ordering, which no test pinned until a mutation swapped `desc()`
        for `asc()` and survived - every test so far had exactly one user
        row, and with one row the two are the same answer.

        With more than one the difference is the whole feature: a cooldown
        measured from the owner's *first* message would read a
        conversation that has been going all day as an absence since
        breakfast, and greet them mid-sentence.

        Rows are written directly here because `save` stamps its own
        timestamp at second precision, and two calls in the same second
        cannot be told apart by time.
        """

        from memory.models import Message

        memory = self.manager(tmp_path)

        for stamp in ("2026-08-24T08:00:00", "2026-08-24T17:30:00"):
            memory.session.add(
                Message(
                    session_id="default",
                    role="user",
                    content=f"said at {stamp}",
                    timestamp=stamp,
                )
            )
        memory.session.commit()

        assert memory.last_said_at() == datetime(2026, 8, 24, 17, 30)

    def test_a_row_with_an_unreadable_timestamp_is_not_evidence(
        self, tmp_path
    ):
        """
        None rather than a guess. A row that cannot be dated is not proof
        the owner is present, and inventing a time for it would either
        suppress a greeting they wanted or trigger one they did not.
        """

        from memory.models import Message

        memory = self.manager(tmp_path)

        memory.session.add(
            Message(
                session_id="default",
                role="user",
                content="when was this",
                timestamp="sometime last tuesday",
            )
        )
        memory.session.commit()

        assert memory.last_said_at() is None

    def test_it_looks_past_a_long_run_of_replies(self, tmp_path):
        """
        A scan of the last handful of rows would miss a user turn buried
        under enough assistant rows. The query filters rather than
        slicing, so depth does not matter.
        """

        memory = self.manager(tmp_path)

        memory.save("user", "the one that matters")

        for index in range(40):
            memory.save("assistant", f"reply {index}")

        assert memory.last_said_at() is not None


# ======================================================================
# Phase 15 - section 21
# ======================================================================
#
# Section 21's one binding sentence is: "AURA must not silently perform
# arbitrary high-impact actions merely because it detected an event."
#
# Three things follow from taking that literally, and each has a class
# below. The first two are defects found by probing this package rather
# than by reading it; the third is a property that is true today by
# accident and is written down here so that it stops being an accident.


class TestACategoryWithoutACooldownIsASpamRoute:
    """
    `Category` is a closed set and the module says why: "a category that
    is not listed here cannot be sent at all". But the *cooldown* table
    is a separate dict keyed by string, and `allows()` reads it with
    `.get(category)` and skips the whole per-category branch when the
    answer is falsy.

    So the closed set is closed at the decision layer and open at the
    policy layer, and the two do not check each other. A category added
    to one and not the other loses its per-category throttle silently -
    it does not error, it does not warn, it just sends.

    Probed before writing this: an unlisted category sent five distinct
    messages in five seconds through the real policy. A listed one sent
    one. That is the spam route the closed set exists to prevent.
    """

    def settings(self):
        """Gates that leave only the per-category cooldown standing."""

        return ProactiveSettings(
            enabled=True,
            cooldown_seconds=0.0,
            max_per_day=99,
            quiet_hours=[],
        )

    def send_five(self, category: str) -> int:
        """Five distinct messages, one second apart. How many got out?"""

        moment = [NOW]
        policy = ProactivePolicy(
            settings=self.settings(), clock=lambda: moment[0]
        )

        texts = (
            "The build on branch alpha finished.",
            "Your deployment to staging completed.",
            "A dependency upgrade is available now.",
            "The nightly index rebuild has ended.",
            "Disk usage on the data volume rose.",
        )

        sent = 0

        for text in texts:
            allowed, _reason = policy.allows(category, text)
            if allowed:
                policy.note_sent(category, text)
                sent += 1
            moment[0] = moment[0] + timedelta(seconds=1)

        return sent

    def test_every_category_in_the_enum_has_a_cooldown(self):
        """
        The two structures are declared in different files. This is the
        assertion that stops them drifting apart, and it is cheap enough
        that there is no excuse for not having had it.
        """

        missing = [
            category.value
            for category in Category
            if category.value not in DEFAULT_CATEGORY_COOLDOWN
        ]

        assert missing == [], f"categories with no cooldown: {missing}"

    def test_a_listed_category_is_throttled(self):
        """The control. One message, then the category cooldown holds."""

        assert self.send_five(Category.TASK.value) == 1

    def test_an_unknown_category_cannot_slip_past_the_throttle(self):
        """
        The defect. Before the fix this sent all five, because an unknown
        key means no cooldown means no limit.

        The fix is not to invent a plausible number for a category
        nobody declared - it is to refuse to send it at all. A category
        the system does not know is not a category with a lenient
        cooldown; it is a bug in the caller.
        """

        assert self.send_five("insight") == 0

    def test_and_it_says_why(self):
        """
        Section 21 again: silence that does not explain itself is
        untunable, and this system is mostly silent.
        """

        policy = ProactivePolicy(settings=self.settings(), clock=lambda: NOW)

        allowed, reason = policy.allows("insight", "something")

        assert allowed is False
        assert "insight" in reason
        assert "unknown category" in reason.lower()


class TestTheAppreciationCategoryCanActuallyFire:
    """
    `Category.APPRECIATION` had a decision branch, a cooldown, two
    composer templates and three passing tests, and could not fire in a
    real process. `launcher/services.py` never passed a `memories`
    source, so `engine.memories` was `None`, `_gather_memories()`
    returned `()`, and every appreciation was gated behind an empty
    tuple.

    The three existing tests all built a `ProactiveContext` by hand with
    `relevant_memories` already populated - which is precisely the state
    production could never reach. Wired, green, and dead, for the third
    time in this project, so the tests that matter here are the ones that
    start from the composition root and from a real database.
    """

    @pytest.fixture
    def services(self):
        """
        The real composition root, against a throwaway database.

        Injected rather than defaulted for the reason
        `tests/test_memory_integration.py` gives: building the pipeline
        seeds the profile and creates tables, and doing that to the real
        `data/memory.db` is a write these tests have no business making.
        """

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from launcher.services import build_services
        from memory.manager import MemoryManager
        from memory.models import Base

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()

        yield build_services(
            {
                "llm": {"provider": ""},
                "avatar": {"enabled": False},
                "proactive": {"enabled": True},
            },
            memory=MemoryManager(session=session),
        )

        session.close()

    def test_the_composition_root_hands_the_engine_a_memory_source(
        self, services
    ):
        """
        The defect itself, at the only layer that could have caught it.
        Asserting on the engine's field rather than on a context, because
        a context is the thing a test can fake and production cannot.
        """

        assert services.proactive is not None
        assert services.proactive.memories is not None

    def test_the_source_reads_the_same_store_the_reminders_do(self, services):
        """
        One episodic store, not two. Two would mean an appreciation about
        a project recorded in a database the reminders never read.
        """

        assert (
            services.proactive.memories.store
            is services.proactive.pending_tasks.store
        )
        assert services.proactive.memories.store is services.pipeline.episodic

    def test_the_engine_actually_consults_the_source(self, services):
        """
        One step further than "the field is set": build the context the
        way `tick()` does and confirm the source was reached. A source
        wired to an engine that never called it would satisfy the test
        above.
        """

        engine = services.proactive

        consulted = []

        def source():
            consulted.append(True)
            return ["rewriting the retriever"]

        engine.memories = source

        context = engine.build_context()

        assert consulted, "build_context never called the memory source"
        assert context.relevant_memories == ("rewriting the retriever",)

    def test_an_appreciation_survives_the_whole_path(self, store):
        """
        Decision, composer and policy, over a real episodic store: a
        `project` memory the user recorded three days ago comes back as
        an appreciation carrying their own sentence.

        Built with `build_proactive_engine` rather than `build_services`
        because this one has to send. The composition root supplies a
        `SendLedger` under the real data directory, and a test that
        records a send through it would write to the owner's file and
        would read whatever that file already said - so the outcome would
        depend on what the developer's machine sent yesterday
        (sections 41, 45). The wiring is covered above; this covers the
        path.
        """

        store.remember(
            "rewriting the retriever this month",
            category="project",
            occurred_at=NOW - timedelta(days=3),
        )

        engine = build_proactive_engine(
            config={
                "proactive": {
                    "enabled": True,
                    # Every gate that is not the subject of this test,
                    # opened out loud rather than left to a default that
                    # would make the result depend on the hour.
                    "cooldown_seconds": 0,
                    "quiet_hours": [],
                    "max_per_day": 99,
                }
            },
            clock=TemporalClock(now=lambda: NOW),
            memories=EpisodicMemorySource(store, clock=lambda: NOW),
            # Away long enough for a greeting to have been the reason,
            # which is why one is already on the record below.
            last_user_message=lambda: NOW - timedelta(hours=5),
        )

        # Appreciation is third in the decision order and the greeting
        # branch sits above it, so the afternoon greeting has to have
        # already happened for this to be the reason that wins.
        engine.policy.note_sent(Category.GREETING.value, "hey, you're back")

        decision = engine.tick()

        assert decision.send is True, decision.reason
        assert decision.category == Category.APPRECIATION.value
        assert "rewriting the retriever this month" in decision.detail


class TestTheAppreciationSourceOnlySaysWhatTheUserSaid:
    """
    `EpisodicMemorySource` makes the same promise `proactive/tasks.py`
    makes about reminders: everything specific in an outgoing message is
    the user's own sentence, passed through unchanged. An appreciation
    about a project the user never mentioned is Aura inventing a life for
    somebody and then admiring it.
    """

    def source(self, store, now=NOW):
        return EpisodicMemorySource(store, clock=lambda: now)

    def test_an_empty_store_says_nothing(self, store):
        """The normal case, and it must stay cheap."""

        assert self.source(store)() == []

    def test_it_returns_the_users_own_words(self, store):

        store.remember(
            "rewriting the retriever this month",
            category="project",
            occurred_at=NOW - timedelta(days=3),
        )

        assert self.source(store)() == ["rewriting the retriever this month"]

    def test_it_ignores_plans_because_the_task_source_owns_those(self, store):
        """
        Mining one category for two kinds of unprompted message means
        saying the same thing twice in two voices. `plan` belongs to
        `EpisodicTaskSource`.
        """

        store.remember(
            "I'm going to rewrite the retriever",
            category="plan",
            occurred_at=NOW - timedelta(days=3),
        )

        assert self.source(store)() == []

    def test_something_said_an_hour_ago_is_not_acknowledged_yet(self, store):
        """
        Repeating what the user said an hour ago back at them is not
        warmth. The window opens at a day.
        """

        store.remember(
            "working on the settings contract",
            category="project",
            occurred_at=NOW - timedelta(hours=1),
        )

        assert self.source(store)() == []

    def test_something_from_two_months_ago_is_left_alone(self, store):
        """Past the window, mentioning it is not warmth either."""

        store.remember(
            "working on the settings contract",
            category="project",
            occurred_at=NOW - timedelta(days=60),
        )

        assert self.source(store)() == []

    def test_a_fragment_is_not_a_subject(self, store):
        """
        The composer interpolates this into a sentence. "Been thinking
        about ok." is worse than silence.
        """

        store.remember(
            "ok",
            category="project",
            occurred_at=NOW - timedelta(days=3),
        )

        assert self.source(store)() == []

    def test_the_most_recent_comes_first(self, store):

        store.remember(
            "the older project nobody mentions",
            category="project",
            occurred_at=NOW - timedelta(days=10),
        )
        store.remember(
            "the newer project worth acknowledging",
            category="project",
            occurred_at=NOW - timedelta(days=2),
        )

        assert self.source(store)()[0] == "the newer project worth acknowledging"

    def test_one_unreadable_row_does_not_silence_the_good_ones(self, store):
        """
        `parse_timestamp` is tolerant on purpose - it reads columns
        written by several versions of Aura, and its own docstring says
        one unreadable row must not take down recall. Skipping the row is
        what makes that true here.

        Without the skip the comparison is `oldest <= None`, which
        raises, which `_gather_memories` swallows as "nothing to say". So
        the failure is not one lost memory: it is every memory lost, for
        as long as that row is in the fortnight the query covers. The bad
        row is deliberately the one `by_category` returns first, since it
        sorts a string column and "sometime last tuesday" sorts above any
        real ISO date.
        """

        store.remember(
            "the row an older Aura wrote",
            category="project",
            occurred_at="sometime last tuesday",
        )
        store.remember(
            "rewriting the retriever this month",
            category="project",
            occurred_at=NOW - timedelta(days=3),
        )

        assert self.source(store)() == ["rewriting the retriever this month"]

    def test_a_broken_store_is_a_source_with_nothing_to_say(self):
        """
        Not caught in the source: the engine already treats a failing
        source as silence, and a second opinion about what a broken
        database means is how the two drift apart.
        """

        class Broken:
            def by_category(self, *a, **k):
                raise RuntimeError("database is gone")

        engine = ProactiveEngine(memories=self.source(Broken()))

        assert engine._gather_memories() == ()


class TestNothingOnTheBusActsOnAnEvent:
    """
    Section 21's one binding sentence: "AURA must not silently perform
    arbitrary high-impact actions merely because it detected an event."

    Today that holds, and it holds by accident. Every subscriber on the
    bus happens to be something that renders, speaks, logs or queues -
    the avatar, the TTS engine, the event log, the notification outbox.
    Nothing on the bus opens an app, runs a tool or touches the network
    on the owner's behalf, so no event can currently cause an action.

    That is a property of the current subscriber list, not of the bus.
    `subscribe` takes any callable, `publish` calls every match, and
    handler exceptions are swallowed - so the day something that acts is
    attached, an event becomes an action with no decision anywhere and no
    line in a diff that says so.

    So the accident is written down here. This is not a test of how the
    bus dispatches; it is the boundary itself, expressed as the one thing
    that can be inspected: who is listening. A new subscriber from
    outside the presentation layer fails this test, and the failure is
    the section 21 decision the change needs - either it is presentation
    and belongs on the list, or it acts and needs a confirmation policy
    ahead of it rather than a subscription.
    """

    # Every module allowed to hold a bus subscription, and what each one
    # does when an event arrives. Presentation and recording only.
    PRESENTATION = {
        "events.log": "writes a DEBUG line",
        "avatar.state": "moves the avatar's state machine",
        "avatar.animation": "changes which animation is playing",
        "avatar.controller": "blinks",
        "voice.tts.engine": "speaks the reply that was already produced",
        "server.notifications": "queues for a device to come and drain",
    }

    @pytest.fixture
    def bus(self):
        """
        The real composition root with every presentation consumer it
        has, plus the outbox the way `server/runtime.py` attaches it.

        Against a throwaway database, for the reason
        `tests/test_memory_integration.py` gives: building the pipeline
        creates tables and seeds the profile, which is not a write these
        tests should make to `data/memory.db`.
        """

        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from launcher.services import build_services
        from memory.manager import MemoryManager
        from memory.models import Base
        from server.notifications import NotificationOutbox

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        session = sessionmaker(bind=engine)()

        services = build_services(
            {
                "llm": {"provider": ""},
                # On, so the test sees the subscribers a real desktop
                # process has rather than the empty bus a headless one
                # would give it.
                "avatar": {"enabled": True},
                "proactive": {"enabled": True},
                "voice": {"tts": {"enabled": True, "provider": "mock"}},
            },
            memory=MemoryManager(session=session),
        )

        NotificationOutbox().attach(services.bus)

        yield services.bus

        session.close()

    def subscribers(self, bus) -> list[tuple[str, str]]:
        """
        Every handler on the bus as (owning module, name).

        Reads `_wildcard` as well as `_handlers`: `subscribe_all` keeps
        its handlers in a separate list, so a walk of `_handlers` alone
        misses the one subscriber that receives every event - which is
        exactly the kind of subscription this test exists to notice.
        """

        found = []

        for handler in list(bus._wildcard):
            found.append((self.owner(handler), self.name(handler)))

        for handlers in bus._handlers.values():
            for handler in handlers:
                found.append((self.owner(handler), self.name(handler)))

        return found

    @staticmethod
    def owner(handler) -> str:
        """
        The module that owns the handler, bound method or closure.

        `__module__` on a bound method is where the function was defined,
        which for a subscriber defined inside `attach()` is the same
        answer - but for a method inherited from a base class it is not.
        The instance's class is the honest owner, so it is preferred.
        """

        instance = getattr(handler, "__self__", None)

        if instance is not None:
            return type(instance).__module__

        return getattr(handler, "__module__", "")

    @staticmethod
    def name(handler) -> str:
        return getattr(handler, "__qualname__", repr(handler))

    def test_every_subscriber_is_presentation(self, bus):

        acting = [
            (module, name)
            for module, name in self.subscribers(bus)
            if module not in self.PRESENTATION
        ]

        assert acting == [], (
            "A subscriber outside the presentation layer is on the event "
            f"bus: {acting}. Section 21 forbids an event silently causing "
            "an action, so this needs a decision rather than an addition "
            "to this list: if the handler renders, logs or queues, add it "
            "to PRESENTATION with what it does; if it acts on the owner's "
            "behalf, it needs a confirmation policy in front of it and "
            "must not be reached by publish() alone."
        )

    def test_the_bus_is_not_vacuously_clean(self, bus):
        """
        The test above passes on an empty bus, and an empty bus is a
        broken composition root rather than a safe one - it is what the
        phase 12 defect looked like, when `subscribe_all` had no
        production caller at all.
        """

        modules = {module for module, _name in self.subscribers(bus)}

        assert "events.log" in modules, "nothing is observing the bus"
        assert "avatar.animation" in modules
        assert "server.notifications" in modules

    def test_the_proactive_output_reaches_only_consumers_that_queue_it(
        self, bus
    ):
        """
        The specific section 21 case, narrowed to the event this phase
        publishes. An unprompted message Aura decided to send on her own
        must end up somewhere a device can collect it, and nowhere that
        does anything else with it.

        Asked through `publish`'s own matching rule rather than by
        reading one key of `_handlers`, because a handler registered
        against `Event` receives this too - and that subscription by base
        class is the one a narrower test would miss.
        """

        event = CompanionNotificationEvent(
            message="Been thinking about the retriever.",
            reason="test",
            source="proactive",
        )

        reached = [self.owner(handler) for handler in list(bus._wildcard)]

        for event_type, handlers in bus._handlers.items():
            if isinstance(event, event_type):
                reached.extend(self.owner(handler) for handler in handlers)

        assert "server.notifications" in reached, (
            "a proactive message has no transport - it would be decided, "
            "composed, rate-limited and then dropped"
        )

        assert set(reached) <= set(self.PRESENTATION), sorted(
            set(reached) - set(self.PRESENTATION)
        )
