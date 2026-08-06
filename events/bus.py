"""
Event bus.

A deliberately small synchronous publish/subscribe hub. No framework,
no threads of its own, no async runtime.

Design rules:

- Publishing must never raise because of a subscriber. A crashing avatar
  must not take down the conversation that triggered it. Handler errors
  are captured and logged, and remaining handlers still run.

- Subscribing during a publish must not affect that publish. Handlers
  are snapshotted before dispatch, so a handler that subscribes or
  unsubscribes cannot mutate the list being iterated.

- Delivery is synchronous and in subscription order, which keeps tests
  deterministic and avoids a background thread for the common case.
"""

import threading
from collections import defaultdict
from typing import Callable, Type, TypeVar

from core.logger import logger
from events.types import Event

E = TypeVar("E", bound=Event)

Handler = Callable[[Event], None]


class EventBus:

    def __init__(self):
        self._handlers: dict[type, list[Handler]] = defaultdict(list)
        self._wildcard: list[Handler] = []
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Subscription
    # ------------------------------------------------------------------

    def subscribe(
        self,
        event_type: Type[E],
        handler: Callable[[E], None],
    ) -> Callable[[], None]:
        """
        Register a handler for one event type.

        Returns an unsubscribe callable, so callers can clean up without
        needing to hold on to the handler reference themselves.
        """

        with self._lock:
            self._handlers[event_type].append(handler)

        def unsubscribe() -> None:
            self.unsubscribe(event_type, handler)

        return unsubscribe

    def subscribe_all(self, handler: Handler) -> Callable[[], None]:
        """
        Register a handler that receives every event.

        Used by logging and by the avatar debug overlay.
        """

        with self._lock:
            self._wildcard.append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._wildcard:
                    self._wildcard.remove(handler)

        return unsubscribe

    def unsubscribe(self, event_type: type, handler: Handler) -> None:
        with self._lock:
            handlers = self._handlers.get(event_type)
            if handlers and handler in handlers:
                handlers.remove(handler)

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()
            self._wildcard.clear()

    # ------------------------------------------------------------------
    # Publishing
    # ------------------------------------------------------------------

    def publish(self, event: Event) -> None:
        """
        Deliver an event to every matching handler.

        Handlers registered against a base class also receive subclass
        events, so subscribing to Event is equivalent to subscribe_all.
        """

        with self._lock:
            # Snapshot inside the lock; dispatch outside it, so a handler
            # is free to subscribe/unsubscribe or publish without
            # deadlocking on the re-entrant lock.
            targets: list[Handler] = list(self._wildcard)

            for event_type, handlers in self._handlers.items():
                if isinstance(event, event_type):
                    targets.extend(handlers)

        for handler in targets:
            try:
                handler(event)
            except Exception as error:
                # One bad subscriber must not abort delivery to the rest.
                logger.exception(
                    "Event handler failed for %s: %s",
                    type(event).__name__,
                    error,
                )

    def handler_count(self, event_type: type | None = None) -> int:
        """Number of registered handlers. Used by tests and diagnostics."""

        with self._lock:
            if event_type is None:
                total = len(self._wildcard)
                return total + sum(
                    len(h) for h in self._handlers.values()
                )

            return len(self._handlers.get(event_type, []))
