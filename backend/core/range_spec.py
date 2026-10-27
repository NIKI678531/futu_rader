"""Shared representation and validation for preset and custom date ranges."""

from __future__ import annotations

from datetime import date


PRESET_KEYS = ("d0", "d1", "d2", "d7", "d14", "d30", "mtd")
CUSTOM_PREFIX = "custom:"
CUSTOM_MAX_DAYS = 30


class CustomRangeError(ValueError):
    """The custom range query is syntactically valid HTTP but not selectable."""


def custom_key(date_from: date | str, date_to: date | str) -> str:
    first = _date(date_from, "from")
    last = _date(date_to, "to")
    if last < first:
        raise CustomRangeError("自选日期的结束日不得早于开始日")
    days = (last - first).days + 1
    if days > CUSTOM_MAX_DAYS:
        raise CustomRangeError(f"自选日期最多选择 {CUSTOM_MAX_DAYS} 个自然日")
    return f"{CUSTOM_PREFIX}{first.isoformat()}:{last.isoformat()}"


def parse_custom_key(key: object) -> tuple[date, date] | None:
    value = str(key or "")
    if not value.startswith(CUSTOM_PREFIX):
        return None
    parts = value[len(CUSTOM_PREFIX):].split(":")
    if len(parts) != 2:
        raise CustomRangeError("自选日期参数格式无效")
    normalized = custom_key(parts[0], parts[1])
    first, last = normalized[len(CUSTOM_PREFIX):].split(":")
    return date.fromisoformat(first), date.fromisoformat(last)


def is_custom_key(key: object) -> bool:
    try:
        return parse_custom_key(key) is not None
    except CustomRangeError:
        return False


def is_valid_range_key(key: object) -> bool:
    return key in PRESET_KEYS or is_custom_key(key)


def validate_selectable(
    first: date,
    last: date,
    earliest_available: date | None,
    latest_complete: date | None,
) -> None:
    """Validate a short interval against the dates actually available to query."""
    custom_key(first, last)  # validates order and inclusive length
    if earliest_available is not None and first < earliest_available:
        raise CustomRangeError(
            f"自选日期开始日不得早于最早可用数据日 {earliest_available.isoformat()}"
        )
    if latest_complete is not None and last > latest_complete:
        raise CustomRangeError(
            f"自选日期结束日不得晚于最新完整数据日 {latest_complete.isoformat()}"
        )


def _date(value: date | str, name: str) -> date:
    if isinstance(value, date):
        return value
    if not value:
        raise CustomRangeError(f"自选日期缺少 {name} 参数")
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CustomRangeError(f"自选日期 {name} 必须是 YYYY-MM-DD") from exc
