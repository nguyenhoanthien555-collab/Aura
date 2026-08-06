"""
Floating avatar window.

Tkinter, because it ships with Python: a companion that needs a 300 MB
GUI toolkit before it can wave at you is a companion nobody installs.

    borderless   overrideredirect(True), no taskbar entry
    always on top  -topmost
    draggable    press and move anywhere on the body
    scalable     mouse wheel, or set_scale()

There is no AI logic here and no event subscription. The window is told a
state and draws it. Everything it knows arrives through `set_state`.

Live2D / sprite path: drop `idle.png`, `listening.png`, `thinking.png`
and `speaking.png` into the configured sprite directory and they are used
instead of the placeholder shape. Tk 8.6 reads PNG natively, so this
needs no extra dependency.
"""

import os
from typing import Callable

from core.logger import logger
from events.types import AuraState


DEFAULT_SIZE = 160

# Placeholder palette, one colour per state. Replaced the moment real
# sprites are present.
STATE_COLOURS = {
    AuraState.IDLE: "#4b6cb7",
    AuraState.LISTENING: "#2ecc71",
    AuraState.THINKING: "#f1c40f",
    AuraState.SPEAKING: "#e67e22",
}

# Any pixel in this colour becomes see through on Windows, which is what
# makes the avatar look like it floats on the desktop instead of sitting
# in a grey box.
TRANSPARENT_KEY = "#ff00ff"

MIN_SCALE = 0.5
MAX_SCALE = 3.0


def is_display_available() -> bool:
    """
    Whether a GUI can be created at all.

    Checked before construction so a headless run falls back to the null
    renderer instead of raising TclError deep inside startup.
    """

    if os.name != "nt" and not os.environ.get("DISPLAY"):
        return False

    try:
        import tkinter             # noqa: F401, PLC0415
        return True
    except Exception:
        return False


