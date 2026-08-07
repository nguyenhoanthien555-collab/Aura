"""
Streaming speech.

The point of streaming is that Aura starts talking early. Text on screen
gets that for free; speech does not, because a synthesiser needs whole
sentences - "The dep" and "endency" are two clips of nonsense.

So this sits on the bus, watches the chunk stream, aggregates it into
sentences and speaks each one as it completes:

    StreamChunkEvent  ->  SentenceAggregator  ->  TTSEngine.speak()

The first sentence is usually spoken while the model is still writing the
third, which is the whole difference between a companion that answers and
one that waits.

Ordering is the hard part and the reason for the queue. `speak` blocks
until playback finishes, and chunks keep arriving on the bus while it
does. Speaking inline from the handler would stall the stream - and with
a synchronous bus, stall the generator producing it. So sentences go on a
queue and one worker thread drains it, which keeps them in order, keeps
exactly one voice audible at a time, and never blocks the publisher.

    brain      publishes chunks, never waits, never imports this
    this       aggregates and queues, never blocks the bus
    worker     speaks one sentence at a time, in order

It uses SentenceAggregator from brain/streaming.py rather than
reimplementing the rule. That module is pure text handling with no
imports of its own; borrowing a text utility is not the voice layer
depending on conversation logic, and duplicating the sentence rule would
guarantee the two eventually disagree about what a sentence is.
"""

import queue
import threading

from brain.streaming import SentenceAggregator
from core.logger import logger
from events.types import (
    StreamChunkEvent,
    StreamFinishedEvent,
    StreamStartedEvent,
)


# Sentences below this are joined to the next one. Speech has a cost per
# utterance that text does not: "Yeah." on its own sounds clipped, and
# with Edge it is also a whole network round trip for one word.
MIN_SPOKEN_CHARS = 24

# How long to wait for the queue to drain on shutdown. Long enough for a
# sentence in flight, short enough not to hang an exit.
DRAIN_TIMEOUT = 10.0


class StreamingSpeaker:
    """
    Speaks a reply sentence by sentence as it streams.

    Wraps anything with a `speak(text)` - the TTSEngine normally, so that
    SpeakingEvent still comes from one place and the avatar sees no
    difference between a streamed reply and an ordinary one.
    """

    def __init__(
        self,
        engine,
        min_chars: int = MIN_SPOKEN_CHARS,
        start_worker: bool = True,
    ):
        """
        `start_worker=False` keeps everything on the calling thread, for
        tests: `feed` then `drain` gives the same sentences in the same
        order with no thread and no timing.
        """

        self.engine = engine
        self.min_chars = min_chars

        self._aggregator = SentenceAggregator(min_chars=min_chars)
        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._threaded = start_worker
        self._stopping = threading.Event()
        self._releases: list = []

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def attach(self, bus):
        """Follow the stream on a bus. Returns a detach callable."""

        self._releases = [
            bus.subscribe(StreamStartedEvent, self._on_started),
            bus.subscribe(StreamChunkEvent, self._on_chunk),
            bus.subscribe(StreamFinishedEvent, self._on_finished),
        ]

        if self._threaded:
            self._ensure_worker()

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

        self.stop()

    # ------------------------------------------------------------------
    # The stream
    # ------------------------------------------------------------------

    def _on_started(self, event: StreamStartedEvent) -> None:
        """
        A new reply. Drop anything left from the last one.

        A stream that ended in an error can leave half a sentence in the
        buffer; speaking it at the front of the next reply would be worse
        than losing it.
        """

        self._aggregator = SentenceAggregator(min_chars=self.min_chars)

    def _on_chunk(self, event: StreamChunkEvent) -> None:
        self.feed(getattr(event, "text", ""))

    def _on_finished(self, event: StreamFinishedEvent) -> None:
        """
        The reply ended.

        The tail is spoken on success. On failure it is dropped - a
        partial sentence read aloud sounds like Aura trailed off, and the
        error is already on the bus for anything that wants to report it.
        """

        tail = self._aggregator.flush()

        if tail and getattr(event, "ok", True):
            self._enqueue(tail)

    # ------------------------------------------------------------------
    # Speaking
    # ------------------------------------------------------------------

    def feed(self, fragment: str) -> list[str]:
        """
        Add a fragment; queue whatever sentences it completed.

        Returns them, which is only of interest to a test - the caller on
        the bus ignores it.
        """

        sentences = self._aggregator.feed(fragment)

        for sentence in sentences:
            self._enqueue(sentence)

        return sentences

    def drain(self) -> int:
        """
        Speak everything queued, on this thread. Returns how many.

        The synchronous counterpart to the worker, used by tests and by
        any host that would rather own the thread itself.
        """

        spoken = 0

        while True:
            try:
                sentence = self._queue.get_nowait()
            except queue.Empty:
                break

            if sentence is None:
                break

            self._say(sentence)
            spoken += 1

        return spoken

    def cancel(self) -> None:
        """
        Abandon the rest of this reply.

        Three things have to happen and the order matters: the queue is
        emptied first so the worker cannot pick up another sentence, the
        aggregator is reset so no half sentence survives into the next
        reply, and only then is the engine asked to stop - otherwise the
        sentence that was cut short is immediately followed by the next
        one, which is the opposite of cancelling.

        The worker itself is left running. It is idle once the queue is
        empty, and keeping it alive means the next reply starts speaking
        without paying for a thread again.
        """

        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        self._aggregator = SentenceAggregator(min_chars=self.min_chars)

        stop = getattr(self.engine, "stop", None)

        if stop is None:
            return

        try:
            stop()
        except Exception as error:
            logger.warning("Could not stop speech: %s", error)

    def stop(self, timeout: float = DRAIN_TIMEOUT) -> None:
        """Finish the sentence in flight, then let the worker exit."""

        self._stopping.set()
        self._queue.put(None)

        worker = self._worker

        if worker is not None and worker.is_alive():
            worker.join(timeout)

        self._worker = None

    # ------------------------------------------------------------------

    def _enqueue(self, sentence: str) -> None:

        if not sentence or not sentence.strip():
            return

        self._queue.put(sentence)

        if self._threaded:
            self._ensure_worker()

    def _ensure_worker(self) -> None:

        if self._worker is not None and self._worker.is_alive():
            return

        self._stopping.clear()

        self._worker = threading.Thread(
            target=self._run,
            name="aura-streaming-speech",
            daemon=True,
        )

        self._worker.start()

    def _run(self) -> None:

        while not self._stopping.is_set():

            sentence = self._queue.get()

            if sentence is None:
                break

            self._say(sentence)

    def _say(self, sentence: str) -> None:

        try:
            self.engine.speak(sentence)
        except Exception as error:
            # One failed sentence must not end the reply. The next one
            # may well succeed - a dropped packet is not a dead speaker.
            logger.warning("Streaming speech failed: %s", error)

    def __repr__(self) -> str:
        return f"StreamingSpeaker(queued={self._queue.qsize()})"


__all__ = ["StreamingSpeaker", "MIN_SPOKEN_CHARS"]
