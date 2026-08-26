"""
Time, as a first-class input.

A language model has no clock. Everything it believes about "now" comes
from what we put in the prompt, and everything it believes about "when
did that happen" comes from how we describe a stored timestamp. Both are
computed here so there is exactly one answer in the process.

Two rules hold this module together.

**Everything is naive local time.** `memory.models.timestamp_now` has
always written `datetime.now().isoformat()`, which is naive wall clock,
and the database is full of those strings. Introducing aware datetimes
next to them would mean every comparison is one missing `tzinfo` away
from `TypeError`. So a configured timezone changes *what local means*
- the wall clock is read in that zone - and the value is still stored
and compared naive. `_strip` defends the boundary for callers who hand
us an aware datetime anyway.

**Relative phrasing is calendar arithmetic, not subtraction.** At 00:10,
something stored at 23:50 is twenty minutes old and happened
*yesterday*. Anything that divides a `timedelta` by 86400 gets that
wrong, and gets it wrong exactly at the boundary where a person is most
likely to notice. Every phrase below is chosen from a difference of
`date` objects first, and only then refined by clock time.

No date, hour or year is hardcoded anywhere in Aura. If a test needs it
to be Tuesday, it injects a clock.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone as dt_timezone

from core.logger import logger


# Part-of-day boundaries, in local hours. Night wraps midnight, so it is
# defined as "none of the others" rather than as a range.
MORNING_START = 5
AFTERNOON_START = 12
EVENING_START = 17
NIGHT_START = 22

MORNING = "morning"
AFTERNOON = "afternoon"
EVENING = "evening"
NIGHT = "night"

# Anything closer than this is "just now" rather than a duration.
JUST_NOW_SECONDS = 60

DATE_FORMAT = "%A %d %B %Y"
TIME_FORMAT = "%H:%M"


# The three spellings of UTC that resolve without a timezone database.
# Named rather than written inline because `core/settings_store.py` has to
# agree about them when it validates an owner-supplied zone, and a second
# copy of the list there would refuse "Z" the day this one grew a fourth
# entry.
UTC_ALIASES = ("UTC", "GMT", "Z")


def canonical_timezone_name(name: str | None) -> str:
    """
    A zone name as it should be stored and shown.

    Whitespace and the UTC aliases only. IANA keys are case-sensitive to
    `ZoneInfo`, so the lowercasing that every other name-ish setting in
    this codebase applies would break every real zone - "asia/ho_chi_minh"
    resolves nowhere. "utc" is folded to "UTC" because the stored name is
    what the prompt's TIME section prints, and "(utc, UTC+00:00)" reads as
    some zone other than the one the owner picked.
    """

    text = str(name or "").strip()

    if text.upper() in UTC_ALIASES:
        return "UTC"

    return text


def resolve_timezone(name: str | None):
    """
    A `tzinfo` for a configured IANA name, or None for system local.

    Leaving this unset is the normal case and needs nothing installed:
    the machine's own clock is already in the user's timezone. The
    override exists for deployments where it is not - a Render container
    runs in UTC while the person using it does not.

    An IANA name needs a timezone database. Linux images have one;
    Windows does not ship one, so a name like "Asia/Ho_Chi_Minh" resolves
    there only if `tzdata` is installed (it is a hard requirement in
    requirements.txt for exactly this reason). "UTC" is special-cased
    because `datetime.timezone.utc` is always available, and UTC is the
    one value a deployment is actually likely to write down.

    Fixed-offset spellings - the "GMT+7" a phone keyboard produces, or
    "UTC+07:00" - are accepted alongside IANA names and resolve to a
    `timezone` with that offset. They are not zone names: they carry no
    DST history, which for Vietnam (no DST) is exactly right, and they
    were previously rejected outright even though an owner who typed one
    said something precise and machine-readable.

    An unresolvable name is a configuration mistake, not a crash: it is
    logged and the system clock is used, because a companion that
    refuses to start over a typo in a timezone is worse than one that is
    an hour out.
    """

    if not name:
        return None

    text = str(name).strip()

    if not text:
        return None

    if text.upper() in UTC_ALIASES:
        return dt_timezone.utc

    fixed = _fixed_offset(text)

    if fixed is not None:
        return fixed

    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(text)
    except Exception as error:                       # unknown zone, no tzdata
        logger.warning(
            "Unknown timezone %r (%s); using the system local clock instead. "
            "IANA names need a system timezone database, or `pip install "
            "tzdata` on Windows.",
            name,
            error,
        )
        return None


# "GMT+7", "UTC+07:00", "GMT-5", "+09:30". The bare signed form is a
# spelling phones produce; the prefixed forms are what people type when
# they mean an offset rather than a zone. Hours are 1-2 digits so that
# "GMT+7" and "GMT+07" both parse; minutes are optional and two digits
# when present.
_OFFSET_PATTERN = None


def _fixed_offset(text: str):
    """
    A fixed-offset `timezone` for spellings like "GMT+7", or None.

    Deliberately narrower than `str` would allow: only explicit offsets
    are matched, so a real zone name ("GMT+7" collides with nothing in
    the IANA database) can never be shadowed by this rule. Returns None
    for anything that is not an offset, and the caller falls through to
    `ZoneInfo`.
    """

    global _OFFSET_PATTERN

    if _OFFSET_PATTERN is None:
        import re

        _OFFSET_PATTERN = re.compile(
            r"^(?:(?:GMT|UTC))?\s*([+-])\s*(\d{1,2})(?::(\d{2}))?$",
            re.IGNORECASE,
        )

    match = _OFFSET_PATTERN.match(text.strip())

    if match is None:
        return None

    sign, hours, minutes = match.groups()

    delta = timedelta(hours=int(hours), minutes=int(minutes or 0))

    if hours == "0" and not minutes:
        # "+0"/"-0" both mean UTC; keep the sign from mattering.
        pass
    elif int(hours) > 23 or (minutes and int(minutes) > 59):
        return None

    if sign == "-":
        delta = -delta

    return dt_timezone(delta)


def _strip(moment: datetime) -> datetime:
    """
    Force a datetime into this module's naive-local convention.

    An aware datetime is converted to its own local wall clock and then
    has the tzinfo removed, so it compares with everything else here
    instead of raising.
    """

    if moment.tzinfo is not None:
        return moment.astimezone().replace(tzinfo=None)

    return moment


def local_now(timezone=None) -> datetime:
    """The current wall clock, naive, in the application's timezone."""

    if timezone is None:
        return datetime.now()

    return datetime.now(timezone).replace(tzinfo=None)


