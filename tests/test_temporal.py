"""
Temporal context tests.

Every test pins its own clock. Nothing here reads the machine's real
time, so the suite gives the same answer at 23:59 on New Year's Eve as
it does at noon in June - which is the property the module itself is
supposed to provide to Aura.

The interesting cases are all boundaries: midnight, the difference
between "twenty minutes ago" and "yesterday", a timestamp that cannot be
parsed, and a timezone name that does not exist.
"""

from datetime import datetime, timedelta, timezone

import pytest

from core.temporal import (
    AFTERNOON,
    EVENING,
    MORNING,
    NIGHT,
    TemporalClock,
    TemporalContext,
    describe_when,
    in_quiet_hours,
    local_now,
    parse_timestamp,
    part_of_day,
    resolve_timezone,
)


def at(text: str) -> datetime:
    """A fixed naive local moment, written the way a test reads."""

    return datetime.fromisoformat(text)


# ----------------------------------------------------------------------
# part of day
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "hour, expected",
    [
        (0, NIGHT),
        (4, NIGHT),
        (5, MORNING),
        (11, MORNING),
        (12, AFTERNOON),
        (16, AFTERNOON),
        (17, EVENING),
        (21, EVENING),
        (22, NIGHT),
        (23, NIGHT),
    ],
)
def test_part_of_day_covers_every_hour(hour, expected):

    assert part_of_day(datetime(2026, 8, 11, hour, 30)) == expected


def test_part_of_day_accepts_an_aware_datetime():
    """An aware value must not raise; it is read as its own wall clock."""

    aware = datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)

    assert part_of_day(aware) in (MORNING, AFTERNOON, EVENING, NIGHT)


# ----------------------------------------------------------------------
# parsing stored timestamps
# ----------------------------------------------------------------------

def test_parse_timestamp_reads_the_stored_format():
    """memory.models.timestamp_now writes isoformat(timespec="seconds")."""

    stored = datetime(2026, 8, 11, 14, 32, 5).isoformat(timespec="seconds")

    assert parse_timestamp(stored) == datetime(2026, 8, 11, 14, 32, 5)


@pytest.mark.parametrize(
    "value",
    [
        "2026-08-11 14:32:05",
        "2026-08-11T14:32:05",
        "2026-08-11",
    ],
)
def test_parse_timestamp_accepts_older_shapes(value):

    assert parse_timestamp(value) is not None


@pytest.mark.parametrize("value", ["", None, "   ", "not a date", "11/08/2026"])
def test_parse_timestamp_returns_none_rather_than_raising(value):
    """One unreadable row must not take down recall."""

    assert parse_timestamp(value) is None


def test_parse_timestamp_passes_a_datetime_through():

    moment = datetime(2026, 8, 11, 9, 0)

    assert parse_timestamp(moment) == moment


def test_parse_timestamp_makes_an_aware_value_comparable():
    """The result must compare with naive values instead of raising."""

    parsed = parse_timestamp(datetime(2026, 8, 11, 9, 0, tzinfo=timezone.utc))

    assert parsed is not None
    assert parsed.tzinfo is None
    assert parsed < datetime(2030, 1, 1)


# ----------------------------------------------------------------------
# relative description - the reason this module exists
# ----------------------------------------------------------------------

def test_just_now():

    now = at("2026-08-11T14:32:00")

    assert describe_when(now - timedelta(seconds=20), now) == "just now"


@pytest.mark.parametrize(
    "then, now, expected",
    [
        ("2026-08-11T08:00:00", "2026-08-11T14:00:00", "this morning"),
        ("2026-08-11T13:00:00", "2026-08-11T16:00:00", "this afternoon"),
        ("2026-08-11T18:00:00", "2026-08-11T21:00:00", "this evening"),
        ("2026-08-11T01:00:00", "2026-08-11T04:00:00", "earlier today"),
    ],
)
def test_same_day_uses_the_part_of_day(then, now, expected):

    assert describe_when(at(then), at(now)) == expected


