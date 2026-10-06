"""Strict-cashtag comment-volume selection for bounded AI runs.

The business pages and the worker must agree on which products form the
comment-volume Top 10.  This module keeps that database decision in
``backend/core``: only parent feeds carrying the corresponding ETF's strict
FUTU cashtag qualify, then products are ordered by the resulting platform
comment count (ties by code).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from core.calendar import build, parse_anchor
from radar_db.comment_filter import (
    load_comment_filter_config,
    qualifying_feed_scope_predicate,
    require_filter_ready,
)
from radar_db.product_catalog import load_products
from radar_db.schema import feeds, meta_kv
from radar_db.time_windows import hkt_range_utc_naive


TOP_COMMENT_PRODUCT_LIMIT = 10


def top_comment_products(
    engine,
    range_key: str,
    *,
    anchor: date | str | None = None,
    limit: int = TOP_COMMENT_PRODUCT_LIMIT,
) -> list[dict[str, int | str]]:
    """Return the active products with the highest strict-tag comment volume."""

    limit = int(limit)
    if limit < 1:
        raise ValueError("limit must be positive")
    products = load_products(engine=engine)
    codes = [str(product["code"]) for product in products]
    if not codes:
        return []

    config = load_comment_filter_config()
    require_filter_ready(engine, config)
    if anchor is None:
        with engine.connect() as conn:
            anchor = conn.execute(
                select(meta_kv.c.v).where(meta_kv.c.k == "anchor")
            ).scalar_one_or_none()
    anchor_date = anchor if isinstance(anchor, date) else parse_anchor(anchor)
    if anchor_date is None:
        return []
    window = build(range_key, anchor_date)
    lo, hi = hkt_range_utc_naive(window["from"], window["to"])
    comment_total = func.coalesce(func.sum(feeds.c.comment_count), 0)
    statement = (
        select(feeds.c.code, comment_total.label("comments"))
        .where(
            feeds.c.code.in_(codes),
            feeds.c.posted_at >= lo,
            feeds.c.posted_at < hi,
            qualifying_feed_scope_predicate(feeds, config, codes=codes),
        )
        .group_by(feeds.c.code)
    )
    with engine.connect() as conn:
        counts = {str(code): int(value or 0) for code, value in conn.execute(statement)}

    ordered = sorted(codes, key=lambda code: (-counts.get(code, 0), code))[:limit]
    return [
        {"rank": rank, "code": code, "comments": counts.get(code, 0)}
        for rank, code in enumerate(ordered, start=1)
    ]
