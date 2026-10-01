"""
Unit tests for Proactive Intelligence Upgrades: Evening Recap, Goal Follow-up, and Wellbeing.
"""

from datetime import datetime, timedelta
import pytest

from core.temporal import TemporalContext
from proactive.context import ProactiveContext
from proactive.decision import (
    ACTIVE_CONVERSATION_SECONDS,
    Category,
    ProactiveDecision,
    should_proactively_message,
)
from proactive.messages import MessageComposer
from proactive.policy import (
    DEFAULT_CATEGORY_COOLDOWN,
    ProactivePolicy,
    ProactiveSettings,
)

NOW_EVENING = datetime(2026, 10, 1, 21, 0, 0)
NOW_AFTERNOON = datetime(2026, 10, 1, 14, 0, 0)


def evening_context(
    now: datetime = NOW_EVENING,
    last_user: datetime = None,
    daily_topics: tuple = (),
    active_goals: tuple = (),
    session_duration: float = 0.0,
    greeted: bool = True,
) -> ProactiveContext:
    return ProactiveContext(
        temporal=TemporalContext(now=now),
        last_user_message_at=last_user,
        daily_topics=daily_topics,
        active_goals=active_goals,
        session_duration_seconds=session_duration,
        greeted_this_part=greeted,
    )


def test_category_enum_and_cooldown_registry():
    assert "evening_recap" in [c.value for c in Category]
    assert "goal_followup" in [c.value for c in Category]
    assert Category.EVENING_RECAP.value in DEFAULT_CATEGORY_COOLDOWN
    assert Category.GOAL_FOLLOWUP.value in DEFAULT_CATEGORY_COOLDOWN


def test_evening_recap_triggers_in_evening_window():
    # User was active 45 minutes ago in the evening, daily topics present
    ctx = evening_context(
        last_user=NOW_EVENING - timedelta(minutes=45),
        daily_topics=("xây dựng web search và workspace tools",),
    )
    decision = should_proactively_message(ctx)
    assert decision.send is True
    assert decision.category == Category.EVENING_RECAP.value
    assert "web search" in decision.detail


def test_evening_recap_silent_during_active_chat():
    # User spoke 30 seconds ago (active interaction) -> must stay silent
    ctx = evening_context(
        last_user=NOW_EVENING - timedelta(seconds=30),
        daily_topics=("xây dựng web search",),
    )
    decision = should_proactively_message(ctx)
    assert decision.send is False
    assert "recent interaction" in decision.reason


def test_goal_followup_triggers_after_absence():
    # User has been away for 2 hours, active goal tracked
    ctx = evening_context(
        now=NOW_AFTERNOON,
        last_user=NOW_AFTERNOON - timedelta(hours=2),
        active_goals=("hoàn thành kiến trúc Aura 2.0",),
    )
    decision = should_proactively_message(ctx)
    assert decision.send is True
    assert decision.category == Category.GOAL_FOLLOWUP.value
    assert "Aura 2.0" in decision.detail


def test_wellbeing_prolonged_session():
    # 3.5 hour continuous working session during the afternoon
    ctx = evening_context(
        now=NOW_AFTERNOON,
        last_user=NOW_AFTERNOON - timedelta(minutes=10),
        session_duration=3.5 * 3600,
    )
    decision = should_proactively_message(ctx)
    assert decision.send is True
    assert decision.category == Category.WELLBEING.value


def test_message_composer_evening_recap():
    composer = MessageComposer()
    ctx = evening_context(daily_topics=("dự án Aura Companion",))
    decision = ProactiveDecision(
        send=True,
        reason="evening recap test",
        category=Category.EVENING_RECAP.value,
        detail="dự án Aura Companion",
    )
    msg = composer.compose(decision, ctx, rotation=0)
    assert "Aura Companion" in msg
    assert len(msg) > 10

    # Empty detail returns ""
    decision_empty = ProactiveDecision(
        send=True,
        reason="empty detail",
        category=Category.EVENING_RECAP.value,
        detail="   ",
    )
    assert composer.compose(decision_empty, ctx) == ""


def test_message_composer_goal_followup():
    composer = MessageComposer()
    ctx = evening_context()
    decision = ProactiveDecision(
        send=True,
        reason="goal followup test",
        category=Category.GOAL_FOLLOWUP.value,
        detail="nâng cấp hệ thống nhớ",
    )
    msg = composer.compose(decision, ctx, rotation=0)
    assert "nâng cấp hệ thống nhớ" in msg


def test_proactive_policy_respects_new_category_cooldowns():
    settings = ProactiveSettings(enabled=True)
    policy = ProactivePolicy(settings=settings, clock=lambda: NOW_EVENING)

    # 1. First evening recap should be allowed
    recap_msg = "Tổng kết buổi tối: hôm nay chúng ta đã hoàn thành xuất sắc!"
    allowed, reason = policy.allows(Category.EVENING_RECAP.value, recap_msg)
    assert allowed

    # Record sending it
    policy.note_sent(Category.EVENING_RECAP.value, recap_msg)

    # 2. Immediate second recap should be rejected by cooldown
    allowed_second, reason_second = policy.allows(
        Category.EVENING_RECAP.value,
        "Một tổng kết buổi tối khác hoàn toàn mới",
    )
    assert not allowed_second
    assert "cooldown" in reason_second.lower()