def test_twenty_minutes_across_midnight_is_yesterday_not_minutes_ago():
    """
    The boundary case the whole calendar-arithmetic design exists for.

    23:50 read at 00:10 is twenty minutes old. Anything dividing a
    timedelta by 86400 says "today". A person says "last night".
    """

    then = at("2026-08-11T23:50:00")
    now = at("2026-08-12T00:10:00")

    assert (now - then) < timedelta(hours=1)
    assert describe_when(then, now) == "last night"


def test_yesterday_morning_is_yesterday():

    assert describe_when(at("2026-08-10T09:00:00"), at("2026-08-11T14:00:00")) == (
        "yesterday"
    )


def test_yesterday_evening_is_last_night():

    assert describe_when(at("2026-08-10T20:00:00"), at("2026-08-11T14:00:00")) == (
        "last night"
    )


@pytest.mark.parametrize(
    "days_ago, expected",
    [
        (2, "2 days ago"),
        (3, "3 days ago"),
        (6, "6 days ago"),
        (8, "last week"),
        (13, "last week"),
        (15, "2 weeks ago"),
        (28, "4 weeks ago"),
        (40, "last month"),
        (95, "3 months ago"),
        (400, "1 year ago"),
        (800, "2 years ago"),
    ],
)
def test_past_distances(days_ago, expected):

    now = at("2026-08-11T14:00:00")

    assert describe_when(now - timedelta(days=days_ago), now) == expected


@pytest.mark.parametrize(
    "days_ahead, expected",
    [
        (1, "tomorrow"),
        (3, "in 3 days"),
        (9, "next week"),
        (20, "in 2 weeks"),
        (45, "next month"),
        (120, "in 4 months"),
        (500, "in 1 year"),
    ],
)
def test_future_distances(days_ahead, expected):
    """A memory may hold a future event; it must not be described as past."""

    now = at("2026-08-11T14:00:00")

    assert describe_when(now + timedelta(days=days_ahead), now) == expected


def test_later_the_same_day_is_later_today():

    now = at("2026-08-11T09:00:00")

    assert describe_when(now + timedelta(hours=6), now) == "later today"


def test_a_single_day_is_singular():

    now = at("2026-08-11T14:00:00")

    assert describe_when(now - timedelta(days=1, hours=2), now) in (
        "yesterday",
        "last night",
    )
    assert describe_when(now - timedelta(days=8), now) == "last week"
    assert describe_when(now - timedelta(days=400), now) == "1 year ago"


def test_unreadable_timestamp_describes_as_nothing():
    """Undated, not broken."""

    assert describe_when("banana", at("2026-08-11T14:00:00")) == ""
    assert describe_when(None, at("2026-08-11T14:00:00")) == ""


def test_describe_when_accepts_a_stored_string():

    stored = datetime(2026, 8, 10, 20, 0).isoformat(timespec="seconds")

    assert describe_when(stored, at("2026-08-11T14:00:00")) == "last night"


def test_describe_when_without_a_reference_uses_the_present():
    """The default path still has to work, so it is exercised once."""

    assert describe_when(local_now()) == "just now"


# ----------------------------------------------------------------------
# quiet hours - shared by every part of Aura that can speak unprompted
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "hour, windows, expected",
    [
        (2, [[23, 7]], True),
        (23, [[23, 7]], True),
        (7, [[23, 7]], False),
        (12, [[23, 7]], False),
        (13, [[12, 14]], True),
        (14, [[12, 14]], False),
        (3, [[9, 17], [23, 7]], True),
        (10, [], False),
    ],
)
def test_quiet_hour_windows(hour, windows, expected):

    assert in_quiet_hours(hour, windows) is expected


@pytest.mark.parametrize(
    "windows",
    [None, [[]], [["a", "b"]], [[1]], [{}], [[5, 5]]],
)
def test_malformed_quiet_hours_are_skipped_not_raised(windows):
    """A bad config entry must not make the gate fail open *or* explode."""

    assert in_quiet_hours(3, windows) is False


