"""
Vision manager.

The only vision object anything outside this package holds. It satisfies
brain.ports.VisionProvider, so ConversationManager can ask what Aura can
see without importing a single line of capture code.

Three deliberate choices:

- Off by default. Looking at someone's screen is not something a
  companion should start doing because a config key was missing.

- Throttled. Turns arrive far faster than the screen meaningfully
  changes, so observations are reused for `min_interval` seconds.

- Silent on failure. A missing display or a revoked permission yields
  None, never an exception, because vision is an enhancement to a turn
  and never a precondition for one.
"""

import time
from typing import Callable

from core.logger import logger
from events.types import VisionUpdateEvent

from vision.capture import (
    ScreenCapture,
    WindowReader,
    default_window_reader,
)

from vision.context import VisionContext
from vision.processor import VisionProcessor, WindowTitleProcessor


DEFAULT_MIN_INTERVAL = 2.0


class VisionManager:

    def __init__(
        self,
        capture: ScreenCapture | None = None,
        processor: VisionProcessor | None = None,
        window_reader: WindowReader | None = None,
        events=None,
        enabled: bool = False,
        min_interval: float = DEFAULT_MIN_INTERVAL,
        source: str = "screen",
        clock: Callable[[], float] = time.monotonic,
    ):

        self.capture = capture

        # Window titles by default: no pixels, no model, no optional
        # dependency. Pixel vision is a composition decision, made by
        # launcher/services.py when config asks for it, by injecting
        # OllamaVisionProcessor here.
        self.processor = processor or WindowTitleProcessor()

        self.window_reader = window_reader or default_window_reader()
        self.events = events
        self.enabled = enabled
        self.min_interval = min_interval
        self.source = source
        self.clock = clock

        self._context: VisionContext | None = None
        self._last_seen: float | None = None


    # ------------------------------------------------------------------
    # Port: VisionProvider
    # ------------------------------------------------------------------

    def get_context(self) -> VisionContext | None:
        """
        The current observation, reusing a recent one when fresh enough.
        """

        if not self.enabled:
            return None

        if self._is_fresh():

            logger.debug(
                "Vision: reusing observation from %.1fs ago (min_interval=%.1fs)",
                self.clock() - (self._last_seen or 0.0),
                self.min_interval,
            )

            return self._context

        return self.refresh()


    def is_available(self) -> bool:
        return self.enabled


    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def refresh(self) -> VisionContext | None:
        """
        Observe now, ignoring the throttle.

        Publishes VisionUpdateEvent only when the description actually
        changed.
        """

        self._last_seen = self.clock()

        description = self._observe()

        if not description:
            self._context = None
            return None


        context = VisionContext(
            source=self.source,
            description=description,
        )


        changed = (
            self._context is None
            or self._context.description != context.description
        )


        self._context = context


        if changed:
            self._emit(
                VisionUpdateEvent(
                    source=context.source,
                    description=context.description,
                )
            )


        return context


    def clear(self) -> None:
        """
        Forget current observation.
        """

        self._context = None
        self._last_seen = None



    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_fresh(self) -> bool:

        if self._last_seen is None:
            return False

        return (
            self.clock() - self._last_seen
        ) < self.min_interval



    def _observe(self) -> str:

        title = self._active_window()
        frame = self._grab()


        logger.debug(
            "Vision: observing at t=%.1f (window=%r, frame=%s)",
            self.clock(),
            title,
            "none" if frame is None
            else f"{frame.width}x{frame.height} {frame.image_format}",
        )


        try:
            return (
                self.processor
                .describe(frame, title)
                or ""
            ).strip()


        except Exception as error:

            logger.debug(
                "Vision processing failed: %s",
                error
            )

            return ""



    def _active_window(self) -> str:

        if self.window_reader is None:
            return ""


        try:
            return self.window_reader.active_window() or ""


        except Exception as error:

            logger.debug(
                "Window read failed: %s",
                error
            )

            return ""



    def _grab(self):

        if self.capture is None:
            return None


        try:
            return self.capture.capture()


        except Exception as error:

            logger.debug(
                "Frame capture failed: %s",
                error
            )

            return None



    def _emit(self, event) -> None:

        if self.events is None:
            return


        try:
            self.events.publish(event)

        except Exception as error:
            # Observation itself succeeded; only the announcement failed.
            # Logged because a UI that stopped showing what Aura can see
            # looks exactly like vision being switched off.
            logger.debug(
                "Vision event publish failed (%s): %s",
                type(event).__name__,
                error,
            )