"""Shared time boundaries for UTC-naive community timestamps.

Feed and comment ``DateTime`` values are stored as UTC with no ``tzinfo``.
Product ranges, however, are inclusive Hong Kong calendar dates.  Keep that
conversion here so backend reads and worker candidate selection cannot drift.

``price_bars.timestamp`` is deliberately excluded: price bars use exchange-
local HKT-naive timestamps and must not pass through these helpers.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


HKT = ZoneInfo("Asia/Hong_Kong")


def _date_label(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError("date boundary must be a date, datetime, or ISO date string")


def hkt_range_utc_naive(
    date_from: date | datetime | str,
    date_to: date | datetime | str,
) -> tuple[datetime, datetime]:
    """Convert an inclusive HKT date range to UTC-naive ``[lo, hi)`` bounds."""
    first = _date_label(date_from)
    last = _date_label(date_to)
    if last < first:
        raise ValueError("date_to must not be earlier than date_from")
    lo = datetime.combine(first, time.min, HKT).astimezone(timezone.utc)
    hi = datetime.combine(last + timedelta(days=1), time.min, HKT).astimezone(timezone.utc)
    return lo.replace(tzinfo=None), hi.replace(tzinfo=None)


def utc_naive_to_hkt(value: datetime | None) -> datetime | None:
    """Return a database UTC-naive timestamp as an HKT-naive wall time."""
    if value is None:
        return None
    utc_value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    return utc_value.astimezone(HKT).replace(tzinfo=None)
