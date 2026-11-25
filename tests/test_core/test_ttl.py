from datetime import UTC, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from gripper.core.exceptions import InvalidTTL
from gripper.core.ttl import calculate_expires_at, parse_ttl


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("30m", timedelta(minutes=30)),
        ("2h", timedelta(hours=2)),
        ("1d", timedelta(days=1)),
        ("7d", timedelta(days=7)),
    ],
)
def test_parse_ttl(value, expected):
    """Parse the supported TTL formats into durations."""

    assert parse_ttl(value) == expected


@pytest.mark.parametrize("value", ["", "0m", "-1h", "1.5h", "1w", "1h30m", "30", " 2h", "2H"])
def test_parse_ttl_rejects_invalid_input(value):
    """Reject zero, negative, unsupported, and malformed TTL values."""

    with pytest.raises(InvalidTTL, match="positive TTL"):
        parse_ttl(value)


def test_parse_ttl_rejects_overflow():
    """Report a duration overflow as an invalid TTL."""

    with pytest.raises(InvalidTTL, match="too large"):
        parse_ttl("999999999999999d")


def test_calculate_expires_at_returns_utc():
    """Convert a starting time with an offset into an absolute UTC deadline."""

    starts_at = datetime(2026, 1, 1, 23, 45, tzinfo=timezone(timedelta(hours=4)))

    expires_at = calculate_expires_at("7d", starts_at)

    assert expires_at == datetime(2026, 1, 8, 19, 45, tzinfo=UTC)
    assert expires_at.tzinfo is UTC


def test_calculate_expires_at_across_daylight_saving():
    """Count elapsed hours across a daylight-saving clock change."""

    starts_at = datetime(2026, 3, 29, 1, 30, tzinfo=ZoneInfo("Europe/Berlin"))

    assert calculate_expires_at("2h", starts_at) == datetime(2026, 3, 29, 2, 30, tzinfo=UTC)


def test_calculate_expires_at_requires_timezone():
    """Reject an ambiguous starting time instead of using the local timezone."""

    with pytest.raises(ValueError, match="timezone"):
        calculate_expires_at("30m", datetime(2026, 1, 1))


def test_calculate_expires_at_rejects_date_overflow():
    """Reject a valid duration that would exceed the supported date range."""

    with pytest.raises(InvalidTTL, match="date range"):
        calculate_expires_at("1m", datetime.max.replace(tzinfo=UTC))
