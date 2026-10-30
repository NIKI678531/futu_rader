"""基准区间与时间桶（PRD §3.1、§5 `buildRange(key)`）。

PRD §5 逐字：区间与桶由**后端下发，前端不自行算桶**。这条不是性能考虑——桶的边界
决定了热力图、趋势图、K 线三者的横轴是否对齐，前端各算各的就一定会错开。

同一条口径也定义了「基准区间」：与当前区间等长、紧邻其前的一段（d1/d2 例外，
文案是「较昨日同期」）。环比 delta 的分母就是它，所以它必须和区间一起下发。

演示 provider 的取值逐字来自设计源；真实 provider 的实现是同一套日历规则的 SQL 版本，
**只会有这一份**（铁律 1）。
"""

import logging

from providers import get_provider
from providers.sentinel import MISSING
from .range_spec import (
    CustomRangeError,
    PRESET_KEYS,
    custom_key,
    parse_custom_key,
    validate_selectable,
)


log = logging.getLogger(__name__)

# PRD §3.1 的预设，加上「当天」d0。未知 key 不做兜底回落——静默回落到 d7 会让前端以为自己拿到了
# 请求的区间，图表横轴和标题却是另一段时间。
VALID_KEYS = PRESET_KEYS


def build_custom_key(date_from, date_to, *, scope="market"):
    """Validate an HTTP custom-range tuple and return its internal cache key."""
    key = custom_key(date_from, date_to)
    provider = get_provider()
    complete = provider.build_range("d30")
    earliest = None
    latest = None
    if complete is not None and complete is not MISSING:
        from datetime import date
        latest = date.fromisoformat(complete["to"])
    bounds = getattr(provider, "custom_date_bounds", None)
    if callable(bounds):
        earliest, provider_latest = bounds()
        latest = provider_latest or latest
    first, last = parse_custom_key(key)
    validate_selectable(first, last, earliest, latest, scope=scope)
    return key


def build_range(key):
    """返回该预设区间的完整描述（含 buckets / benchFrom / benchTo / 各处文案）。

    未知 key 返回 MISSING —— 那是「没这个东西」，由端点转成 404；
    与「有这个东西但取不到值」（None → unavailable）是两回事。
    """
    try:
        custom = parse_custom_key(key)
    except CustomRangeError:
        return MISSING
    if key not in VALID_KEYS and custom is None:
        return MISSING

    try:
        provider = get_provider()
    except Exception:
        log.exception(
            "range_provider_resolution_failed key=%s "
            "possible_causes=invalid_DATA_PROVIDER_or_RADAR_DB_URL;missing_database_driver;"
            "database_engine_initialization_failed",
            key,
        )
        raise

    provider_class = type(provider).__name__
    provider_name = getattr(provider, "name", provider_class)
    try:
        data = provider.build_range(key)
    except Exception:
        log.exception(
            "range_build_failed key=%s provider=%s provider_name=%s "
            "possible_causes=provider_query_failed;calendar_build_failed;unexpected_provider_data",
            key,
            provider_class,
            provider_name,
        )
        raise

    if data is None:
        diagnostics = {
            "reason": "provider_returned_none",
            "database": "unknown",
            "anchor": "unknown",
            "meta_key_count": "unknown",
            "possible_causes": (
                "required_metadata_missing;data_source_not_initialized;"
                "data_source_configuration_mismatch;provider_read_failed"
            ),
        }
        diagnose = getattr(provider, "range_unavailable_diagnostics", None)
        if callable(diagnose):
            try:
                diagnostics.update(diagnose(key) or {})
            except Exception:
                log.exception(
                    "range_diagnostics_failed key=%s provider=%s provider_name=%s",
                    key,
                    provider_class,
                    provider_name,
                )

        log.warning(
            "range_unavailable key=%s provider=%s provider_name=%s reason=%s "
            "database=%s anchor=%s meta_key_count=%s possible_causes=%s",
            key,
            provider_class,
            provider_name,
            diagnostics["reason"],
            diagnostics["database"],
            diagnostics["anchor"],
            diagnostics["meta_key_count"],
            diagnostics["possible_causes"],
        )
    else:
        log.info(
            "range_available key=%s provider=%s provider_name=%s from=%s to=%s "
            "granularity=%s bucket_count=%s",
            key,
            provider_class,
            provider_name,
            data.get("from") if isinstance(data, dict) else "unknown",
            data.get("to") if isinstance(data, dict) else "unknown",
            data.get("gran") if isinstance(data, dict) else "unknown",
            len(data.get("buckets", [])) if isinstance(data, dict) else "unknown",
        )
    return data
