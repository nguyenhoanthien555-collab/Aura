"""
Remote screen observations.

A phone cannot be screenshotted by the server, so the direction of the
vision pipeline inverts: the device pushes what it sees, and the server
holds the most recent push.

Nothing new is invented for this. `RemoteScreenSource` implements the two
capture ports that already exist -

    vision.capture.WindowReader   -> active_window()
    vision.capture.ScreenCapture  -> capture() / is_available()

- and `RemoteScreenProcessor` implements `vision.processor.VisionProcessor`.
So the ordinary `VisionManager` drives them, and every guarantee it
already makes (off by default, throttled, silent on failure, an event
only when the description changed) applies to a phone exactly as it does
to a desktop. `brain/` sees a `VisionContext` and cannot tell the
difference.
"""

import time
from dataclasses import dataclass, field
from threading import Lock

from vision.capture import Frame
from vision.processor import MAX_DESCRIPTION


# A device that has stopped reporting should not keep answering for the
# screen it last showed. Past this age the source goes quiet.
DEFAULT_MAX_AGE = 90.0


@dataclass(frozen=True)
class ScreenObservation:
    """
    One screen, as reported by a device.

    Every field is optional because Android accessibility data is
    best-effort: some apps expose a label and no text, some expose text
    and no label, and a secure window exposes neither.
    """

    application: str = ""
    package: str = ""
    screen_text: str = ""
    accessibility_context: str = ""
    device_id: str = ""
    received_at: float = 0.0
    frame: Frame | None = None

    def is_empty(self) -> bool:
        return not (
            self.application.strip()
            or self.package.strip()
            or self.screen_text.strip()
            or self.accessibility_context.strip()
        )

    def fingerprint(self) -> str:
        """
        What "the same screen" means.

        Deliberately excludes `received_at` and `device_id`: the same
        screen reported twice a second apart is the same screen.
        """

        return "\n".join((
            self.package.strip(),
            self.application.strip(),
            " ".join(self.screen_text.split()),
            " ".join(self.accessibility_context.split()),
        ))


class RemoteScreenSource:
    """
    The latest observation pushed by a device.

    Thread-safe: pushes arrive on request threads while the conversation
    thread reads. A single slot, not a queue - a companion cares about
    what is on screen now, and a backlog of stale screens is worse than
    none.
    """

    def __init__(
        self,
        max_age: float = DEFAULT_MAX_AGE,
        clock=time.monotonic,
    ):
        self.max_age = max_age
        self.clock = clock

        self._lock = Lock()
        self._latest: ScreenObservation | None = None
        self._received_at: float | None = None
        self.submissions = 0

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def submit(self, observation: ScreenObservation) -> ScreenObservation | None:
        """
        Record an observation. Returns it, or None when it said nothing.
        """

        if observation.is_empty():
            return None

        with self._lock:
            self._latest = observation
            self._received_at = self.clock()
            self.submissions += 1

        return observation

    def latest(self) -> ScreenObservation | None:
        """The current observation, or None when there is none or it aged out."""

        with self._lock:

            if self._latest is None or self._received_at is None:
                return None

            if self.clock() - self._received_at > self.max_age:
                return None

            return self._latest

    def clear(self) -> None:
        with self._lock:
            self._latest = None
            self._received_at = None

    # ------------------------------------------------------------------
    # Port: WindowReader
    # ------------------------------------------------------------------

    def active_window(self) -> str:
        """The foreground app, in the shape a window title would have."""

        observation = self.latest()

        if observation is None:
            return ""

        return observation.application or observation.package or ""

    # ------------------------------------------------------------------
    # Port: ScreenCapture
    # ------------------------------------------------------------------

    def capture(self) -> Frame | None:
        """The pushed screenshot, when the device sent one."""

        observation = self.latest()

        if observation is None:
            return None

        return observation.frame

    def is_available(self) -> bool:
        return self.latest() is not None


class RemoteScreenProcessor:
    """
    Describes a pushed screen in one plain sentence.

    Reads the observation from the source rather than from `describe`'s
    arguments: the `VisionProcessor` signature carries a frame and a
    title, and Android supplies rather more than a title.

    No model is called here. This is the zero-dependency description, the
    mobile counterpart of `WindowTitleProcessor` - an image model can be
    dropped in later by passing a different processor to VisionManager.
    """

    def __init__(
        self,
        source: RemoteScreenSource,
        max_text: int = 400,
    ):
        self.source = source
        self.max_text = max_text

    def describe(self, frame: Frame | None, window_title: str = "") -> str:

        observation = self.source.latest()

        if observation is None:
            return ""

        app = (
            observation.application.strip()
            or observation.package.strip()
        )

        if app:
            description = f"User is using {app} on their phone"
        else:
            description = "User is on their phone"

        detail = self._detail(observation)

        if detail:
            description = f"{description}. On screen: {detail}"

        return description[:MAX_DESCRIPTION]

    def _detail(self, observation: ScreenObservation) -> str:

        parts = [
            " ".join(observation.screen_text.split()),
            " ".join(observation.accessibility_context.split()),
        ]

        detail = " | ".join(part for part in parts if part)

        return detail[: self.max_text]


def build_remote_vision(
    events=None,
    min_interval: float = 8.0,
    enabled: bool = False,
    max_age: float = DEFAULT_MAX_AGE,
):
    """
    A VisionManager fed by a device, and the source to push into.

    Returns (manager, source). The manager is an ordinary VisionManager -
    it is the *inputs* that are remote, not the machinery.
    """

    from vision.manager import VisionManager

    source = RemoteScreenSource(max_age=max_age)

    manager = VisionManager(
        capture=source,
        processor=RemoteScreenProcessor(source),
        window_reader=source,
        events=events,
        enabled=enabled,
        min_interval=min_interval,
        source="phone",
    )

    return manager, source
