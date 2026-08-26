"""
Notification policy.

The last gate, and the only one that can veto a genuinely relevant
thought. Relevance asks "is this worth saying"; policy asks "is now a
reasonable moment to say it".

They are separate because they fail differently. A wrong relevance call
is a bad remark. A missing policy is a companion that talks over you all
evening.

Every rule here defaults to silence:

    disabled            off until the user turns it on
    quiet hours         nothing at all
    active chat         she is already in the conversation
    cooldown            one thing at a time
    hourly ceiling      survives a stuck relevance score
    duplicate           she already said this
"""

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Lock

from companion.decision import CompanionDecision, Priority
from core.logger import logger
from core.paths import DATA_DIR
from core.temporal import in_quiet_hours


DEFAULT_COOLDOWN = 300.0
DEFAULT_MAX_PER_HOUR = 6
DEFAULT_THRESHOLD = 0.7
DEFAULT_SUPPRESS_AFTER_CHAT = 120.0

# How long a message counts as "already said". A default rather than a
# constant since phase 14: the proactive gate next door has made this the
# owner's number for a while, and two notions of "already said this" that
# can disagree is one too many.
DEFAULT_DUPLICATE_WINDOW = 1800.0

# Where the durable history lives when a composition root supplies one.
# A separate file from `proactive.json` on purpose: sharing it would make
# each gate count the other's sends, turning "four a day" and "six an
# hour" into one budget the owner never asked for (section 2).
LEDGER_PATH = DATA_DIR / "companion.json"

# What goes in the ledger's category column. This gate has no per-category
# rule; the column is there so a human opening the file can tell which of
# Aura's two mouths wrote the row.
LEDGER_CATEGORY = "companion"


def _default_wall_clock() -> datetime:
    """
    Naive local wall time, matching what the ledger already stores.

    Deliberately not `core.temporal.local_now`, which honours the owner's
    configured zone: a stored reading and the reading it is subtracted from
    must come from the same frame, and a policy built before the owner set
    a timezone would otherwise compare across two. Ages are all this is
    ever used for, and an age does not care which zone it was measured in
    as long as both ends agree.
    """

    return datetime.now()


@dataclass
class PolicySettings:
    """
    Tuning for the policy, read from `server.companion` in config.yaml.

    `quiet_hours` is a list of [start_hour, end_hour] pairs in local
    time. A pair may wrap midnight: [23, 7] means 23:00 to 07:00.
    """

    enabled: bool = False
    relevance_threshold: float = DEFAULT_THRESHOLD
    cooldown_seconds: float = DEFAULT_COOLDOWN
    max_per_hour: int = DEFAULT_MAX_PER_HOUR
    quiet_hours: list = field(default_factory=list)
    suppress_after_chat_seconds: float = DEFAULT_SUPPRESS_AFTER_CHAT
    duplicate_window_seconds: float = DEFAULT_DUPLICATE_WINDOW

    @classmethod
    def from_config(cls, config: dict | None) -> "PolicySettings":
        """
        Build from the `server.companion` section, tolerating a missing
        or partial one.
        """

        settings = config or {}

        return cls(
            enabled=bool(settings.get("enabled", False)),
            relevance_threshold=float(
                settings.get("relevance_threshold", DEFAULT_THRESHOLD)
            ),
            cooldown_seconds=float(
                settings.get("cooldown_seconds", DEFAULT_COOLDOWN)
            ),
            max_per_hour=int(settings.get("max_per_hour", DEFAULT_MAX_PER_HOUR)),
            quiet_hours=list(settings.get("quiet_hours") or []),
            suppress_after_chat_seconds=float(
                settings.get(
                    "suppress_after_chat_seconds", DEFAULT_SUPPRESS_AFTER_CHAT
                )
            ),
            duplicate_window_seconds=float(
                settings.get(
                    "duplicate_window_seconds", DEFAULT_DUPLICATE_WINDOW
                )
            ),
        )


