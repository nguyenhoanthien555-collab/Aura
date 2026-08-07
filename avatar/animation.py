"""
Animation events.

The avatar wants a finer grained story than the conversation tells. The
brain publishes "a reply arrived"; a face wants "stop the thinking loop,
start the talking loop, and by the way blink". Rather than teach the
brain to say all that - which would put animation vocabulary inside
conversation logic - this derives it.

    domain facts                 animation events
    ------------                 ----------------
    ThinkingEvent          ->    ThinkingStartedEvent
    ResponseEvent          ->    ThinkingFinishedEvent, Typing*
    ErrorEvent             ->    ThinkingFinishedEvent(ok=False)
    SpeakingEvent(True)    ->    SpeechStartedEvent
    SpeakingEvent(False)   ->    SpeechFinishedEvent
    StreamStartedEvent     ->    TypingStartedEvent
    StreamFinishedEvent    ->    TypingFinishedEvent
    (idle time passing)    ->    BlinkEvent

Same shape as AvatarStateMachine: subscribe to facts, own a derivation,
publish the result. The brain is not modified and does not know this
exists.

Two rules keep it safe to run on a live bus.

First, the animation events are siblings of the domain events rather than
subclasses. The bus delivers to base classes, so a director listening for
SpeakingEvent that published a subclass of SpeakingEvent would receive
its own output and recurse forever. Sibling types make that impossible by
construction.

Second, every pair is edge triggered. A finish is only published if the
matching start was, so a stray ResponseEvent with no ThinkingEvent before
it does not leave an animator unwinding a loop that never began.

Blinking has no timer of its own. `tick(now)` is called by whoever owns a
clock - a Tk `after` loop, a render frame - which is what makes this
testable by passing 0.0 and then 9.0.
"""

from typing import Callable

from events.types import (
    BlinkEvent,
    ErrorEvent,
    ResponseEvent,
    SpeakingEvent,
    SpeechFinishedEvent,
    SpeechStartedEvent,
    StreamFinishedEvent,
    StreamStartedEvent,
    ThinkingEvent,
    ThinkingFinishedEvent,
    ThinkingStartedEvent,
    TypingFinishedEvent,
    TypingStartedEvent,
)


# Roughly human, and slow enough that a missed tick is not visible.
BLINK_INTERVAL = 4.0