def part_of_day(moment: datetime) -> str:
    """Which part of the day a moment falls in."""

    hour = _strip(moment).hour

    if MORNING_START <= hour < AFTERNOON_START:
        return MORNING

    if AFTERNOON_START <= hour < EVENING_START:
        return AFTERNOON

    if EVENING_START <= hour < NIGHT_START:
        return EVENING

    return NIGHT


def parse_timestamp(value) -> datetime | None:
    """
    Read a stored timestamp back into a datetime, or None.

    Tolerant on purpose: it is fed database columns written by several
    versions of Aura, and one unreadable row must not take down recall.
    """

    if isinstance(value, datetime):
        return _strip(value)

    if not value:
        return None

    text = str(value).strip()

    if not text:
        return None

    try:
        return _strip(datetime.fromisoformat(text))
    except ValueError:
        pass

    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue

    return None


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _describe_past(then: datetime, now: datetime, days: int) -> str:
    """`days` is a calendar-day difference, already computed and positive."""

    if days == 0:
        part = part_of_day(then)

        # "this night" is not English, and at 01:00 the small hours of
        # the same calendar day are still most naturally "earlier today".
        if part == NIGHT:
            return "earlier today"

        return f"this {part}"

    if days == 1:
        # 23:50 yesterday, read at 00:10, is "last night" - not "1 day ago".
        if part_of_day(then) in (EVENING, NIGHT):
            return "last night"

        return "yesterday"

    if days < 7:
        return f"{_plural(days, 'day')} ago"

    if days < 14:
        return "last week"

    if days < 31:
        return f"{_plural(days // 7, 'week')} ago"

    if days < 60:
        return "last month"

    if days < 365:
        return f"{_plural(days // 30, 'month')} ago"

    return f"{_plural(days // 365, 'year')} ago"


