"""Invalidate comment-analysis work when its persisted source facts change.

The collection and JSONL ingestion paths both update facts outside the
annotation worker.  They must retire old jobs in the same transaction as the
source edit; otherwise a completed job can continue publishing annotations
until the next extraction pass creates a replacement input hash.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import or_, select, update

from .schema import annotation_jobs, comments


COMMENT_ANALYSIS_TASKS = ("comment_product", "kol_comment_opinion")


def supersede_comment_analysis_jobs(
    conn,
    *,
    feed_ids: Iterable[int] = (),
    comment_ids: Iterable[int] = (),
    reason: str = "Source input changed",
    updated_at: datetime | None = None,
) -> int:
    """Retire existing comment jobs affected by a source-fact mutation.

    ``feed_ids`` invalidates every comment currently attached to those parent
    feeds. ``comment_ids`` also covers comments deleted earlier in the calling
    transaction.  An empty scope is a no-op.
    """

    feed_ids = tuple(dict.fromkeys(int(value) for value in feed_ids))
    comment_ids = tuple(dict.fromkeys(int(value) for value in comment_ids))
    targets = []
    if feed_ids:
        targets.append(
            annotation_jobs.c.target_id.in_(
                select(comments.c.comment_id).where(comments.c.feed_id.in_(feed_ids))
            )
        )
    if comment_ids:
        targets.append(annotation_jobs.c.target_id.in_(comment_ids))
    if not targets:
        return 0

    result = conn.execute(
        update(annotation_jobs)
        .where(
            annotation_jobs.c.target_type == "comment",
            annotation_jobs.c.task.in_(COMMENT_ANALYSIS_TASKS),
            annotation_jobs.c.status != "superseded",
            or_(*targets),
        )
        .values(
            status="superseded",
            lease_until=None,
            last_error=reason,
            updated_at=updated_at
            or datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    return max(0, int(result.rowcount or 0))
