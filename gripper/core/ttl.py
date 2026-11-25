import re
from datetime import UTC, datetime, timedelta

from gripper.core.exceptions import InvalidTTL


TTL_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}


def parse_ttl(value: str) -> timedelta:
    """Parse a positive integer duration in minutes, hours, or days."""

    match = re.fullmatch(r"([1-9][0-9]*)([mhd])", value)
    if match is None:
        raise InvalidTTL("Use a positive TTL such as 30m, 2h, 1d, or 7d.")

    amount, unit = match.groups()
    try:
        return timedelta(seconds=int(amount) * TTL_UNIT_SECONDS[unit])
    except (ValueError, OverflowError) as exception:
        raise InvalidTTL("TTL is too large.") from exception


def format_ttl(duration: timedelta) -> str:
    """Display a whole-minute duration using the same units as TTL input."""

    days, minutes = divmod(int(duration.total_seconds()) // 60, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    return " ".join(f"{value}{unit}" for value, unit in [(days, "d"), (hours, "h"), (minutes, "m")] if value)


def calculate_expires_at(ttl: str, starts_at: datetime) -> datetime:
    """Add a TTL to an explicit starting time and return the deadline in UTC."""

    duration = parse_ttl(ttl)
    starts_at = as_utc(starts_at)

    try:
        return starts_at + duration
    except OverflowError as exception:
        raise InvalidTTL("TTL exceeds the supported date range.") from exception


def as_utc(value: datetime) -> datetime:
    """Convert an aware datetime to UTC without assuming a local timezone."""

    if value.utcoffset() is None:
        raise ValueError("A timezone is required.")

    return value.astimezone(UTC)