def test_companion_goal_source():
    from proactive.goals import CompanionGoalSource
    from memory.companion import Goal

    class FakeGoalStore:
        def __init__(self, goals):
            self._goals = goals

        def active(self, limit=None):
            return self._goals[:limit] if limit else self._goals

    class FakeCompanion:
        def __init__(self, goals):
            self.goals = FakeGoalStore(goals)

    # 1. Empty companion / None
    empty_source = CompanionGoalSource(None)
    assert empty_source() == []

    # 2. Companion with active goals
    goals = [
        Goal(title="Viết xong web search", priority="now"),
        Goal(title="Nâng cấp memory", priority="soon"),
    ]
    companion = FakeCompanion(goals)
    source = CompanionGoalSource(companion, limit=5)
    result = source()
    assert result == ["Viết xong web search", "Nâng cấp memory"]

    # 3. Direct GoalStore passing
    direct_source = CompanionGoalSource(FakeGoalStore(goals), limit=1)
    assert direct_source() == ["Viết xong web search"]

    # 4. Broken store produces safe empty list
    class BrokenStore:
        def active(self, limit=None):
            raise RuntimeError("Database corrupted")

    broken_source = CompanionGoalSource(BrokenStore())
    assert broken_source() == []


def test_daily_topic_source():
    from proactive.topics import DailyTopicSource
    from memory.companion import Highlight
    from memory.models import EpisodicMemory, Message

    now = datetime(2026, 10, 1, 21, 30, 0)

    # 1. Source with companion highlights
    class FakeHighlights:
        def recent(self, limit=10):
            return [
                Highlight(summary="Triển khai git tools", reason="work", at="2026-10-01T15:00:00"),
                Highlight(summary="Tin tức cũ", reason="old", at="2026-09-30T10:00:00"),
            ]

    class FakeCompanion:
        highlights = FakeHighlights()

    source = DailyTopicSource(companion=FakeCompanion(), clock=lambda: now)
    topics = source()
    assert topics == ["Triển khai git tools"]

    # 2. Source with episodic store
    class FakeEpisodicStore:
        def since(self, moment):
            return [
                EpisodicMemory(
                    content="Lập trình proactive context engine",
                    category="project",
                    occurred_at="2026-10-01T18:00:00",
                ),
                EpisodicMemory(
                    content="Ăn tối",
                    category="other",
                    occurred_at="2026-10-01T19:00:00",
                ),
            ]

    source_ep = DailyTopicSource(episodic_store=FakeEpisodicStore(), clock=lambda: now)
    topics_ep = source_ep()
    assert "Lập trình proactive context engine" in topics_ep

    # 3. Fallback to memory messages
    class FakeMemory:
        def get_recent(self, limit=30):
            return [
                Message(
                    role="user",
                    content="hãy hỗ trợ anh refactor module memory",
                    timestamp="2026-10-01T16:00:00",
                ),
                Message(
                    role="user",
                    content="hello",  # Should be filtered as greeting
                    timestamp="2026-10-01T16:05:00",
                ),
            ]

    source_mem = DailyTopicSource(memory=FakeMemory(), clock=lambda: now)
    topics_mem = source_mem()
    assert len(topics_mem) == 1
    assert "refactor module memory" in topics_mem[0]

    # 4. Empty / Broken sources produce empty list safely
    broken_source = DailyTopicSource(
        episodic_store=object(),
        companion=object(),
        memory=object(),
        clock=lambda: now,
    )
    assert broken_source() == []


def test_proactive_engine_gathering_layer_wiring():
    from proactive.engine import ProactiveEngine
    from core.temporal import TemporalClock

    clock_now = NOW_EVENING
    clock = TemporalClock(now=lambda: clock_now)

    engine = ProactiveEngine(
        clock=clock,
        active_goals=lambda: ["Mục tiêu Alpha"],
        daily_topics=lambda: ["Chủ đề Beta"],
    )

    # Context before any chat: session_duration is 0.0
    ctx = engine.build_context()
    assert ctx.active_goals == ("Mục tiêu Alpha",)
    assert ctx.daily_topics == ("Chủ đề Beta",)
    assert ctx.session_duration_seconds == 0.0

    # Note chat at 21:00
    engine.note_chat()

    # Move clock forward by 30 minutes
    clock_now = NOW_EVENING + timedelta(minutes=30)
    ctx_after = engine.build_context()
    assert ctx_after.session_duration_seconds == 1800.0


def test_the_composition_root_wires_proactive_sources():
    from launcher.services import build_services
    from proactive.goals import CompanionGoalSource
    from proactive.topics import DailyTopicSource

    services = build_services(
        config={
            "llm": {"provider": "mock"},
            "memory": {"recall": False, "profile": False, "companion": True},
            "voice": {"enabled": False},
            "vision": {"enabled": False},
            "tools": {"enabled": False},
        }
    )

    assert services.proactive is not None
    assert isinstance(services.proactive.active_goals_source, CompanionGoalSource)
    assert isinstance(services.proactive.daily_topics_source, DailyTopicSource)


