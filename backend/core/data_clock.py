"""数据日历的单一读取入口。

``anchor`` 是最近一个已完整采集的香港自然日，``d0`` 则是已经到达数据库的
最新香港日期。两者不能共用一个日期，也不能用服务器时钟补齐。

历史 dump 和新版在线增量链路都显式保存 ``data_max_ts``，正常路径 O(1) 读该水位。
只有旧库缺键时才用带索引的 ``max(feeds.posted_at)`` 兼容。feeds 时间戳是 UTC-naive，
转为 HKT 后才能取日期。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Mapping

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from radar_db.schema import feeds, meta_kv
from radar_db.time_windows import HKT, utc_naive_to_hkt

from .calendar import parse_anchor


def _metadata_time(value) -> datetime | None:
    """Parse a stored data timestamp as an HKT wall-clock value."""
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        day = parse_anchor(text)
        return datetime.combine(day, datetime.min.time()) if day else None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(HKT).replace(tzinfo=None)
    return parsed


def latest_hkt_data_time(engine, meta: Mapping[str, object] | None = None) -> datetime | None:
    """Return the latest HKT wall time backed by an observed feed.

    ``data_max_ts`` is the normal O(1) watermark.  The indexed table maximum
    is used only for older databases that predate it.
    """
    source = dict(meta or {})
    if not source:
        try:
            with engine.connect() as conn:
                source = dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v)).all())
        except SQLAlchemyError:
            source = {}

    recorded = _metadata_time(source.get("data_max_ts"))
    if recorded is not None:
        # Normal online ingestion advances this watermark in the same page
        # transaction as its facts.  This O(1) read is the hot path for every
        # d0 API request; the indexed MAX query below is migration compatibility
        # for databases created before that watermark existed.
        return recorded

    candidates = []
    try:
        with engine.connect() as conn:
            latest_feed = conn.execute(select(func.max(feeds.c.posted_at))).scalar_one()
    except SQLAlchemyError:
        latest_feed = None
    if latest_feed is not None:
        if not isinstance(latest_feed, datetime):
            try:
                latest_feed = datetime.fromisoformat(str(latest_feed))
            except ValueError:
                latest_feed = None
        if latest_feed is not None:
            candidates.append(utc_naive_to_hkt(latest_feed))
    known = [candidate for candidate in candidates if candidate is not None]
    return max(known) if known else None


def latest_hkt_data_date(engine, meta: Mapping[str, object] | None = None) -> date | None:
    """Date-only projection used by range selection and analysis orchestration."""
    latest = latest_hkt_data_time(engine, meta)
    return latest.date() if latest is not None else None


def analysis_range_anchor(
    engine,
    range_key: str,
    complete_anchor: date | str | None = None,
    *,
    meta: Mapping[str, object] | None = None,
) -> date | None:
    """Resolve the right edge used by one analysis range.

    Existing ranges end at the latest complete day.  ``d0`` ends at the
    latest actually observed data day and is unavailable until that day is
    later than the complete anchor.  Returning ``None`` here prevents an
    unsynchronised current day from being published as a confirmed empty day.
    """
    source = dict(meta or {})
    anchor = complete_anchor
    if anchor is None:
        if not source:
            try:
                with engine.connect() as conn:
                    source = dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v)).all())
            except SQLAlchemyError:
                source = {}
        anchor = source.get("anchor")
    anchor = anchor if isinstance(anchor, date) else parse_anchor(anchor)
    if range_key != "d0":
        return anchor
    if anchor is None:
        return None
    latest = latest_hkt_data_date(engine, source)
    if latest is None:
        return None
    return latest if latest > anchor else None
