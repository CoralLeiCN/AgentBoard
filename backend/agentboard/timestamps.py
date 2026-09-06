"""Canonical RFC 3339 UTC timestamps. Integers are only used for duration arithmetic.

Nine fractional digits preserve OTLP nanoseconds and make stored strings sortable.
Leap seconds, unknown offsets (-00:00), and precision beyond nanoseconds are rejected.
"""

import re
from datetime import datetime, timedelta, timezone

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
RFC3339 = re.compile(r"(\d{4}-\d{2}-\d{2})[Tt](\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?([Zz]|[+-]\d{2}:\d{2})")


def timestamp_ns(value: str) -> int:
    """Parse a supported RFC 3339 instant without floating-point rounding."""
    match = RFC3339.fullmatch(value) if isinstance(value, str) else None
    if not match or match[4] == "-00:00":
        raise ValueError("Expected RFC 3339 with a known timezone and at most 9 fractional digits")
    zone = "+00:00" if match[4].upper() == "Z" else match[4]
    if zone[4:] > "59" or zone[1:3] > "23":
        raise ValueError("Invalid RFC 3339 timezone offset")
    dt = datetime.fromisoformat(f"{match[1]}T{match[2]}{zone}")
    delta = dt - EPOCH
    result = (delta.days * 86400 + delta.seconds) * 1_000_000_000
    result += int((match[3] or "").ljust(9, "0"))
    if result < 0:
        raise ValueError("Timestamp must be on or after the Unix epoch")
    return result


def format_timestamp(value: int) -> str:
    """Convert an external epoch-nanosecond value to the canonical representation."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("Expected nonnegative integer epoch nanoseconds")
    seconds, nanos = divmod(value, 1_000_000_000)
    try:
        dt = EPOCH + timedelta(seconds=seconds)
    except OverflowError as exc:
        raise ValueError("Timestamp is outside the supported calendar range") from exc
    return f"{dt:%Y-%m-%dT%H:%M:%S}.{nanos:09d}Z"


def normalize_timestamp(value: str) -> str:
    return format_timestamp(timestamp_ns(value))
