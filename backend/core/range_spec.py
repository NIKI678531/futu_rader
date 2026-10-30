"""Shared representation and validation for preset and custom date ranges."""

from __future__ import annotations

from datetime import date


PRESET_KEYS = ("d0", "d1", "d2", "d7", "d14", "d30", "mtd")
CUSTOM_PREFIX = "custom:"
MARKET_SCOPE = "market"
ACCOUNT_SCOPE = "accounts"
RANGE_SCOPES = (MARKET_SCOPE, ACCOUNT_SCOPE)
MARKET_MAX_DAYS = 31


class CustomRangeError(ValueError):
    """The custom range query is syntactically valid HTTP but not selectable."""


def custom_key(date_from: date | str, date_to: date | str) -> str:
    """Return the internal key after syntax/order validation only.

    The selectable span is endpoint-scoped: market pages allow 31 inclusive
    days while account pages allow one calendar year.  Keeping that policy out
    of the internal key lets both domains share the same calendar/cache token.
    """
    first = _date(date_from, "from")
    last = _date(date_to, "to")
    if last < first:
        raise CustomRangeError("自选日期的结束日不得早于开始日")
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
    *,
    scope: str | None = None,
) -> None:
    """Validate an interval against its page policy and available facts."""
    custom_key(first, last)  # validates syntax and order
    if scope is not None:
        validate_span(first, last, scope)
    if earliest_available is not None and first < earliest_available:
        raise CustomRangeError(
            f"自选日期开始日不得早于最早可用数据日 {earliest_available.isoformat()}"
        )
    if latest_complete is not None and last > latest_complete:
        raise CustomRangeError(
            f"自选日期结束日不得晚于最新完整数据日 {latest_complete.isoformat()}"
        )


def validate_span(first: date, last: date, scope: str) -> None:
    """Apply the inclusive market/account span limit.

    One account year means ``[start, same calendar date next year)``.  This
    permits 366 days when the selected calendar year crosses 29 February,
    without allowing 1 January through the following 1 January.
    """
    if scope not in RANGE_SCOPES:
        raise CustomRangeError(f"未知的日期范围作用域 {scope!r}")
    if last < first:
        raise CustomRangeError("自选日期的结束日不得早于开始日")
    days = (last - first).days + 1
    if scope == MARKET_SCOPE:
        if days > MARKET_MAX_DAYS:
            raise CustomRangeError(
                f"市场页面自选日期最多选择 {MARKET_MAX_DAYS} 个自然日"
            )
        return
    anniversary = _next_anniversary(first)
    if last >= anniversary:
        raise CustomRangeError("账号页面自选日期最长为 1 个自然年")


def _next_anniversary(value: date) -> date:
    try:
        return value.replace(year=value.year + 1)
    except ValueError:
        # 29 February's next anniversary is the following 1 March, so the
        # inclusive interval may still contain the whole leap-year span.
        return value.replace(year=value.year + 1, month=3, day=1)


def _date(value: date | str, name: str) -> date:
    if isinstance(value, date):
        return value
    if not value:
        raise CustomRangeError(f"自选日期缺少 {name} 参数")
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise CustomRangeError(f"自选日期 {name} 必须是 YYYY-MM-DD") from exc
