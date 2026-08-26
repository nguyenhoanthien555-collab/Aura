"""
Timezone resolution, on the machine Aura actually runs on.

`core.temporal.resolve_timezone` is the one door every configured zone
goes through - settings validation at PATCH time and clock construction
at startup both - so its contract is pinned here rather than trusted:

    an IANA name resolves when a timezone database exists
    ("Asia/Ho_Chi_Minh" on Windows needs the `tzdata` package)
    a fixed-offset spelling ("GMT+7") resolves everywhere
    UTC and its aliases always resolve
    nothing resolves to a guess - None means "use the system clock"
"""

from datetime import datetime, timedelta, timezone as dt_timezone

import pytest

from core.temporal import canonical_timezone_name, resolve_timezone


HO_CHI_MINH_OFFSET = timedelta(hours=7)


def _offset_of(name: str, moment: datetime) -> timedelta:
    zone = resolve_timezone(name)

    assert zone is not None

    return moment.replace(tzinfo=zone).utcoffset()


class TestIANANames:
    def test_asia_ho_chi_minh_resolves(self):
        # The regression this file exists for: on Windows this raised
        # ZoneInfoNotFoundError until tzdata became a requirement, and
        # the refusal surfaced as a settings error naming GMT+7.
        moment = datetime(2026, 1, 15, 12, 0)

        assert _offset_of("Asia/Ho_Chi_Minh", moment) == HO_CHI_MINH_OFFSET

    def test_an_unresolvable_name_is_none_not_a_crash(self):
        assert resolve_timezone("Mars/Olympus_Mons") is None

    @pytest.mark.parametrize("empty", ["", "   ", None])
    def test_no_name_means_system_clock(self, empty):
        assert resolve_timezone(empty) is None


class TestFixedOffsets:
    @pytest.mark.parametrize(
        "spelling",
        ["GMT+7", "gmt+7", "UTC+7", "utc+07", "GMT+07:00", "UTC+7:00"],
    )
    def test_the_spellings_a_phone_produces(self, spelling):
        # The owner's stored value was literally "GMT+7"; before this it
        # was refused by settings validation with a message about IANA
        # databases that did not answer what they wrote.
        moment = datetime(2026, 1, 15, 12, 0)

        assert _offset_of(spelling, moment) == HO_CHI_MINH_OFFSET

    def test_negative_offsets(self):
        moment = datetime(2026, 1, 15, 12, 0)

        assert _offset_of("GMT-5", moment) == timedelta(hours=-5)

    def test_half_hour_offsets(self):
        moment = datetime(2026, 1, 15, 12, 0)

        assert _offset_of("+09:30", moment) == timedelta(hours=9, minutes=30)

    def test_out_of_range_hours_are_not_an_offset(self):
        assert resolve_timezone("GMT+99") is None

    def test_zero_is_utc(self):
        zone = resolve_timezone("GMT+0")

        assert zone is not None
        assert datetime(2026, 1, 1).replace(tzinfo=zone).utcoffset() == timedelta(0)


class TestUTCAliases:
    @pytest.mark.parametrize("alias", ["UTC", "GMT", "Z"])
    def test_aliases_resolve_to_real_utc(self, alias):
        assert resolve_timezone(alias) is dt_timezone.utc

    def test_lowercase_utc_is_canonicalised_before_resolution(self):
        # canonical_timezone_name folds case-insensitively; resolution of
        # the folded value must agree.
        assert resolve_timezone(canonical_timezone_name("utc")) is dt_timezone.utc
