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
