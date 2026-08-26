"""
What Aura has already said unprompted, on disk.

Every limit the owner sets on proactive messaging - `max_per_day`, the
global cooldown, the per-category cooldown, the duplicate window and the
similarity threshold - is answered from the same short history of sends.
`ProactivePolicy` holds that history in a `deque`, which is the right
structure and was the wrong lifetime: it died with the process.

The consequence is the owner's configuration quietly not being honoured.
They ask for no more than four messages a day, Aura sends four, the
server restarts, and four more are allowed - the limit is not overridden
by anything, it simply forgets. Section 20 says do not spam
notifications; section 2 says AURA must not silently override owner
configuration. A cap that resets itself on every reboot satisfies
neither, and a desktop process restarts often: a crash, an upgrade, a
reload, a closed laptop.

Section 19 is the phase this belongs to, and its own constraint decides
the shape: the target is not a process that never dies but a system that
survives being killed.

Why a JSON file rather than a table
-----------------------------------
`data/settings.json` is the precedent, and this is the same kind of
state: small, bounded, rewritten whole, and not a kind of knowing.
`memory/models.py` says in its own docstring that its three tables are
"one per kind of knowing" and that temporary state is deliberately kept
out so it "cannot silently become permanent" - a send ledger is neither
a fact about the owner nor an episode, and adding it there would argue
against that file's stated purpose. The atomic write below is copied
from `core/settings_store.py` for the same reason its own is atomic: a
half-written file read at the next start would be worse than no file.

What is stored, and what that means
-----------------------------------
The time, the category, and the message text. The text is not optional
bookkeeping - `duplicate_window_seconds` and `similarity_threshold` are
questions about what was said, and cannot be answered from timestamps.
It is Aura's own words rather than the owner's, and the transcript is
already persisted, so this adds no new class of data to the disk. It is
still content: `events/log.py` denies strings by default, so none of it
reaches a log line.

The bound lives in one place
----------------------------
This module has no size limit of its own. `save` writes exactly what it
is handed, and the caller hands it the contents of a `deque(maxlen=...)`,
so the deque's bound is the only definition of how much history exists
and the file cannot drift from it. A second limit here would be a second
mechanism for a guarantee the caller already makes.
"""

import json
import os
from datetime import datetime
from pathlib import Path

from core.logger import logger
from core.paths import DATA_DIR
from core.temporal import parse_timestamp


DEFAULT_PATH = DATA_DIR / "proactive.json"

# One entry, as it appears on disk and in memory: when, category, text.
Entry = tuple[datetime, str, str]


class SendLedger:
    """
    Loads and saves the proactive send history.

    Not thread-safe and deliberately not locked: `ProactivePolicy`
    already holds a lock around the history this reads and writes, and a
    second lock underneath it would be a second opinion about the same
    critical section.
    """

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else DEFAULT_PATH

    # ------------------------------------------------------------------

    def load(self) -> tuple[Entry, ...]:
        """
        Read the history, dropping anything unreadable.

        Never raises. A ledger that cannot be read is a forgotten limit,
        which is bad; a ledger that cannot be read and stops Aura
        starting is worse, and section 41 is explicit that existing data
        is not to be destroyed - so an unreadable file is left exactly
        where it is rather than replaced with a valid empty one.
        """

        if not self.path.exists():
            return ()

        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as error:
            # The class name only. The file holds message text, and a
            # decoder's error message quotes the input it choked on.
            logger.warning(
                "Proactive ledger at %s is unreadable (%s); ignoring it",
                self.path, type(error).__name__,
            )
            return ()

        if not isinstance(stored, dict):
            return ()

        rows = stored.get("sends")

        if not isinstance(rows, list):
            return ()

        entries: list[Entry] = []

        for row in rows:
            entry = _read_row(row)

            if entry is not None:
                entries.append(entry)

        # Oldest first, matching the deque the caller keeps: every reader
        # of that history walks it in reverse to find the most recent,
        # and a file in the other order would invert all of them.
        entries.sort(key=lambda item: item[0])

        return tuple(entries)

    def save(self, entries) -> None:
        """
        Replace the file with exactly these entries.

        Whole-file rather than append-only, because the caller's bound is
        a `maxlen` and an append-only file would grow past it. A handful
        of sends a day against sixty-odd rows costs nothing.

        Never raises either. A failed write means the next start forgets
        a limit; an exception here would mean a message the owner allowed
        is not delivered, which is the more visible failure and the one
        they did not ask for.
        """

        document = json.dumps(
            {
                "version": 1,
                "sends": [
                    {
                        "at": when.isoformat(),
                        "category": str(category),
                        "message": str(message),
                    }
                    for when, category, message in entries
                ],
            },
            indent=2,
            ensure_ascii=False,
        )

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)

            temporary = self.path.with_suffix(self.path.suffix + ".tmp")

            temporary.write_text(document, encoding="utf-8")

            os.replace(temporary, self.path)

        except Exception as error:
            logger.warning(
                "Could not write the proactive ledger to %s (%s)",
                self.path, type(error).__name__,
            )


def _read_row(row) -> Entry | None:
    """One stored send, or None if it is not one."""

    if not isinstance(row, dict):
        return None

    when = parse_timestamp(row.get("at"))

    if when is None:
        return None

    category = row.get("category")
    message = row.get("message")

    if not isinstance(category, str) or not isinstance(message, str):
        return None

    return (when, category, message)


__all__ = ["SendLedger", "DEFAULT_PATH", "Entry"]
