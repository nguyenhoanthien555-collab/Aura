"""
Remote screen observations.

The claim under test is that a phone can drive the existing vision
pipeline without the pipeline knowing: `RemoteScreenSource` satisfies the
capture ports, `RemoteScreenProcessor` satisfies the processor port, and
an ordinary `VisionManager` runs both.
"""

import pytest

from events.bus import EventBus
from events.types import VisionUpdateEvent
from vision.capture import Frame, ScreenCapture, WindowReader
from vision.processor import VisionProcessor
from vision.remote import (
    RemoteScreenProcessor,
    RemoteScreenSource,
    ScreenObservation,
    build_remote_vision,
)


class FakeClock:
    """A clock that only moves when a test says so."""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def observation(**overrides) -> ScreenObservation:
    fields = {
        "application": "Gmail",
        "package": "com.google.android.gm",
        "screen_text": "Inbox 3 unread",
        "device_id": "phone-1",
    }
    fields.update(overrides)
    return ScreenObservation(**fields)


# ----------------------------------------------------------------------
# ScreenObservation
# ----------------------------------------------------------------------

class TestScreenObservation:

    def test_an_observation_with_no_content_is_empty(self):
        assert ScreenObservation().is_empty()
        assert ScreenObservation(application="   ").is_empty()

    def test_any_one_field_is_enough_to_be_non_empty(self):
        assert not ScreenObservation(application="Gmail").is_empty()
        assert not ScreenObservation(package="com.foo").is_empty()
        assert not ScreenObservation(screen_text="hello").is_empty()
        assert not ScreenObservation(accessibility_context="button").is_empty()

    def test_the_same_screen_reported_twice_has_one_fingerprint(self):
        first = observation(received_at=1.0, device_id="a")
        second = observation(received_at=999.0, device_id="b")

        # Time and device are deliberately excluded: it is the same screen.
        assert first.fingerprint() == second.fingerprint()

    def test_different_content_gives_a_different_fingerprint(self):
        assert (
            observation().fingerprint()
            != observation(screen_text="Inbox 4 unread").fingerprint()
        )


# ----------------------------------------------------------------------
# RemoteScreenSource
# ----------------------------------------------------------------------

class TestRemoteScreenSource:

    def test_it_satisfies_both_capture_ports(self):
        source = RemoteScreenSource()

        # This is the whole point of the design: no new port, no Vision2.
        assert isinstance(source, ScreenCapture)
        assert isinstance(source, WindowReader)

    def test_nothing_pushed_means_nothing_to_read(self):
        source = RemoteScreenSource()

        assert source.latest() is None
        assert source.active_window() == ""
        assert source.capture() is None
        assert not source.is_available()

    def test_a_pushed_observation_is_the_latest(self):
        source = RemoteScreenSource()

        accepted = source.submit(observation())

        assert accepted is not None
        assert source.latest() == observation()
        assert source.submissions == 1

    def test_an_empty_observation_is_refused(self):
        source = RemoteScreenSource()

        assert source.submit(ScreenObservation()) is None
        assert source.latest() is None
        assert source.submissions == 0

    def test_only_the_newest_screen_is_kept(self):
        source = RemoteScreenSource()

        source.submit(observation(application="Gmail"))
        source.submit(observation(application="Chrome"))

        assert source.latest().application == "Chrome"

    def test_a_stale_observation_stops_answering(self):
        clock = FakeClock()
        source = RemoteScreenSource(max_age=90.0, clock=clock)

        source.submit(observation())
        assert source.latest() is not None

        clock.advance(91.0)

        # A device that stopped reporting must not keep speaking for the
        # screen it last showed.
        assert source.latest() is None
        assert source.active_window() == ""
        assert not source.is_available()

    def test_active_window_falls_back_to_the_package(self):
        source = RemoteScreenSource()

        source.submit(observation(application="", package="com.foo.bar"))

        assert source.active_window() == "com.foo.bar"

    def test_a_pushed_frame_is_returned_by_capture(self):
        source = RemoteScreenSource()
        frame = Frame(data=b"png-bytes", image_format="png", source="phone")

        source.submit(observation(frame=frame))

        assert source.capture() is frame

    def test_clear_forgets_everything(self):
        source = RemoteScreenSource()
        source.submit(observation())

        source.clear()

        assert source.latest() is None


# ----------------------------------------------------------------------
# RemoteScreenProcessor
# ----------------------------------------------------------------------

class TestRemoteScreenProcessor:

    def test_it_satisfies_the_processor_port(self):
        source = RemoteScreenSource()

        assert isinstance(RemoteScreenProcessor(source), VisionProcessor)

    def test_nothing_observed_describes_nothing(self):
        processor = RemoteScreenProcessor(RemoteScreenSource())

        assert processor.describe(None, "") == ""

    def test_it_names_the_app_and_the_screen(self):
        source = RemoteScreenSource()
        source.submit(observation())

        described = RemoteScreenProcessor(source).describe(None, "")

        assert "Gmail" in described
        assert "phone" in described
        assert "Inbox 3 unread" in described

    def test_an_app_with_no_text_still_describes_the_app(self):
        source = RemoteScreenSource()
        source.submit(ScreenObservation(application="Spotify"))

        assert RemoteScreenProcessor(source).describe(None, "") == (
            "User is using Spotify on their phone"
        )

    def test_long_screen_text_is_truncated(self):
        source = RemoteScreenSource()
        source.submit(observation(screen_text="word " * 2000))

        described = RemoteScreenProcessor(source, max_text=100).describe(None, "")

        assert len(described) < 400


# ----------------------------------------------------------------------
# The assembled pipeline
# ----------------------------------------------------------------------

class TestBuildRemoteVision:

    def test_it_returns_an_ordinary_vision_manager(self):
        from vision.manager import VisionManager

        manager, source = build_remote_vision(enabled=True)

        assert isinstance(manager, VisionManager)
        assert isinstance(source, RemoteScreenSource)
        assert manager.source == "phone"

    def test_disabled_by_default(self):
        manager, _ = build_remote_vision()

        assert not manager.enabled
        assert manager.get_context() is None

    def test_a_pushed_screen_reaches_the_vision_context(self):
        manager, source = build_remote_vision(enabled=True)

        source.submit(observation())

        context = manager.get_context()

        assert context is not None
        assert context.source == "phone"
        assert "Gmail" in context.description

    def test_a_changed_screen_publishes_one_event(self):
        bus = EventBus()
        seen = []
        bus.subscribe(VisionUpdateEvent, seen.append)

        manager, source = build_remote_vision(
            events=bus, enabled=True, min_interval=0.0
        )

        source.submit(observation(application="Gmail"))
        manager.refresh()

        source.submit(observation(application="Chrome"))
        manager.refresh()

        assert len(seen) == 2
        assert seen[0].source == "phone"

    def test_the_same_screen_twice_publishes_once(self):
        bus = EventBus()
        seen = []
        bus.subscribe(VisionUpdateEvent, seen.append)

        manager, source = build_remote_vision(
            events=bus, enabled=True, min_interval=0.0
        )

        source.submit(observation())
        manager.refresh()
        manager.refresh()

        # VisionManager's "only when it changed" guarantee, inherited
        # unchanged by the remote path.
        assert len(seen) == 1

    def test_a_device_that_went_quiet_produces_no_context(self):
        clock = FakeClock()
        manager, source = build_remote_vision(enabled=True, min_interval=0.0)
        source.clock = clock
        source.max_age = 30.0

        source.submit(observation())
        assert manager.refresh() is not None

        clock.advance(31.0)

        assert manager.refresh() is None