class CompanionPolicy:
    """
    Decides whether a relevant thought may be spoken now.

    Thread-safe: screen observations arrive on request threads.

    `clock` is monotonic time for intervals; `local_hour` is wall-clock
    hour for quiet hours. Both are injected so the rules can be tested
    without waiting or without it being 3am.

    `wall_clock` is a third clock and it earns its place: monotonic time is
    meaningless in the next process, so the durable record has to be kept
    in wall time and translated back into this process's frame on load.
    Monotonic still owns every interval the rules compare - all `wall_clock`
    ever does is cross the disk.

    `ledger` and `last_user_message` are the two things that make the rules
    outlive the process, and both arrive from a composition root rather
    than defaulting to something real. That is the reasoning `core/app.py`
    records for the clock: a bare policy has to stay exactly what the tests
    built before phase 14, so a file path and a database handle are the
    caller's business.
    """

    def __init__(
        self,
        settings: PolicySettings | None = None,
        clock=time.monotonic,
        local_hour=None,
        ledger=None,
        wall_clock=None,
        last_user_message=None,
    ):
        self.settings = settings or PolicySettings()
        self.clock = clock
        self.local_hour = local_hour or (lambda: time.localtime().tm_hour)
        self.ledger = ledger
        self.wall_clock = wall_clock or _default_wall_clock
        self.last_user_message = last_user_message

        self._lock = Lock()
        self._last_chat: float | None = None
        self._recent: deque = deque(maxlen=32)      # (when, message)

        # Loaded once, here, for the reason the proactive policy documents:
        # `allows` asks this history four separate questions under a single
        # lock, and re-reading the file per question would let the answers
        # disagree with each other inside one decision.
        #
        # `_last_notified` used to sit beside `_recent` holding the time of
        # its newest entry. It was the same fact twice (section 8), and with
        # a file underneath it would have been the same fact twice in two
        # places that could disagree, so it is derived now.
        if ledger is not None:
            self._recent.extend(self._restore(ledger.load()))

    # ------------------------------------------------------------------
    # Facts the policy needs from outside
    # ------------------------------------------------------------------

    def note_chat(self) -> None:
        """The user just sent a message. Called by the chat path."""

        with self._lock:
            self._last_chat = self.clock()

    def note_notified(self, message: str) -> None:
        """A notification went out. Starts the cooldown."""

        now = self.clock()

        with self._lock:
            self._recent.append((now, self._normalise(message)))
            self._save()

    def reset(self) -> None:
        with self._lock:
            self._last_chat = None
            self._recent.clear()

            # The file too. `reset` is the owner dropping the limit, and a
            # limit that comes back from disk after being dropped is not a
            # limit the owner controls (section 2).
            self._save()

    def history(self) -> tuple:
        """The send history as the rules see it: (monotonic when, message)."""

        with self._lock:
            return tuple(self._recent)

    # ------------------------------------------------------------------
    # Crossing the process boundary
    # ------------------------------------------------------------------

    def _save(self) -> None:
        """
        Write the history out. Caller holds the lock.

        Inside the lock deliberately, despite being disk I/O: two
        notifications racing here would otherwise each snapshot the deque
        and write in whichever order the filesystem happened to see them,
        and the loser's row would be missing from a file that is supposed
        to be the reason a ceiling holds.

        Monotonic goes in as wall time. `now - when` is how old each entry
        is, and an age is the one thing about a monotonic reading that
        still means something tomorrow.
        """

        if self.ledger is None:
            return

        now = self.clock()
        wall = self.wall_clock()

        self.ledger.save(
            (wall - timedelta(seconds=max(0.0, now - when)),
             LEDGER_CATEGORY,
             message)
            for when, message in self._recent
        )

    def _restore(self, entries) -> list:
        """
        Turn the stored wall times back into this process's monotonic frame.

        Anything older than the longest window any rule looks through is
        dropped rather than carried: it cannot change an answer, and a file
        that only ever grows is a slow leak in a directory the owner does
        not read.
        """

        now = self.clock()
        wall = self.wall_clock()

        horizon = max(
            3600.0,
            float(self.settings.cooldown_seconds or 0.0),
            float(self.settings.duplicate_window_seconds or 0.0),
        )

        restored = []

        for when, _category, message in entries:
            age = (wall - when).total_seconds()

            if age < 0.0 or age > horizon:
                continue

            restored.append((now - age, self._normalise(message)))

        restored.sort(key=lambda item: item[0])

        return restored

    def _presence(self) -> float | None:
        """
        When the owner last spoke, in monotonic terms, or None.

        The live signal first: `note_chat()` is called the moment a request
        arrives, which is earlier and cheaper than any query. The stored
        answer is the fallback that survives a restart, and it is not a
        second copy of the truth - it is the same rows the messages table
        already holds, read through `MemoryManager.last_said_at`.
        """

        with self._lock:
            live = self._last_chat

        if live is not None:
            return live

        if self.last_user_message is None:
            return None

        try:
            answer = self.last_user_message()
        except Exception as error:
            logger.warning("Presence source failed: %s", error)
            return None

        if not isinstance(answer, datetime):
            return None

        age = (self.wall_clock() - answer).total_seconds()

        if age < 0.0:
            return None

        return self.clock() - age

    # ------------------------------------------------------------------
    # The gate
    # ------------------------------------------------------------------

    def allows(self, score: float, message: str) -> CompanionDecision:
        """
        A silent CompanionDecision explaining the refusal, or a
        `should_notify=True` one carrying `message`.

        Order matters: the cheapest and most absolute rules run first, so
        the reason a user reads is the most fundamental one that applied.
        """

        settings = self.settings

        if not settings.enabled:
            return CompanionDecision.silent("companion notifications are off")

        if not message.strip():
            return CompanionDecision.silent("nothing to say", confidence=score)

        if score < settings.relevance_threshold:
            return CompanionDecision.silent(
                f"below relevance threshold "
                f"({score:.2f} < {settings.relevance_threshold:.2f})",
                confidence=score,
            )

        if self._in_quiet_hours():
            return CompanionDecision.silent("quiet hours", confidence=score)

        now = self.clock()

        # Read before the lock: `_presence` takes it itself, and it may go
        # out to the message store, which is not work to do while holding a
        # lock that every screen observation needs.
        last_chat = self._presence()

        with self._lock:
            recent = list(self._recent)

        # The newest send *is* the last notification. Derived rather than
        # stored, so there is no second copy to fall out of step with the
        # file (section 8). `deque(maxlen=...)` always keeps the newest, so
        # the maximum is right even once the history has rolled over.
        last_notified = max((when for when, _ in recent), default=None)

        if (
            last_chat is not None
            and settings.suppress_after_chat_seconds > 0
            and now - last_chat < settings.suppress_after_chat_seconds
        ):
            return CompanionDecision.silent(
                "user is mid-conversation", confidence=score
            )

        if last_notified is not None:
            elapsed = now - last_notified

            if elapsed < settings.cooldown_seconds:
                return CompanionDecision.silent(
                    f"cooling down "
                    f"({settings.cooldown_seconds - elapsed:.0f}s left)",
                    confidence=score,
                )

        if self._is_duplicate(message, recent, now):
            return CompanionDecision.silent(
                "already said this recently", confidence=score
            )

        if self._hourly_ceiling_reached(recent, now):
            return CompanionDecision.silent(
                f"hourly limit reached ({settings.max_per_hour})",
                confidence=score,
            )

        return CompanionDecision(
            should_notify=True,
            reason="relevant, and a reasonable moment",
            priority=self._priority(score),
            message=message.strip(),
            confidence=score,
            cooldown=settings.cooldown_seconds,
        )

    # ------------------------------------------------------------------
    # Rules
    # ------------------------------------------------------------------

    def _in_quiet_hours(self) -> bool:
        """
        Delegated to `core.temporal`, which the proactive scheduler reads
        too. "Quiet" has to mean the same thing to everything in Aura
        that can speak unprompted, and two copies of a midnight-wrapping
        range check would eventually disagree.
        """

        return in_quiet_hours(self.local_hour(), self.settings.quiet_hours)

    @staticmethod
    def _normalise(message: str) -> str:
        return " ".join(message.lower().split())

    def _is_duplicate(self, message: str, recent, now: float) -> bool:

        candidate = self._normalise(message)

        window = self.settings.duplicate_window_seconds

        return any(
            said == candidate and now - when < window
            for when, said in recent
        )

    def _hourly_ceiling_reached(self, recent, now: float) -> bool:

        if self.settings.max_per_hour <= 0:
            return True

        in_last_hour = sum(1 for when, _ in recent if now - when < 3600.0)

        return in_last_hour >= self.settings.max_per_hour

    @staticmethod
    def _priority(score: float) -> Priority:

        if score >= 0.9:
            return Priority.HIGH

        if score >= 0.75:
            return Priority.NORMAL

        return Priority.LOW