def _describe_future(then: datetime, now: datetime, days: int) -> str:
    """`days` is a calendar-day difference, already computed and positive."""

    if days == 0:
        return "later today"

    if days == 1:
        return "tomorrow"

    if days < 7:
        return f"in {_plural(days, 'day')}"

    if days < 14:
        return "next week"

    if days < 31:
        return f"in {_plural(days // 7, 'week')}"

    if days < 60:
        return "next month"

    if days < 365:
        return f"in {_plural(days // 30, 'month')}"

    return f"in {_plural(days // 365, 'year')}"


def describe_when(then, now: datetime | None = None) -> str:
    """
    How a person would say when `then` was, relative to `now`.

    Accepts a datetime or a stored timestamp string. Returns "" when the
    value cannot be read at all, so a caller can render nothing rather
    than "unknown" - a memory with no legible date should look undated,
    not look broken.
    """

    moment = parse_timestamp(then)

    if moment is None:
        return ""

    reference = _strip(now) if now is not None else local_now()

    seconds = (reference - moment).total_seconds()

    if abs(seconds) < JUST_NOW_SECONDS:
        return "just now"

    # Calendar days, so midnight is a boundary and 86400 seconds is not.
    days = (reference.date() - moment.date()).days

    if seconds > 0:
        return _describe_past(moment, reference, max(days, 0))

    return _describe_future(moment, reference, max(-days, 0))


