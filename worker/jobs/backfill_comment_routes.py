"""Backfill and atomically activate strict-cashtag comment AI routes.

This command never calls a model.  It is resumable by comment id and keeps the
new route generation unavailable to scheduled AI until ``--activate`` succeeds.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, func, insert, select, update

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT, ROOT / "worker"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from radar_db import make_engine  # noqa: E402
from radar_db.comment_routes import (  # noqa: E402
    COMMENT_ROUTE_POOL_DIGEST_META_KEY,
    COMMENT_ROUTE_READY_META_KEY,
    COMMENT_ROUTE_VERSION,
    COMMENT_ROUTE_VERSION_META_KEY,
    activate,
    active_product_codes,
    product_pool_digest,
    replace_comment_routes,
    route_rows,
)
from radar_db.schema import comment_product_routes, comments, feeds, meta_kv  # noqa: E402


CURSOR_KEY = "comment_route_backfill_cursor"
STATUS_KEY = "comment_route_backfill_status"
BACKFILL_POOL_DIGEST_KEY = "comment_route_backfill_pool_digest"
READINESS_KEYS = (
    COMMENT_ROUTE_READY_META_KEY,
    COMMENT_ROUTE_VERSION_META_KEY,
    COMMENT_ROUTE_POOL_DIGEST_META_KEY,
)


def _put_meta(conn, key, value):
    value = str(value)
    if not conn.execute(update(meta_kv).where(meta_kv.c.k == key).values(v=value)).rowcount:
        conn.execute(insert(meta_kv).values(k=key, v=value))


def _audit(engine):
    with engine.connect() as conn:
        rows = conn.execute(
            select(
                comment_product_routes.c.subject_code,
                func.count().label("routes"),
                func.sum(comment_product_routes.c.matched_parent).label("parent"),
                func.sum(comment_product_routes.c.matched_comment).label("comment"),
                func.sum(
                    (comment_product_routes.c.subject_code != feeds.c.code)
                ).label("cross_product"),
            )
            .select_from(
                comment_product_routes.join(
                    feeds,
                    feeds.c.feed_id == comment_product_routes.c.feed_id,
                )
            )
            .where(comment_product_routes.c.rule_version == COMMENT_ROUTE_VERSION)
            .group_by(comment_product_routes.c.subject_code)
            .order_by(comment_product_routes.c.subject_code)
        )
        return {
            row.subject_code: {
                "routes": int(row.routes or 0),
                "matchedParent": int(row.parent or 0),
                "matchedComment": int(row.comment or 0),
                "crossProduct": int(row.cross_product or 0),
            }
            for row in rows
        }


def run(engine, *, batch_size=500, resume=False, activate_routes=False, dry_run=False):
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if dry_run and activate_routes:
        raise ValueError("--dry-run cannot be combined with --activate")

    pool = active_product_codes()
    digest = product_pool_digest(pool)
    with engine.connect() as conn:
        saved_cursor = conn.execute(
            select(meta_kv.c.v).where(meta_kv.c.k == CURSOR_KEY)
        ).scalar()
        saved_digest = conn.execute(
            select(meta_kv.c.v).where(meta_kv.c.k == BACKFILL_POOL_DIGEST_KEY)
        ).scalar()
    if resume and saved_digest != digest:
        raise RuntimeError(
            "cannot resume without a matching route backfill checkpoint; "
            "restart without --resume"
        )
    cursor = int(saved_cursor or 0) if resume else 0

    if not dry_run:
        with engine.begin() as conn:
            conn.execute(delete(meta_kv).where(meta_kv.c.k.in_(READINESS_KEYS)))
            if not resume:
                conn.execute(delete(comment_product_routes))
                _put_meta(conn, CURSOR_KEY, 0)
            _put_meta(conn, BACKFILL_POOL_DIGEST_KEY, digest)
            _put_meta(conn, STATUS_KEY, "running")

    processed = route_count = 0
    audit = defaultdict(lambda: {
        "routes": 0,
        "matchedParent": 0,
        "matchedComment": 0,
        "crossProduct": 0,
    })
    now = datetime.utcnow()
    while True:
        with engine.connect() as conn:
            batch = list(
                conn.execute(
                    select(
                        comments.c.comment_id,
                        comments.c.feed_id,
                        comments.c.content.label("comment_content"),
                        feeds.c.code.label("anchor_code"),
                        feeds.c.title,
                        feeds.c.content.label("post_content"),
                    )
                    .select_from(comments.join(feeds, feeds.c.feed_id == comments.c.feed_id))
                    .where(comments.c.comment_id > cursor)
                    .order_by(comments.c.comment_id)
                    .limit(batch_size)
                )
            )
        if not batch:
            break
        if dry_run:
            for row in batch:
                desired = route_rows(
                    row.comment_id,
                    row.feed_id,
                    row.title,
                    row.post_content,
                    row.comment_content,
                    now=now,
                    pool_codes=pool,
                )
                for route in desired:
                    item = audit[route["subject_code"]]
                    item["routes"] += 1
                    item["matchedParent"] += int(route["matched_parent"])
                    item["matchedComment"] += int(route["matched_comment"])
                    item["crossProduct"] += int(route["subject_code"] != row.anchor_code)
                route_count += len(desired)
        else:
            with engine.begin() as conn:
                for row in batch:
                    desired = route_rows(
                        row.comment_id,
                        row.feed_id,
                        row.title,
                        row.post_content,
                        row.comment_content,
                        now=now,
                        pool_codes=pool,
                    )
                    replace_comment_routes(conn, row.comment_id, desired)
                    route_count += len(desired)
                cursor = int(batch[-1].comment_id)
                _put_meta(conn, CURSOR_KEY, cursor)
        processed += len(batch)
        cursor = int(batch[-1].comment_id)

    superseded = 0
    if not dry_run:
        audit = _audit(engine)
        with engine.begin() as conn:
            _put_meta(conn, STATUS_KEY, "populated")
            if activate_routes:
                result = activate(conn, codes=pool)
                superseded = result["supersededJobs"]
                _put_meta(conn, STATUS_KEY, "active")
                conn.execute(delete(meta_kv).where(meta_kv.c.k.in_((
                    CURSOR_KEY,
                    BACKFILL_POOL_DIGEST_KEY,
                ))))

    return {
        "status": "dry_run" if dry_run else "active" if activate_routes else "populated",
        "ruleVersion": COMMENT_ROUTE_VERSION,
        "productPoolDigest": digest,
        "processed": processed,
        "routes": route_count,
        "lastCommentId": cursor,
        "supersededJobs": superseded,
        "products": dict(sorted(audit.items())),
        "nextAction": (
            "run normal extraction to enqueue newly eligible units"
            if activate_routes
            else "review the audit, then rerun with --resume --activate"
        ),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="回填并激活评论内容 tag AI 路由")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = run(
        make_engine(),
        batch_size=args.batch_size,
        resume=args.resume,
        activate_routes=args.activate,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
