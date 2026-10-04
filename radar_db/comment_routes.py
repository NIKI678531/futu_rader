"""Deterministic strict-cashtag routing for comment-level AI work.

Platform counters remain section/parent scoped.  This module owns the separate
AI eligibility seam: a comment is eligible for every active product whose
strict FUTU cashtag occurs in its parent title/body or in that comment itself.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import datetime

from sqlalchemy import delete, exists, insert, select, update

from .comment_filter import extract_feed_mentions, source_ticker_to_code
from .product_catalog import load_products
from .revisions import bump_revision, ensure_ai_revision, mark_synthesis
from .schema import annotation_jobs, comment_product_routes, meta_kv


COMMENT_ROUTE_VERSION = "content-cashtag-v1"
COMMENT_ROUTE_READY_META_KEY = "comment_route_ready"
COMMENT_ROUTE_VERSION_META_KEY = "comment_route_version"
COMMENT_ROUTE_POOL_DIGEST_META_KEY = "comment_route_pool_digest"
COMMENT_ROUTE_READY_VALUE = "1"


def active_product_codes() -> frozenset[str]:
    return frozenset(str(row["code"]) for row in load_products())


def product_pool_digest(codes: Iterable[str] | None = None) -> str:
    normalized = sorted(set(codes or active_product_codes()))
    material = json.dumps(normalized, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def codes_in_text(text: str | None, *, pool_codes: Iterable[str] | None = None) -> set[str]:
    pool = set(pool_codes or active_product_codes())
    found = set()
    for mention in extract_feed_mentions(None, text):
        code = source_ticker_to_code(mention.raw_ticker)
        if code in pool:
            found.add(code)
    return found


def route_matches(
    title: str | None,
    post_content: str | None,
    comment_content: str | None,
    *,
    pool_codes: Iterable[str] | None = None,
) -> dict[str, dict[str, bool]]:
    """Return product routes and which content boundary matched each product."""

    pool = set(pool_codes or active_product_codes())
    parent = {
        code
        for mention in extract_feed_mentions(title, post_content)
        if (code := source_ticker_to_code(mention.raw_ticker)) in pool
    }
    comment = codes_in_text(comment_content, pool_codes=pool)
    return {
        code: {
            "matched_parent": code in parent,
            "matched_comment": code in comment,
        }
        for code in sorted(parent | comment)
    }


def route_rows(
    comment_id: int,
    feed_id: int,
    title: str | None,
    post_content: str | None,
    comment_content: str | None,
    *,
    now: datetime,
    pool_codes: Iterable[str] | None = None,
) -> list[dict]:
    return [
        {
            "comment_id": int(comment_id),
            "subject_code": code,
            "feed_id": int(feed_id),
            **matched,
            "rule_version": COMMENT_ROUTE_VERSION,
            "updated_at": now,
        }
        for code, matched in route_matches(
            title,
            post_content,
            comment_content,
            pool_codes=pool_codes,
        ).items()
    ]


def replace_comment_routes(
    conn,
    comment_id: int,
    rows: Iterable[Mapping[str, object]],
) -> dict[str, set[str]]:
    """Replace one comment's routes and return added/removed/retained codes."""

    comment_id = int(comment_id)
    desired_rows = [dict(row) for row in rows]
    comment_ids = {int(row["comment_id"]) for row in desired_rows}
    if comment_ids and comment_ids != {comment_id}:
        raise ValueError("replace_comment_routes accepts exactly one comment")
    desired = {str(row["subject_code"]): row for row in desired_rows}
    existing = {
        str(row["subject_code"]): dict(row)
        for row in conn.execute(
            select(comment_product_routes).where(
                comment_product_routes.c.comment_id == comment_id
            )
        ).mappings()
    }
    for code, values in desired.items():
        prior = existing.get(code)
        if prior is None:
            conn.execute(insert(comment_product_routes).values(**values))
        else:
            conn.execute(
                update(comment_product_routes)
                .where(
                    comment_product_routes.c.comment_id == comment_id,
                    comment_product_routes.c.subject_code == code,
                )
                .values(**{k: v for k, v in values.items() if k != "comment_id"})
            )
    removed = set(existing) - set(desired)
    if removed:
        conn.execute(
            delete(comment_product_routes).where(
                comment_product_routes.c.comment_id == comment_id,
                comment_product_routes.c.subject_code.in_(sorted(removed)),
            )
        )
    return {
        "added": set(desired) - set(existing),
        "removed": removed,
        "retained": set(desired) & set(existing),
    }


def delete_comment_routes(conn, comment_ids: Iterable[int]) -> set[str]:
    ids = tuple(dict.fromkeys(int(value) for value in comment_ids))
    if not ids:
        return set()
    affected = set(
        conn.execute(
            select(comment_product_routes.c.subject_code).where(
                comment_product_routes.c.comment_id.in_(ids)
            )
        ).scalars()
    )
    conn.execute(
        delete(comment_product_routes).where(comment_product_routes.c.comment_id.in_(ids))
    )
    return affected


