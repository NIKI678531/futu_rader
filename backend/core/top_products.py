"""Strict-cashtag comment-volume selection for AI runs.

The admin page and the worker must agree on which products a range analyzes.
This module keeps that database decision in ``backend/core``: only parent feeds
carrying the corresponding ETF's strict FUTU cashtag qualify, then products are
ordered by the resulting platform comment count (ties by code).

``analysis_products`` is the single selection used by every AI entry point
(ADR-0031): every active product by default, or the own-product comment-volume
Top N plus every peer when an own limit is configured.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from core.calendar import build, parse_anchor
from radar_db.comment_filter import (
    is_filter_ready,
    load_comment_filter_config,
    qualifying_feed_scope_predicate,
    require_filter_ready,
)
from radar_db.product_catalog import load_products
from radar_db.schema import feeds, meta_kv
from radar_db.time_windows import hkt_range_utc_naive


TOP_COMMENT_PRODUCT_LIMIT = 10


def _anchor_date(engine, anchor):
    if anchor is None:
        with engine.connect() as conn:
            anchor = conn.execute(
                select(meta_kv.c.v).where(meta_kv.c.k == "anchor")
            ).scalar_one_or_none()
    return anchor if isinstance(anchor, date) else parse_anchor(anchor)


def _comment_counts(engine, range_key, anchor_date, codes, config):
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
        return {str(code): int(value or 0) for code, value in conn.execute(statement)}


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
    anchor_date = _anchor_date(engine, anchor)
    if anchor_date is None:
        return []
    counts = _comment_counts(engine, range_key, anchor_date, codes, config)

    ordered = sorted(codes, key=lambda code: (-counts.get(code, 0), code))[:limit]
    return [
        {"rank": rank, "code": code, "comments": counts.get(code, 0)}
        for rank, code in enumerate(ordered, start=1)
    ]


def parse_own_limit(value) -> int | None:
    """``AI_OWN_TOP_LIMIT``: empty or ``0`` means every own product."""

    text = str(value if value is not None else "").strip()
    if not text:
        return None
    limit = int(text)
    if limit < 0:
        raise ValueError("AI_OWN_TOP_LIMIT must be zero (all) or a positive integer")
    return limit or None


def analysis_products(
    engine,
    range_key: str,
    *,
    anchor: date | str | None = None,
    own_limit: int | None = None,
) -> list[dict[str, int | str | None]]:
    """Return the products one range analyzes, own products first.

    Without ``own_limit`` every active product is returned; products without
    comments cost no model request because Layer B publishes an empty result.
    Comment counts only order the work, so an unready comment filter leaves
    them ``None`` instead of blocking an unlimited run.
    """

    products = load_products(engine=engine)
    codes = [str(product["code"]) for product in products]
    if not codes:
        return []
    anchor_date = _anchor_date(engine, anchor)
    config = load_comment_filter_config()
    counts: dict[str, int] | None = None
    if anchor_date is not None:
        with engine.connect() as conn:
            meta = dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v)).all())
        if is_filter_ready(meta, config):
            counts = _comment_counts(engine, range_key, anchor_date, codes, config)
    if own_limit is not None and counts is None:
        require_filter_ready(engine, config)
        return []

    def ordered(ownership):
        group = [str(p["code"]) for p in products if p.get("ownership") == ownership]
        return sorted(group, key=lambda code: (-(counts or {}).get(code, 0), code))

    own = ordered("own")
    if own_limit is not None:
        own = own[:own_limit]
    selected = own + ordered("peer")
    ownership = {str(p["code"]): p.get("ownership") for p in products}
    return [
        {
            "rank": rank,
            "code": code,
            "ownership": ownership[code],
            "comments": None if counts is None else counts.get(code, 0),
        }
        for rank, code in enumerate(selected, start=1)
    ]