class TkAvatarWindow:

    def __init__(
        self,
        size: int = DEFAULT_SIZE,
        scale: float = 1.0,
        alpha: float = 0.95,
        position: tuple[int, int] | None = None,
        sprites_dir: str | None = None,
        on_close: Callable[[], None] | None = None,
    ):

        self.base_size = max(48, int(size))
        self.scale = self._clamp_scale(scale)
        self.alpha = max(0.1, min(1.0, alpha))
        self.position = position
        self.sprites_dir = sprites_dir
        self.on_close = on_close

        self.state = AuraState.IDLE
        self.closed = False

        self._root = None
        self._canvas = None
        self._body = None
        self._label = None
        self._sprites: dict[AuraState, object] = {}
        self._drag_origin = (0, 0)

        self._build()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @staticmethod
    def _clamp_scale(scale: float) -> float:
        return max(MIN_SCALE, min(MAX_SCALE, float(scale)))

    @property
    def size(self) -> int:
        return int(self.base_size * self.scale)

    def _build(self) -> None:

        import tkinter as tk        # noqa: PLC0415

        self._root = tk.Tk()

        self._root.title("Aura")
        self._root.overrideredirect(True)
        self._root.attributes("-topmost", True)

        try:
            self._root.attributes("-alpha", self.alpha)
        except Exception:
            pass

        # Windows only; elsewhere the backdrop stays opaque, which is
        # cosmetic rather than fatal.
        try:
            self._root.attributes("-transparentcolor", TRANSPARENT_KEY)
        except Exception:
            pass

        self._canvas = tk.Canvas(
            self._root,
            highlightthickness=0,
            bd=0,
            bg=TRANSPARENT_KEY,
        )

        self._canvas.pack(fill="both", expand=True)

        self._load_sprites()
        self._apply_geometry()
        self._draw()
        self._bind()

    def _apply_geometry(self) -> None:

        size = self.size

        if self.position is None:
            # Bottom right, clear of the taskbar.
            screen_width = self._root.winfo_screenwidth()
            screen_height = self._root.winfo_screenheight()

            self.position = (
                max(0, screen_width - size - 40),
                max(0, screen_height - size - 120),
            )

        x, y = self.position

        self._root.geometry(f"{size}x{size}+{int(x)}+{int(y)}")

    def _bind(self) -> None:

        for widget in (self._root, self._canvas):
            widget.bind("<Button-1>", self._on_press)
            widget.bind("<B1-Motion>", self._on_drag)
            widget.bind("<MouseWheel>", self._on_wheel)

        self._root.bind("<Escape>", lambda _event: self.close())
        self._root.protocol("WM_DELETE_WINDOW", self.close)

    # ------------------------------------------------------------------
    # Sprites
    # ------------------------------------------------------------------

    def _load_sprites(self) -> None:
        """
        Load one PNG per state, if a sprite directory was configured.

        Missing files are not an error - any state without a sprite falls
        back to the placeholder shape, so a half finished sprite set
        still works.
        """

        if not self.sprites_dir:
            return

        directory = str(self.sprites_dir)

        if not os.path.isdir(directory):
            logger.debug("Sprite directory not found: %s", directory)
            return

        import tkinter as tk        # noqa: PLC0415

        for state in AuraState:

            path = os.path.join(directory, f"{state.value}.png")

            if not os.path.isfile(path):
                continue

            try:
                self._sprites[state] = tk.PhotoImage(
                    master=self._root,
                    file=path,
                )
            except Exception as error:
                logger.debug("Sprite load failed for %s: %s", state, error)

    # ------------------------------------------------------------------
    # Drawing
    # ------------------------------------------------------------------

    def _draw(self) -> None:

        if self._canvas is None:
            return

        self._canvas.delete("all")

        size = self.size

        self._canvas.configure(width=size, height=size)

        sprite = self._sprites.get(self.state)

        if sprite is not None:

            self._body = self._canvas.create_image(
                size // 2,
                size // 2,
                image=sprite,
            )

        else:
            padding = max(4, size // 10)

            self._body = self._canvas.create_oval(
                padding,
                padding,
                size - padding,
                size - padding,
                fill=STATE_COLOURS.get(self.state, "#4b6cb7"),
                outline="",
            )

        self._label = self._canvas.create_text(
            size // 2,
            size - max(8, size // 12),
            text=self.state.value,
            fill="#ffffff",
            font=("Segoe UI", max(7, size // 16)),
        )

    # ------------------------------------------------------------------
    # Renderer interface
    # ------------------------------------------------------------------

    def set_state(self, state: AuraState) -> None:
        """
        Show a state.

        Events can be published from any thread, so the redraw is handed
        to the Tk thread with `after()` rather than touching widgets
        directly. Calling Tk from a foreign thread is the classic way to
        make a GUI die at random.
        """

        self.state = state

        if self.closed or self._root is None:
            return

        try:
            self._root.after(0, self._draw)
        except Exception:
            pass

    def set_scale(self, scale: float) -> None:

        self.scale = self._clamp_scale(scale)

        if self.closed or self._root is None:
            return

        try:
            self._root.after(0, self._resize)
        except Exception:
            pass

    def _resize(self) -> None:

        self._apply_geometry()
        self._draw()

    def show(self) -> None:

        if self._root is not None and not self.closed:
            self._root.deiconify()

    def hide(self) -> None:

        if self._root is not None and not self.closed:
            self._root.withdraw()

    def close(self) -> None:

        if self.closed:
            return

        self.closed = True

        if self.on_close is not None:
            try:
                self.on_close()
            except Exception:
                pass

        if self._root is not None:
            try:
                self._root.destroy()
            except Exception:
                pass

    def is_available(self) -> bool:
        return not self.closed

    # ------------------------------------------------------------------
    # Event loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """
        Block on the Tk main loop.

        Must be called from the thread that constructed the window. The
        launcher runs this on the main thread and puts the chat loop on a
        worker, which is the arrangement Tk is happiest with.
        """

        if self._root is None or self.closed:
            return

        try:
            self._root.mainloop()
        except Exception as error:
            logger.debug("Avatar loop ended: %s", error)

    def pump(self) -> bool:
        """
        Process pending GUI work and return immediately.

        The alternative to `run()`, for callers that already own the main
        thread. Returns False once the window is gone.
        """

        if self._root is None or self.closed:
            return False

        try:
            self._root.update()
            return True
        except Exception:
            self.closed = True
            return False

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def _on_press(self, event) -> None:
        self._drag_origin = (event.x, event.y)

    def _on_drag(self, event) -> None:

        offset_x, offset_y = self._drag_origin

        x = event.x_root - offset_x
        y = event.y_root - offset_y

        self.position = (x, y)

        try:
            self._root.geometry(f"+{int(x)}+{int(y)}")
        except Exception:
            pass

    def _on_wheel(self, event) -> None:

        step = 0.1 if event.delta > 0 else -0.1

        self.set_scale(self.scale + step)