def route_exists_predicate(target_id, subject_code):
    return exists(
        select(1).where(
            comment_product_routes.c.comment_id == target_id,
            comment_product_routes.c.subject_code == subject_code,
            comment_product_routes.c.rule_version == COMMENT_ROUTE_VERSION,
        )
    )


def readiness_values(codes: Iterable[str] | None = None) -> dict[str, str]:
    return {
        COMMENT_ROUTE_READY_META_KEY: COMMENT_ROUTE_READY_VALUE,
        COMMENT_ROUTE_VERSION_META_KEY: COMMENT_ROUTE_VERSION,
        COMMENT_ROUTE_POOL_DIGEST_META_KEY: product_pool_digest(codes),
    }


def is_ready(values: Mapping[str, str | None], codes: Iterable[str] | None = None) -> bool:
    return all(values.get(key) == value for key, value in readiness_values(codes).items())


def readiness_on_connection(conn, *, lock: bool = False, codes=None) -> bool:
    expected = readiness_values(codes)
    query = select(meta_kv.c.k, meta_kv.c.v).where(meta_kv.c.k.in_(tuple(expected)))
    if lock:
        query = query.with_for_update()
    return is_ready(dict(conn.execute(query).all()), codes)


def require_ready_on_connection(conn, *, lock: bool = False, codes=None) -> None:
    if not readiness_on_connection(conn, lock=lock, codes=codes):
        raise RuntimeError(
            "comment AI routes are not ready; run "
            "`python -m jobs.backfill_comment_routes --resume --activate`"
        )


def require_ready(engine, *, codes=None) -> None:
    with engine.connect() as conn:
        require_ready_on_connection(conn, codes=codes)


def report_policy_fields(engine, *, codes=None) -> dict[str, str]:
    """Routing provenance embedded in calibration and human-gold reports."""

    expected = readiness_values(codes)
    with engine.connect() as conn:
        values = dict(
            conn.execute(
                select(meta_kv.c.k, meta_kv.c.v).where(meta_kv.c.k.in_(tuple(expected)))
            ).all()
        )
    if not is_ready(values, codes):
        raise RuntimeError(
            "comment AI routes are not ready; quality reports cannot be generated"
        )
    return {
        "commentRouteVersion": values[COMMENT_ROUTE_VERSION_META_KEY],
        "productPoolDigest": values[COMMENT_ROUTE_POOL_DIGEST_META_KEY],
    }


def _put_meta(conn, key: str, value: object) -> None:
    value = str(value)
    if not conn.execute(update(meta_kv).where(meta_kv.c.k == key).values(v=value)).rowcount:
        conn.execute(insert(meta_kv).values(k=key, v=value))


def supersede_ineligible_jobs(conn, *, reason: str | None = None) -> int:
    """Retire comment tasks that no longer have an active content-tag route."""

    eligible = route_exists_predicate(
        annotation_jobs.c.target_id,
        annotation_jobs.c.subject_code,
    )
    result = conn.execute(
        update(annotation_jobs)
        .where(
            annotation_jobs.c.target_type == "comment",
            annotation_jobs.c.task.in_(("comment_product", "kol_comment_opinion")),
            annotation_jobs.c.status != "superseded",
            ~eligible,
        )
        .values(
            status="superseded",
            lease_until=None,
            last_error=reason or f"Excluded by {COMMENT_ROUTE_VERSION}",
            updated_at=datetime.utcnow(),
        )
    )
    return max(0, int(result.rowcount or 0))


def activate(conn, *, codes: Iterable[str] | None = None) -> dict[str, object]:
    """Atomically publish one complete route generation and invalidate derivatives."""

    pool = set(codes or active_product_codes())
    invalid = int(
        conn.execute(
            select(comment_product_routes.c.comment_id).where(
                comment_product_routes.c.rule_version != COMMENT_ROUTE_VERSION
            ).limit(1)
        ).first()
        is not None
    )
    if invalid:
        raise RuntimeError("comment route table contains rows from another rule version")
    retired = supersede_ineligible_jobs(conn)
    ensure_ai_revision(conn)
    bump_revision(conn, "ai_input")
    mark_synthesis(conn, pool, True)
    # A human-gold score is valid only for the candidate universe it sampled.
    # Remove the prior database-level claim when publishing a new route
    # generation; the newly generated report can restore it explicitly.
    conn.execute(delete(meta_kv).where(meta_kv.c.k == "ai_validation"))
    for key, value in readiness_values(pool).items():
        _put_meta(conn, key, value)
    return {"active": True, "supersededJobs": retired}

