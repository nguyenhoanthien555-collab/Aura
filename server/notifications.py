"""
Pending notifications, per device.

The companion engine publishes a `CompanionNotificationEvent` and stops
caring. Something has to hold that message until the phone next asks for
it, because a phone is not always connected and a decision Aura already
made should not be lost to a dropped socket.

A bounded deque per device, in memory. Notifications are worth minutes,
not days - a remark about a build that failed an hour ago is noise, and
persisting it would only guarantee it arrives late.
"""

import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from threading import Lock


DEFAULT_MAX_PER_DEVICE = 20

# Anything older than this is dropped unread.
DEFAULT_MAX_AGE = 1800.0

# Notifications with no device attached go here, and any device asking
# for its mail also gets these.
BROADCAST = "*"


@dataclass(frozen=True)
class PendingNotification:
    """One undelivered notification."""

    message: str
    reason: str = ""
    priority: str = "normal"
    confidence: float = 0.0
    source: str = ""
    device_id: str = ""
    created_at: float = field(default_factory=time.time)
    notification_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def as_dict(self) -> dict:
        return {
            "notification_id": self.notification_id,
            "message": self.message,
            "reason": self.reason,
            "priority": self.priority,
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "created_at": self.created_at,
        }


class NotificationOutbox:
    """
    Holds notifications until a device collects them.

    Thread-safe. `drain` is destructive: a collected notification is
    gone, which is what makes polling idempotent from the device's point
    of view - it never sees the same remark twice.
    """

    def __init__(
        self,
        max_per_device: int = DEFAULT_MAX_PER_DEVICE,
        max_age: float = DEFAULT_MAX_AGE,
        clock=time.time,
    ):
        self.max_per_device = max_per_device
        self.max_age = max_age
        self.clock = clock

        self._lock = Lock()
        self._queues: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=self.max_per_device)
        )

    def add(self, notification: PendingNotification) -> None:

        key = notification.device_id or BROADCAST

        with self._lock:
            self._queues[key].append(notification)

    def drain(self, device_id: str = "") -> list[PendingNotification]:
        """
        Take everything waiting for this device, newest last.

        Includes the broadcast queue, so a notification raised without a
        device attached reaches whoever asks first.
        """

        now = self.clock()

        keys = [BROADCAST]

        if device_id and device_id != BROADCAST:
            keys.append(device_id)

        collected: list[PendingNotification] = []

        with self._lock:
            for key in keys:

                queue = self._queues.get(key)

                if not queue:
                    continue

                collected.extend(queue)
                queue.clear()

        fresh = [
            item for item in collected
            if now - item.created_at <= self.max_age
        ]

        fresh.sort(key=lambda item: item.created_at)

        return fresh

    def pending(self, device_id: str = "") -> int:
        """How many are waiting, without collecting them."""

        keys = [BROADCAST]

        if device_id and device_id != BROADCAST:
            keys.append(device_id)

        with self._lock:
            return sum(len(self._queues.get(key) or ()) for key in keys)

    def clear(self) -> None:
        with self._lock:
            self._queues.clear()

    # ------------------------------------------------------------------

    def attach(self, bus) -> None:
        """
        Subscribe to the event bus.

        This is the only thing that connects the companion engine to a
        transport, and it is a subscription rather than a call - the
        engine still knows nothing about phones.
        """

        from events.types import CompanionNotificationEvent

        def handle(event: CompanionNotificationEvent) -> None:
            self.add(
                PendingNotification(
                    message=event.message,
                    reason=event.reason,
                    priority=event.priority,
                    confidence=event.confidence,
                    source=event.source,
                    device_id=event.device_id,
                    created_at=self.clock(),
                )
            )

        bus.subscribe(CompanionNotificationEvent, handle)
