"""
The notification outbox.

What happens between "Aura decided to say something" and "the phone was
holding it". A dropped socket must not lose a decision she already made,
and a decision she made an hour ago must not arrive as if it were news.
"""

import pytest

from events.bus import EventBus
from events.types import CompanionNotificationEvent
from server.notifications import (
    BROADCAST,
    NotificationOutbox,
    PendingNotification,
)


class FakeClock:
    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def notification(**overrides) -> PendingNotification:
    fields = {
        "message": "Your build failed.",
        "reason": "build failure",
        "priority": "high",
        "confidence": 0.9,
        "source": "GitHub",
        "device_id": "phone-1",
    }
    fields.update(overrides)
    return PendingNotification(**fields)


class TestPendingNotification:

    def test_it_serialises_for_an_api_response(self):
        data = notification().as_dict()

        assert data["message"] == "Your build failed."
        assert data["priority"] == "high"
        assert data["confidence"] == 0.9
        assert "notification_id" in data

    def test_every_notification_is_distinguishable(self):
        assert notification().notification_id != notification().notification_id


class TestNotificationOutbox:

    def test_nothing_waiting_drains_to_nothing(self):
        assert NotificationOutbox().drain("phone-1") == []

    def test_a_notification_waits_for_its_device(self):
        outbox = NotificationOutbox()
        outbox.add(notification())

        collected = outbox.drain("phone-1")

        assert len(collected) == 1
        assert collected[0].message == "Your build failed."

    def test_collecting_is_destructive(self):
        outbox = NotificationOutbox()
        outbox.add(notification())

        outbox.drain("phone-1")

        # A device that polls twice must not see the same remark twice.
        assert outbox.drain("phone-1") == []

    def test_one_device_does_not_read_another_device_s_mail(self):
        outbox = NotificationOutbox()
        outbox.add(notification(device_id="phone-1"))

        assert outbox.drain("phone-2") == []
        assert len(outbox.drain("phone-1")) == 1

    def test_a_notification_with_no_device_reaches_whoever_asks(self):
        outbox = NotificationOutbox()
        outbox.add(notification(device_id=""))

        collected = outbox.drain("phone-9")

        assert len(collected) == 1

    def test_a_device_gets_both_its_own_and_the_broadcast_queue(self):
        outbox = NotificationOutbox()
        outbox.add(notification(device_id=""))
        outbox.add(notification(device_id="phone-1", message="Second"))

        assert len(outbox.drain("phone-1")) == 2

    def test_they_arrive_oldest_first(self):
        clock = FakeClock()
        outbox = NotificationOutbox(clock=clock)

        outbox.add(notification(message="First", created_at=clock()))
        clock.advance(10.0)
        outbox.add(notification(message="Second", created_at=clock()))

        collected = outbox.drain("phone-1")

        assert [item.message for item in collected] == ["First", "Second"]

    def test_a_stale_notification_is_dropped_unread(self):
        clock = FakeClock()
        outbox = NotificationOutbox(max_age=1800.0, clock=clock)

        outbox.add(notification(created_at=clock()))
        clock.advance(1801.0)

        # A remark about a build that failed an hour ago is noise.
        assert outbox.drain("phone-1") == []

    def test_the_queue_is_bounded(self):
        outbox = NotificationOutbox(max_per_device=3)

        for index in range(10):
            outbox.add(notification(message=f"Thing {index}"))

        collected = outbox.drain("phone-1")

        assert len(collected) == 3
        assert collected[-1].message == "Thing 9"

    def test_pending_counts_without_collecting(self):
        outbox = NotificationOutbox()
        outbox.add(notification())

        assert outbox.pending("phone-1") == 1
        assert outbox.pending("phone-1") == 1

    def test_clear_empties_everything(self):
        outbox = NotificationOutbox()
        outbox.add(notification())

        outbox.clear()

        assert outbox.pending("phone-1") == 0


class TestOutboxAttachedToTheBus:

    def test_a_published_event_becomes_a_pending_notification(self):
        bus = EventBus()
        outbox = NotificationOutbox()
        outbox.attach(bus)

        bus.publish(CompanionNotificationEvent(
            message="Your build failed.",
            reason="build failure",
            priority="high",
            confidence=0.9,
            source="GitHub",
            device_id="phone-1",
        ))

        collected = outbox.drain("phone-1")

        assert len(collected) == 1
        assert collected[0].message == "Your build failed."
        assert collected[0].priority == "high"

    def test_an_event_with_no_device_is_broadcast(self):
        bus = EventBus()
        outbox = NotificationOutbox()
        outbox.attach(bus)

        bus.publish(CompanionNotificationEvent(message="Something happened."))

        assert outbox.pending(BROADCAST) == 1

    def test_other_events_are_ignored(self):
        from events.types import ResponseEvent

        bus = EventBus()
        outbox = NotificationOutbox()
        outbox.attach(bus)

        bus.publish(ResponseEvent(text="hello"))

        assert outbox.pending("phone-1") == 0