# ----------------------------------------------------------------------
# TemporalContext
# ----------------------------------------------------------------------

def test_context_renders_two_compact_lines():

    context = TemporalContext(
        now=at("2026-08-11T14:32:00"),
        timezone_name="Asia/Ho_Chi_Minh",
        utc_offset="+07:00",
    )

    lines = context.render()

    assert len(lines) == 2
    assert lines[0] == "Today is Tuesday 11 August 2026."
    assert "14:32" in lines[1]
    assert "Asia/Ho_Chi_Minh, UTC+07:00" in lines[1]
    assert "afternoon" in lines[1]


def test_context_render_survives_an_unknown_zone():

    lines = TemporalContext(now=at("2026-08-11T14:32:00")).render()

    assert len(lines) == 2
    assert "local time" in lines[1]


def test_context_day_boundaries():

    context = TemporalContext(now=at("2026-01-01T00:30:00"))

    assert context.today.isoformat() == "2026-01-01"
    assert context.yesterday.isoformat() == "2025-12-31"
    assert context.tomorrow.isoformat() == "2026-01-02"


def test_context_as_dict_is_structured_not_prose():

    data = TemporalContext(
        now=at("2026-08-11T14:32:00"),
        timezone_name="Asia/Ho_Chi_Minh",
        utc_offset="+07:00",
    ).as_dict()

    assert data["date"] == "2026-08-11"
    assert data["time"] == "14:32"
    assert data["weekday"] == "Tuesday"
    assert data["hour"] == 14
    assert data["part_of_day"] == AFTERNOON


def test_context_describes_against_its_own_frozen_now():

    context = TemporalContext(now=at("2026-08-12T00:10:00"))

    assert context.describe(at("2026-08-11T23:50:00")) == "last night"


# ----------------------------------------------------------------------
# TemporalClock
# ----------------------------------------------------------------------

def test_clock_uses_the_injected_now():

    clock = TemporalClock(now=lambda: at("2026-08-11T14:32:00"))

    assert clock.now() == at("2026-08-11T14:32:00")
    assert clock.hour() == 14
    assert clock.context().part_of_day == AFTERNOON


def test_clock_context_is_frozen_within_a_turn():
    """
    Two reads of the same context give the same answer even when the
    underlying clock has moved. A turn must not see time tick between
    its greeting and its memory section.
    """

    ticks = iter(
        [at("2026-08-11T14:32:00"), at("2026-08-11T15:59:00")]
    )

    clock = TemporalClock(now=lambda: next(ticks))

    context = clock.context()

    assert context.time_text == "14:32"
    assert context.time_text == "14:32"

    # The clock itself did move on; it is the snapshot that is frozen.
    assert clock.now().strftime("%H:%M") == "15:59"


def test_clock_from_config_reads_the_temporal_section():

    clock = TemporalClock.from_config(
        {"temporal": {"timezone": "UTC"}},
        now=lambda: at("2026-08-11T14:32:00"),
    )

    assert clock.timezone_name == "UTC"
    assert clock.context().timezone_name == "UTC"


def test_clock_from_config_tolerates_a_missing_section():

    clock = TemporalClock.from_config({}, now=lambda: at("2026-08-11T14:00:00"))

    assert clock.now() == at("2026-08-11T14:00:00")


def test_unknown_timezone_falls_back_to_system_local():
    """A typo in config must not stop Aura from knowing roughly when it is."""

    clock = TemporalClock(
        timezone_name="Mars/Olympus_Mons",
        now=lambda: at("2026-08-11T14:00:00"),
    )

    assert clock.timezone is None
    assert clock.timezone_name == ""
    assert clock.now() == at("2026-08-11T14:00:00")


def test_resolve_timezone_returns_none_for_empty():

    assert resolve_timezone(None) is None
    assert resolve_timezone("") is None
    assert resolve_timezone("   ") is None


