"""
The anti-spam gate.

Separate from the decision engine on purpose. That one answers "is there
a reason to speak"; this one answers "is it acceptable to speak right
now", and both must say yes. Keeping them apart is what stops a good
reason from overriding the rate limits - which is exactly how a
proactive assistant becomes something people turn off.

Every default here is conservative. The failure mode of a too-quiet
companion is that you miss a reminder. The failure mode of a too-loud one
is that it gets uninstalled, and no amount of good judgement afterwards
recovers from that.

Seven independent rules, each of which can veto alone:

    known category       the closed set, enforced where sending happens
    quiet hours          nothing at all, whatever the reason
    global cooldown      one unprompted message per window, any category
    category cooldown    greetings throttled separately from reminders
    daily maximum        survives a stuck decision engine
    duplicate            she already said exactly this
    similarity           she already said close enough to this

The last one is what the duplicate check cannot do. Two greetings that
differ by one word are not duplicates by string equality and are
absolutely a duplicate to the person reading them.
"""

import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from threading import Lock

from core.temporal import in_quiet_hours, local_now
from memory.retrieval import tokenize
from proactive.decision import Category


# Conservative by design. Two hours between any two unprompted messages.
DEFAULT_COOLDOWN = 7200.0

# Per category, longer. A greeting is welcome once; four times a day it
# is a notification you swipe away without reading.
DEFAULT_CATEGORY_COOLDOWN = {
    Category.GREETING.value: 6 * 3600.0,
    Category.APPRECIATION.value: 24 * 3600.0,
    Category.WELLBEING.value: 12 * 3600.0,
    Category.TASK.value: 4 * 3600.0,
}

# The closed set, derived from the one authority on it rather than
# retyped. `proactive/decision.py` says of `Category`: "A closed set on
# purpose. Each one has its own cooldown and its own justification rule,
# and a category that is not listed here cannot be sent at all."
#
# That last clause was true of the decision engine and false here. This
# module took a plain string and looked it up with `.get()`, so a
# category the system does not know arrived with no per-category
# cooldown - not an error, not a warning, just an unthrottled send. A
# probe put five distinct messages through an unlisted category in five
# seconds while a listed one allowed one.
#
# So the sentence is now enforced where the sending is decided. Derived
# from the enum and not a second literal list, because a hand-maintained
# copy of a closed set is the thing that drifted in the first place.
KNOWN_CATEGORIES = frozenset(category.value for category in Category)

# The ceiling that survives every other rule failing.
DEFAULT_MAX_PER_DAY = 4

# Nights and early mornings, unless configured otherwise. Aura does not
# wake people up.
DEFAULT_QUIET_HOURS = [[22, 8]]

# How long a message counts as already said.
DEFAULT_DUPLICATE_WINDOW = 6 * 3600.0

# Jaccard overlap above which two messages are "the same thing again".
DEFAULT_SIMILARITY_THRESHOLD = 0.6