def in_quiet_hours(hour: int, windows) -> bool:
    """
    Whether `hour` falls inside any [start, end) window of local hours.

    A window may wrap midnight: [23, 7] means 23:00 to 07:00. Shared by
    every part of Aura that can speak unprompted, so "quiet" means the
    same thing to all of them. Malformed windows are skipped rather than
    raising - a bad config entry must not make the gate fail open.
    """

    for window in windows or []:

        try:
            start, end = int(window[0]), int(window[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue

        if start == end:
            continue

        # A window that wraps midnight is two ranges, not one.
        if start < end:
            if start <= hour < end:
                return True
        elif hour >= start or hour < end:
            return True

    return False


@dataclass(frozen=True)
class TemporalContext:
    """
    What time it is, in the form the prompt needs.

    Frozen and precomputed: the same turn must not see the clock tick
    between the greeting and the memory section.
    """

    now: datetime
    timezone_name: str = ""
    utc_offset: str = ""

    @property
    def part_of_day(self) -> str:
        return part_of_day(self.now)

    @property
    def weekday(self) -> str:
        return self.now.strftime("%A")

    @property
    def date_text(self) -> str:
        return self.now.strftime(DATE_FORMAT)

    @property
    def time_text(self) -> str:
        return self.now.strftime(TIME_FORMAT)

    @property
    def today(self) -> date:
        return self.now.date()

    @property
    def yesterday(self) -> date:
        return self.today - timedelta(days=1)

    @property
    def tomorrow(self) -> date:
        return self.today + timedelta(days=1)

    def describe(self, then) -> str:
        """Relative phrasing for a stored moment, against this context."""

        return describe_when(then, self.now)

    def as_dict(self) -> dict:
        """Structured form, for logs and for the proactive decision engine."""

        return {
            "iso": self.now.isoformat(timespec="seconds"),
            "date": self.now.strftime("%Y-%m-%d"),
            "time": self.time_text,
            "weekday": self.weekday,
            "hour": self.now.hour,
            "part_of_day": self.part_of_day,
            "timezone": self.timezone_name,
            "utc_offset": self.utc_offset,
        }

    def render(self) -> list[str]:
        """
        Prompt lines. Two of them, deliberately.

        Everything else here is derivable by the model from the date and
        the clock; spending prompt on "it is not the weekend" would be
        the kind of bloat this phase is supposed to avoid.
        """

        zone = self.timezone_name or "local time"

        if self.utc_offset:
            zone = f"{zone}, UTC{self.utc_offset}"

        return [
            f"Today is {self.date_text}.",
            f"The local time is {self.time_text} ({zone}) - "
            f"{self.part_of_day}.",
        ]


class TemporalClock:
    """
    The application's one source of "now".

    `now` is injectable so that every test which cares about time can
    pin it, and so that no test depends on the machine's real clock,
    the date it runs on, or which side of midnight CI starts.
    """

    def __init__(self, timezone_name: str | None = None, now=None):

        self.timezone_name = str(timezone_name or "")
        self.timezone = resolve_timezone(timezone_name)

        if timezone_name and self.timezone is None:
            # resolve_timezone already logged why.
            self.timezone_name = ""

        self._now = now or (lambda: local_now(self.timezone))

    @classmethod
    def from_config(cls, config: dict | None, now=None) -> "TemporalClock":
        """Built from the optional `temporal` section of config.yaml."""

        settings = (config or {}).get("temporal") or {}

        return cls(timezone_name=settings.get("timezone"), now=now)

    def now(self) -> datetime:
        """The current naive local wall clock."""

        return _strip(self._now())

    def context(self) -> TemporalContext:
        """A frozen snapshot of the present moment."""

        moment = self.now()

        return TemporalContext(
            now=moment,
            timezone_name=self.timezone_name or _system_zone_name(moment),
            utc_offset=self._offset(moment),
        )

    def describe(self, then) -> str:
        return describe_when(then, self.now())

    def use_timezone(self, name: str | None) -> bool:
        """
        Move this clock to another zone, in place. Returns whether it moved.

        In place rather than by rebuilding, and that is the whole design.
        `launcher/services.py` builds one clock and hands the same object
        to the prompt, the memory pipeline, the ranked retriever, the
        quiet-hours check and the proactive engine, so that "the time in
        the prompt and the time on a stored memory cannot disagree". A
        replacement clock would move whichever subsystem got it and leave
        the rest on the old zone - the disagreement a single shared clock
        exists to prevent. The default `_now` closure reads `self.timezone`
        when it is *called*, so even `RankedRetriever`, which captured the
        bound `now` method at construction, follows.

        False means the name does not resolve on this machine and nothing
        changed. That is the opposite of what the constructor does with a
        bad name, deliberately: the constructor has nothing better to fall
        back on, whereas a running clock has something better - the zone
        already in effect - and silently dropping it would punish a typo
        by moving Aura's clock. The caller reports the refusal instead
        (`SettingsService._reapply_temporal`).

        An injected `now` is left alone. It belongs to whoever injected it
        - a test pinning the present, or a harness - and outranks the zone.
        The label still moves, because the label is what the prompt says
        and it is what was asked for.
        """

        wanted = canonical_timezone_name(name)

        if not wanted:
            self.timezone = None
            self.timezone_name = ""
            return True

        resolved = resolve_timezone(wanted)

        if resolved is None:
            # resolve_timezone already logged why.
            return False

        self.timezone = resolved
        self.timezone_name = wanted

        return True

    def hour(self) -> int:
        """Local hour, for quiet-hour checks."""

        return self.now().hour

    def _offset(self, moment: datetime) -> str:
        """"+07:00" for the configured zone at `moment`, or "" if unknown."""

        if self.timezone is None:
            return _system_offset(moment)

        try:
            delta = moment.replace(tzinfo=self.timezone).utcoffset()
        except Exception:
            return ""

        return _format_offset(delta)


def _format_offset(delta: timedelta | None) -> str:

    if delta is None:
        return ""

    total = int(delta.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)

    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def _system_offset(moment: datetime) -> str:
    """The machine's own UTC offset at `moment`."""

    try:
        return _format_offset(moment.astimezone().utcoffset())
    except (OverflowError, OSError, ValueError):
        # Dates near the representable limits cannot be localised.
        return ""


def _system_zone_name(moment: datetime) -> str:
    """The machine's timezone abbreviation, or "" if it has none."""

    try:
        return moment.astimezone().tzname() or ""
    except (OverflowError, OSError, ValueError):
        return ""