def test_utc_resolves_without_a_timezone_database():
    """
    Windows ships no tzdata, and UTC is the value a deployment is most
    likely to configure. It must not silently degrade to system local.
    """

    assert resolve_timezone("UTC") is not None
    assert resolve_timezone("utc") is not None


def test_configured_timezone_offset_is_reported():

    clock = TemporalClock(
        timezone_name="UTC", now=lambda: at("2026-08-11T14:00:00")
    )

    assert clock.context().utc_offset == "+00:00"


def test_no_hardcoded_date_anywhere_in_the_module():
    """
    The clock with no injection must report the actual present, not a
    baked-in date. Guards against a literal sneaking into the module.
    """

    before = datetime.now()
    reading = TemporalClock().now()
    after = datetime.now()

    assert before - timedelta(seconds=5) <= reading <= after + timedelta(seconds=5)


# ----------------------------------------------------------------------
# Moving the zone on the clock the whole process already holds
# ----------------------------------------------------------------------
#
# `launcher/services.py` builds one clock and hands the same object to
# the prompt, the memory pipeline, the retriever, quiet hours and the
# proactive engine, so that "the time in the prompt and the time on a
# stored memory cannot disagree". That is only true of a zone change if
# the change happens *in place*. Rebuilding the clock would leave five
# subsystems holding the old one and the sixth holding the new.

def test_use_timezone_moves_the_reported_zone():

    clock = TemporalClock()

    assert clock.use_timezone("UTC") is True
    assert clock.timezone_name == "UTC"
    assert clock.context().timezone_name == "UTC"
    assert clock.context().utc_offset == "+00:00"


def test_use_timezone_reaches_a_captured_bound_method():
    """
    `RankedRetriever` is constructed with `clock=self.clock.now` - the
    bound method, captured once, at build time. Everything else holds the
    clock object. A zone change has to arrive at both or memory ranking
    dates a recalled line against a different day than the prompt does.

    Reads the real clock deliberately, like the hardcoded-date guard at
    the end of this file: the claim under test is that a reading taken
    after the change is in the new zone, and a pinned `now` would answer
    the question the test after this one asks instead.
    """

    clock = TemporalClock()
    captured = clock.now

    clock.use_timezone("UTC")

    utc_wall = datetime.now(timezone.utc).replace(tzinfo=None)

    assert abs(captured() - utc_wall) < timedelta(seconds=5)


def test_use_timezone_leaves_a_pinned_clock_pinned():
    """
    An injected `now` is the harness's own clock and outranks the zone.
    Otherwise a test that pinned the time would start reading the
    machine's the moment anything moved the zone underneath it.
    """

    clock = TemporalClock(now=lambda: at("2026-08-11T14:00:00"))

    assert clock.use_timezone("UTC") is True
    assert clock.now() == at("2026-08-11T14:00:00")
    # The label still moves: it is what the prompt says, and the caller
    # asked for it.
    assert clock.context().timezone_name == "UTC"


def test_use_timezone_refuses_an_unresolvable_zone_without_degrading():
    """
    A refused change must cost nothing. The constructor degrades a bad
    name to system local because it has nothing better to fall back to;
    here there is something better - the zone already in effect - and
    dropping it would punish a typo by silently moving Aura's clock.
    """

    clock = TemporalClock(timezone_name="UTC")

    assert clock.use_timezone("Mars/Olympus_Mons") is False
    assert clock.timezone_name == "UTC"
    assert clock.context().timezone_name == "UTC"
    assert clock.context().utc_offset == "+00:00"


def test_use_timezone_with_nothing_returns_to_the_machine_zone():
    """Clearing it has to be expressible, or a zone set once is forever."""

    clock = TemporalClock(timezone_name="UTC")

    assert clock.use_timezone("") is True
    assert clock.timezone is None
    assert clock.timezone_name == ""