@dataclass
class ProactiveSettings:
    """
    Tuning, read from `proactive` in config.yaml.

    Disabled by default. A system that can message you unprompted must
    be switched on deliberately, never acquired by upgrading.
    """

    enabled: bool = False
    cooldown_seconds: float = DEFAULT_COOLDOWN
    category_cooldown_seconds: dict = field(
        default_factory=lambda: dict(DEFAULT_CATEGORY_COOLDOWN)
    )
    max_per_day: int = DEFAULT_MAX_PER_DAY
    quiet_hours: list = field(default_factory=lambda: [list(w) for w in DEFAULT_QUIET_HOURS])
    duplicate_window_seconds: float = DEFAULT_DUPLICATE_WINDOW
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD

    @classmethod
    def from_config(cls, config: dict | None) -> "ProactiveSettings":
        """Built from the `proactive` section, tolerating a partial one."""

        settings = (config or {}).get("proactive") or {}

        category = dict(DEFAULT_CATEGORY_COOLDOWN)

        for key, value in (settings.get("category_cooldown_seconds") or {}).items():
            try:
                category[str(key)] = float(value)
            except (TypeError, ValueError):
                continue

        quiet = settings.get("quiet_hours")

        return cls(
            enabled=bool(settings.get("enabled", False)),
            cooldown_seconds=float(
                settings.get("cooldown_seconds", DEFAULT_COOLDOWN)
            ),
            category_cooldown_seconds=category,
            max_per_day=int(settings.get("max_per_day", DEFAULT_MAX_PER_DAY)),
            quiet_hours=(
                [list(w) for w in quiet]
                if quiet is not None
                else [list(w) for w in DEFAULT_QUIET_HOURS]
            ),
            duplicate_window_seconds=float(
                settings.get(
                    "duplicate_window_seconds", DEFAULT_DUPLICATE_WINDOW
                )
            ),
            similarity_threshold=float(
                settings.get("similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD)
            ),
        )


def similarity(left: str, right: str) -> float:
    """
    How alike two messages are, 0.0 to 1.0.

    Jaccard overlap on meaningful tokens, reusing the retriever's
    tokenizer so "the" and "a" do not make everything look similar. Not
    semantic - two differently worded greetings with no shared words will
    not be caught - but it reliably catches the actual failure mode,
    which is a template firing repeatedly with a substitution or two.
    """

    left_tokens = tokenize(left)
    right_tokens = tokenize(right)

    if not left_tokens or not right_tokens:
        return 1.0 if left.strip().lower() == right.strip().lower() else 0.0

    union = left_tokens | right_tokens

    return len(left_tokens & right_tokens) / len(union)


class ProactivePolicy:
    """
    The gate. Thread-safe: ticks and chats arrive on request threads.

    `clock` is the naive local wall clock, injected. Unlike the
    companion policy - which uses monotonic time for intervals - this one
    needs real dates, because "no more than four a day" and "quiet after
    22:00" are questions about the calendar and cannot be answered from
    a monotonic counter.
    """

    def __init__(
        self,
        settings: ProactiveSettings | None = None,
        clock=local_now,
        ledger=None,
    ):

        self.settings = settings or ProactiveSettings()
        self.clock = clock

        self._lock = Lock()
        self._sent: deque = deque(maxlen=64)     # (when, category, message)

        # Optional on purpose. Without a ledger this class behaves exactly
        # as it did - in-memory, no file, no disk touched - because a
        # great many tests build a bare policy and none of them asked for
        # a file to appear next to them. The composition root supplies one.
        self.ledger = ledger

        if ledger is not None:
            # Loaded once, here, rather than on each question. `allows`
            # asks the history four separate things under a single lock,
            # and re-reading the file per question would let the answers
            # disagree with each other inside one decision. The cost is
            # an assumption worth stating: one process owns this file. Two
            # servers over one ledger would each enforce the owner's limit
            # against a stale copy of the other's sends.
            self._sent.extend(ledger.load())

    # ------------------------------------------------------------------

    def note_sent(self, category: str, message: str) -> None:
        """A proactive message went out. Starts every cooldown."""

        with self._lock:
            self._sent.append((self.clock(), str(category), message))

            self._save()

    def reset(self) -> None:
        with self._lock:
            self._sent.clear()

            # The file too. `reset` is the owner dropping the limit they
            # are currently under; if only the copy in memory were
            # cleared, the next start would read the old sends back and
            # restore a limit the owner had just been told was gone.
            self._save()

    def history(self) -> tuple:
        """
        The raw send history, oldest first: `(when, category, message)`.

        Exposed because `ProactiveEngine` needs to know which parts of
        which day it has already greeted, and that is derivable from
        this - `part_of_day` is a pure function of any datetime. Keeping a
        second copy over in the engine is the duplicated state section 8
        forbids, and the copy is the half that used to be lost on restart.
        """

        with self._lock:
            return tuple(self._sent)

    def _save(self) -> None:
        """
        Write the history out. Caller holds the lock.

        Inside the lock deliberately, despite being disk I/O: two sends
        racing here would otherwise each snapshot the deque and write in
        whichever order the filesystem happened to see them, and the file
        could end up without the later message in it. The deque is at
        most a few dozen short rows, so the write is brief.
        """

        if self.ledger is not None:
            self.ledger.save(self._sent)

    def sent_today(self, now: datetime | None = None) -> int:

        moment = now or self.clock()

        with self._lock:
            return sum(
                1 for when, _c, _m in self._sent if when.date() == moment.date()
            )

    def recent_messages(self, limit: int = 10) -> tuple[str, ...]:
        """What was said lately, newest first. Feeds the context object."""

        with self._lock:
            return tuple(message for _w, _c, message in reversed(self._sent))[
                :limit
            ]

    def last_sent_at(self, category: str = "") -> datetime | None:
        """When Aura last spoke unprompted, optionally within a category."""

        with self._lock:
            for when, sent_category, _message in reversed(self._sent):

                if not category or sent_category == category:
                    return when

        return None

    # ------------------------------------------------------------------
    # The gate
    # ------------------------------------------------------------------

    def allows(self, category: str, message: str) -> tuple[bool, str]:
        """
        (allowed, reason). The reason is populated either way.

        Order matters: the most absolute rules run first, so the reason
        a user reads is the most fundamental one that applied.
        """

        if not self.settings.enabled:
            return False, "proactive messaging is off"

        if not str(message or "").strip():
            return False, "nothing to say"

        # The closed set, enforced where sending is decided rather than
        # only where the reason is chosen. An unknown category is not a
        # category with a lenient cooldown - it is a caller bug, and
        # inventing a plausible throttle for it would hide the bug and
        # ship the spam (sections 20, 21).
        #
        # Placed after `enabled` and the empty-message check, which are
        # cheaper and more fundamental, and before quiet hours, so the
        # reason an operator reads names the actual problem instead of
        # the time of day.
        if str(category) not in KNOWN_CATEGORIES:
            return False, (
                f"unknown category '{category}' - "
                "not one of " + ", ".join(sorted(KNOWN_CATEGORIES))
            )

        now = self.clock()

        if in_quiet_hours(now.hour, self.settings.quiet_hours):
            return False, f"quiet hours ({now.hour:02d}:00)"

        with self._lock:
            history = list(self._sent)

        today = sum(1 for when, _c, _m in history if when.date() == now.date())

        if today >= self.settings.max_per_day:
            return False, f"daily limit reached ({self.settings.max_per_day})"

        for when, sent_category, sent_message in reversed(history):

            elapsed = (now - when).total_seconds()

            if elapsed < self.settings.cooldown_seconds:
                return False, (
                    f"cooldown active "
                    f"({int(self.settings.cooldown_seconds - elapsed)}s left)"
                )

            break

        category_cooldown = self.settings.category_cooldown_seconds.get(
            str(category)
        )

        if category_cooldown:

            for when, sent_category, _message in reversed(history):

                if sent_category != str(category):
                    continue

                elapsed = (now - when).total_seconds()

                if elapsed < category_cooldown:
                    return False, (
                        f"{category} cooldown active "
                        f"({int(category_cooldown - elapsed)}s left)"
                    )

                break

        normalised = " ".join(str(message).lower().split())

        for when, _c, sent_message in reversed(history):

            if (now - when).total_seconds() >= self.settings.duplicate_window_seconds:
                continue

            if " ".join(sent_message.lower().split()) == normalised:
                return False, "already said this recently"

            if similarity(message, sent_message) >= self.settings.similarity_threshold:
                return False, "too similar to a recent message"

        return True, "cooldowns satisfied, outside quiet hours, under daily limit"