class AnimationDirector:
    """
    Turns conversation facts into animation cues.

    Publishes to the same bus it listens on. Safe because it subscribes to
    concrete domain types only and emits types that are not among them.
    """

    def __init__(
        self,
        events=None,
        blink_interval: float = BLINK_INTERVAL,
        jitter: Callable[[], float] | None = None,
    ):
        """
        `jitter` returns extra seconds to add to the next blink delay, so
        two Auras on screen do not blink in lockstep. Left as None the
        interval is exact, which is what tests want.
        """

        self.events = events
        self.blink_interval = max(0.0, blink_interval)
        self.jitter = jitter

        # Edge tracking. Each pair publishes its finish only if its start
        # went out, so the stream a renderer sees is always balanced.
        self._thinking = False
        self._speaking = False
        self._typing = False

        self._next_blink: float | None = None

        self._releases: list[Callable[[], None]] = []

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def attach(self, bus) -> Callable[[], None]:
        """
        Follow a bus and publish derived events back onto it.

        Subscribes to each concrete type rather than to Event. Listening
        to the base class would also deliver this director's own output
        back to it.
        """

        self.events = self.events or bus

        pairs = (
            (ThinkingEvent, self._on_thinking),
            (ResponseEvent, self._on_response),
            (ErrorEvent, self._on_error),
            (SpeakingEvent, self._on_speaking),
            (StreamStartedEvent, self._on_stream_started),
            (StreamFinishedEvent, self._on_stream_finished),
        )

        for event_type, handler in pairs:
            self._releases.append(bus.subscribe(event_type, handler))

        def detach() -> None:
            self.detach()

        return detach

    def detach(self) -> None:

        for release in self._releases:
            try:
                release()
            except Exception:
                pass

        self._releases.clear()

    # ------------------------------------------------------------------
    # Thinking
    # ------------------------------------------------------------------

    def _on_thinking(self, event: ThinkingEvent) -> None:

        if self._thinking:
            return

        self._thinking = True

        self._emit(ThinkingStartedEvent())

    def _finish_thinking(self, ok: bool) -> None:

        if not self._thinking:
            return

        self._thinking = False

        self._emit(ThinkingFinishedEvent(ok=ok))

    # ------------------------------------------------------------------
    # Replies
    # ------------------------------------------------------------------

    def _on_response(self, event: ResponseEvent) -> None:
        """
        A finished reply.

        A streamed reply already produced its typing events from the
        stream, so only the unstreamed case opens and closes a typing
        window here - the whole text appeared at once, which is a typing
        window of zero length rather than no typing at all.
        """

        self._finish_thinking(ok=True)

        if getattr(event, "streamed", False):
            self._finish_typing()
            return

        if event.text:
            self._start_typing()
            self._finish_typing()

    def _on_error(self, event: ErrorEvent) -> None:

        self._finish_thinking(ok=False)
        self._finish_typing()

    # ------------------------------------------------------------------
    # Typing
    # ------------------------------------------------------------------

    def _on_stream_started(self, event: StreamStartedEvent) -> None:
        self._start_typing()

    def _on_stream_finished(self, event: StreamFinishedEvent) -> None:
        self._finish_typing()

    def _start_typing(self) -> None:

        if self._typing:
            return

        self._typing = True

        self._emit(TypingStartedEvent())

    def _finish_typing(self) -> None:

        if not self._typing:
            return

        self._typing = False

        self._emit(TypingFinishedEvent())

    # ------------------------------------------------------------------
    # Speech
    # ------------------------------------------------------------------

    def _on_speaking(self, event: SpeakingEvent) -> None:
        """
        Translate the voice system's start/stop flag into a pair.

        A reply being spoken means thinking is over, even if no
        ResponseEvent was seen - TTS can be driven by a stream instead.
        """

        if event.active:

            if self._speaking:
                return

            self._speaking = True

            self._finish_thinking(ok=True)

            self._emit(
                SpeechStartedEvent(
                    text=event.text,
                    voice=getattr(event, "voice", ""),
                    duration=getattr(event, "duration", 0.0),
                )
            )

            return

        if not self._speaking:
            return

        self._speaking = False

        self._emit(SpeechFinishedEvent(ok=True))

    # ------------------------------------------------------------------
    # Blinking
    # ------------------------------------------------------------------

    def tick(self, now: float) -> bool:
        """
        Publish a blink if one is due. Returns True when it did.

        Blinking is suppressed while speaking: a mouth animation and a
        blink competing for the same frame is how a model looks glitched
        rather than alive.
        """

        if self.blink_interval <= 0.0:
            return False

        if self._next_blink is None:
            self._next_blink = now + self._delay()
            return False

        if now < self._next_blink:
            return False

        self._next_blink = now + self._delay()

        if self._speaking:
            return False

        self._emit(BlinkEvent())

        return True

    def _delay(self) -> float:

        if self.jitter is None:
            return self.blink_interval

        try:
            return max(0.1, self.blink_interval + float(self.jitter()))
        except Exception:
            return self.blink_interval

    # ------------------------------------------------------------------

    def _emit(self, event) -> None:

        if self.events is None:
            return

        try:
            self.events.publish(event)
        except Exception:
            pass

    def __repr__(self) -> str:
        return (
            f"AnimationDirector(thinking={self._thinking}, "
            f"speaking={self._speaking}, typing={self._typing})"
        )


__all__ = ["AnimationDirector", "BLINK_INTERVAL"]
