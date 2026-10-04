"""Backfill and activate the parent-feed comment filter facts.

Examples::

    python -m jobs.backfill_comment_filter --dry-run
    python -m jobs.backfill_comment_filter --batch-size 500
    python -m jobs.backfill_comment_filter --resume --activate

Readiness stays disabled until a complete, resolvable pass is explicitly
activated. Progress is stored in ``meta_kv`` after each committed batch so an
interrupted production pass can resume without replaying earlier pages.
Activation retires jobs that are no longer eligible; the next normal
``jobs.extract``/``jobs.full_own`` run creates or revives jobs for comments
whose parent feed has become eligible. The backfill command never calls a model.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import case, delete, func, insert, select, update

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "worker"))

from collection.exact import EXACT_RULE, EXACT_RULE_VERSION, normalize_futu_ticker  # noqa: E402
from radar_db import make_engine  # noqa: E402
from radar_db.comment_filter import (  # noqa: E402
    COMMENT_FILTER_DIGEST_META_KEY,
    COMMENT_FILTER_READY_META_KEY,
    COMMENT_FILTER_VERSION_META_KEY,
    extract_feed_mentions,
    filter_readiness_values,
    load_comment_filter_config,
    normalize_source_ticker,
    qualifies_feed,
    qualifying_feed_predicate,
    qualifying_feed_scope_predicate,
    require_filter_ready,
    source_ticker_valid_predicate,
)
from radar_db.revisions import bump_revision, ensure_ai_revision, mark_synthesis  # noqa: E402
from radar_db.schema import (  # noqa: E402
    annotation_jobs,
    comments,
    feed_mentions,
    feeds,
    meta_kv,
    src_feeds,
    src_stocks,
)


CURSOR_KEY = "comment_filter_backfill_cursor"
STATUS_KEY = "comment_filter_backfill_status"
BACKFILL_DIGEST_KEY = "comment_filter_backfill_config_digest"
READINESS_KEYS = (
    COMMENT_FILTER_READY_META_KEY,
    COMMENT_FILTER_VERSION_META_KEY,
    COMMENT_FILTER_DIGEST_META_KEY,
)


def require_exact_rule_version(config):
    """Keep persisted filter policy and worker routing provenance in lockstep."""

    if config.version != EXACT_RULE_VERSION:
        raise RuntimeError(
            f"worker rule version {EXACT_RULE_VERSION!r} does not match "
            f"comment filter config {config.version!r}"
        )
    return config


def _put_meta(conn, key, value):
    value = str(value)
    if not conn.execute(update(meta_kv).where(meta_kv.c.k == key).values(v=value)).rowcount:
        conn.execute(insert(meta_kv).values(k=key, v=value))


def _source_code(ticker):
    normalized = normalize_source_ticker(ticker)
    return normalize_futu_ticker(normalized) if normalized else None


def _unique_tickers_by_code(conn):
    candidates = defaultdict(set)
    for ticker, in conn.execute(select(src_stocks.c.ticker)):
        normalized = normalize_source_ticker(ticker)
        code = _source_code(ticker)
        if code and normalized:
            candidates[code].add(normalized)
    return {
        code: next(iter(tickers))
        for code, tickers in candidates.items()
        if len(tickers) == 1
    }


def _source_tickers_for_feed_ids(conn, feed_ids):
    if not feed_ids:
        return {}
    rows = conn.execute(
        select(src_feeds.c.feed_id, src_stocks.c.ticker)
        .select_from(src_feeds.join(src_stocks, src_stocks.c.stock_id == src_feeds.c.stock_id))
        .where(src_feeds.c.feed_id.in_(feed_ids))
    )
    return {
        int(feed_id): normalize_source_ticker(ticker)
        for feed_id, ticker in rows
        if normalize_source_ticker(ticker) is not None
    }


def _ticker_for_anchor(ticker, code):
    normalized = normalize_source_ticker(ticker)
    return normalized if normalized and _source_code(normalized) == str(code) else None


def _resolved_source_ticker(row, source_by_feed, unique_by_code):
    """Resolve a ticker without allowing a duplicate source row to remount a feed."""

    return (
        _ticker_for_anchor(source_by_feed.get(row.feed_id), row.code)
        or _ticker_for_anchor(row.source_ticker, row.code)
        or _ticker_for_anchor(unique_by_code.get(row.code), row.code)
    )


def _replace_mentions(conn, feed_id, desired):
    desired_by_ticker = {
        normalize_source_ticker(mention.raw_ticker): mention.as_row(feed_id)
        for mention in desired
    }
    existing = {
        normalize_source_ticker(row["raw_ticker"]): row
        for row in conn.execute(select(feed_mentions).where(
            feed_mentions.c.feed_id == feed_id
        )).mappings()
    }
    changed = False
    for ticker, values in desired_by_ticker.items():
        prior = existing.get(ticker)
        if prior is None:
            conn.execute(insert(feed_mentions).values(**values))
            changed = True
        elif any(
            prior.get(field) != values[field]
            for field in ("raw_ticker", "market", "occurrences")
        ):
            conn.execute(update(feed_mentions).where(
                feed_mentions.c.feed_id == feed_id,
                feed_mentions.c.raw_ticker == prior["raw_ticker"],
            ).values(
                raw_ticker=values["raw_ticker"],
                market=values["market"],
                occurrences=values["occurrences"],
            ))
            changed = True
    stale = set(existing) - set(desired_by_ticker)
    if stale:
        conn.execute(delete(feed_mentions).where(
            feed_mentions.c.feed_id == feed_id,
            feed_mentions.c.raw_ticker.in_([existing[t]["raw_ticker"] for t in stale]),
        ))
        changed = True
    return changed


def _dry_run_audit(rows, source_by_feed, unique_by_code, parsed_by_feed, config, audit):
    for row in rows:
        source_ticker = _resolved_source_ticker(row, source_by_feed, unique_by_code)
        mentioned = extract_feed_mentions(row.title, row.content)
        qualified = qualifies_feed(
            source_ticker,
            mentioned,
            config.policy_for(row.code),
            source_code=row.code,
        )
        item = audit[row.code]
        item["rawFeeds"] += 1
        item["rawPlatformComments"] += int(row.comment_count or 0)
        item["unresolvedSourceTickers"] += int(source_ticker is None)
        if qualified:
            item["qualifyingFeeds"] += 1
            item["filteredPlatformComments"] += int(row.comment_count or 0)
            item["parsedReplies"] += int(parsed_by_feed.get(row.feed_id, 0))


def _database_audit(engine, config):
    audit = {}
    with engine.connect() as conn:
        codes = list(conn.execute(select(feeds.c.code).distinct().order_by(feeds.c.code)).scalars())
        for code in codes:
            base = feeds.c.code == code
            qualify = qualifying_feed_predicate(feeds, config.policy_for(code))
            raw = conn.execute(select(
                func.count(),
                func.coalesce(func.sum(feeds.c.comment_count), 0),
                func.coalesce(
                    func.sum(case((~source_ticker_valid_predicate(feeds), 1), else_=0)),
                    0,
                ),
            ).where(base)).one()
            filtered = conn.execute(select(
                func.count(), func.coalesce(func.sum(feeds.c.comment_count), 0)
            ).where(base, qualify)).one()
            parsed = conn.execute(
                select(func.count())
                .select_from(comments.join(feeds, feeds.c.feed_id == comments.c.feed_id))
                .where(base, qualify)
            ).scalar_one()
            audit[code] = {
                "rawFeeds": int(raw[0]),
                "qualifyingFeeds": int(filtered[0]),
                "rawPlatformComments": int(raw[1]),
                "filteredPlatformComments": int(filtered[1]),
                "parsedReplies": int(parsed),
                "unresolvedSourceTickers": int(raw[2]),
            }
    return audit


COMMENT_TASKS = ("comment_product", "kol_comment_opinion")


def supersede_all_comment_jobs(
    conn,
    *,
    tasks=COMMENT_TASKS,
    reason="Superseded by destructive parent-feed fact rebuild",
):
    """Retire every comment task before a destructive fact-table rebuild.

    A still-qualifying parent is not enough to preserve a job: its title/body
    or reply text may have changed while row counts stayed constant.  Marking
    every old job superseded lets the normal enqueue path revive an identical
    hash or insert the newly computed hash without publishing stale output.
    """

    tasks = tuple(dict.fromkeys(str(task) for task in tasks))
    if not tasks:
        return 0
    result = conn.execute(update(annotation_jobs).where(
        annotation_jobs.c.target_type == "comment",
        annotation_jobs.c.task.in_(tasks),
        annotation_jobs.c.status != "superseded",
    ).values(
        status="superseded",
        lease_until=None,
        last_error=reason,
        updated_at=datetime.utcnow(),
    ))
    return int(result.rowcount or 0)


def supersede_ineligible_comment_jobs(conn, config, *, tasks=COMMENT_TASKS):
    """Retire comment jobs whose parent no longer belongs to their product.

    This is deliberately shared by the historical backfill and the destructive
    dump ETL.  Keeping retirement beside activation makes it impossible for a
    caller to publish a new filter generation while leaving old cross-product
    or excluded jobs executable.
    """

    tasks = tuple(dict.fromkeys(str(task) for task in tasks))
    if not tasks:
        return 0
    mutable = (
        annotation_jobs.c.target_type == "comment",
        annotation_jobs.c.task.in_(tasks),
        annotation_jobs.c.status.in_(("pending", "claimed", "failed", "done", "dead")),
    )
    all_ids = set(conn.execute(
        select(annotation_jobs.c.job_id).where(*mutable)
    ).scalars())
    eligible_ids = set(conn.execute(
        select(annotation_jobs.c.job_id)
        .select_from(
            annotation_jobs
            .join(comments, comments.c.comment_id == annotation_jobs.c.target_id)
            .join(feeds, feeds.c.feed_id == comments.c.feed_id)
        )
        .where(
            *mutable,
            annotation_jobs.c.subject_code == feeds.c.code,
            qualifying_feed_scope_predicate(feeds, config),
        )
    ).scalars())
    stale_ids = all_ids - eligible_ids
    if stale_ids:
        ordered_ids = sorted(stale_ids)
        for start in range(0, len(ordered_ids), 500):
            batch = ordered_ids[start:start + 500]
            conn.execute(update(annotation_jobs).where(
                annotation_jobs.c.job_id.in_(batch)
            ).values(
                status="superseded",
                lease_until=None,
                last_error=f"Excluded by {EXACT_RULE_VERSION}:{EXACT_RULE}",
                updated_at=datetime.utcnow(),
            ))
    return len(stale_ids)


def _invalid_source_ticker_count(conn):
    return int(conn.execute(
        select(func.count()).select_from(feeds).where(
            ~source_ticker_valid_predicate(feeds)
        )
    ).scalar_one())


def finalize_parent_filter(
    conn,
    config,
    *,
    activate,
    status_key=None,
    clear_backfill_state=False,
):
    """Invalidate dependent output and optionally publish filter readiness.

    ``activate`` is a request, not an assertion: the readiness keys are written
    only when the database itself contains zero invalid source tickers.  This
    matters for ETL callers, which must fail closed even if a stale/inaccurate
    counter claims that every ticker was resolved.
    """

    conn.execute(delete(meta_kv).where(meta_kv.c.k.in_(READINESS_KEYS)))

    # Rebuilding or backfilling parent facts changes both ordinary aggregates
    # and the exact input set seen by AI.  Refresh both revisions and make every
    # extant product/range regenerate before old synthesis can be served.
    ensure_ai_revision(conn)
    bump_revision(conn, "data")
    bump_revision(conn, "ai_input")
    codes = set(conn.execute(select(feeds.c.code).distinct()).scalars())
    codes.update(conn.execute(
        select(annotation_jobs.c.subject_code)
        .where(annotation_jobs.c.subject_code.is_not(None))
        .distinct()
    ).scalars())
    mark_synthesis(conn, codes, True)

    invalid = _invalid_source_ticker_count(conn)
    superseded = 0
    active = bool(activate and invalid == 0)
    if active:
        superseded = supersede_ineligible_comment_jobs(conn, config)
        for key, value in filter_readiness_values(config).items():
            _put_meta(conn, key, value)
    if status_key:
        _put_meta(conn, status_key, "active" if active else "populated")
    if clear_backfill_state:
        conn.execute(delete(meta_kv).where(meta_kv.c.k.in_((
            CURSOR_KEY,
            BACKFILL_DIGEST_KEY,
        ))))
    return {
        "active": active,
        "invalidSourceTickers": invalid,
        "supersededJobs": superseded,
    }


def prepare_comment_task_execution(
    engine,
    config=None,
    *,
    tasks=COMMENT_TASKS,
    supersede=False,
):
    """Fail closed before a production worker can consume comment jobs.

    Callers pass the returned config into ``annotate.claim`` so the claim query
    itself is constrained to the same parent-feed set.  The optional retirement
    sweep is used by low-frequency entrypoints; hot orchestration loops rely on
    activation retirement plus the claim predicate to avoid repeated full-job
    scans.
    """

    config = config or load_comment_filter_config()
    require_filter_ready(engine, config)
    retired = 0
    if supersede:
        with engine.begin() as conn:
            retired = supersede_ineligible_comment_jobs(
                conn,
                config,
                tasks=tasks,
            )
    return config, retired


def run(engine, *, batch_size=500, resume=False, activate=False, dry_run=False, after_feed_id=None):
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    if dry_run and activate:
        raise ValueError("--dry-run cannot be combined with --activate")
    if activate and after_feed_id is not None:
        raise ValueError("--activate cannot be combined with --after-feed-id")
    config = require_exact_rule_version(load_comment_filter_config())
    audit = defaultdict(lambda: {
        "rawFeeds": 0,
        "qualifyingFeeds": 0,
        "rawPlatformComments": 0,
        "filteredPlatformComments": 0,
        "parsedReplies": 0,
        "unresolvedSourceTickers": 0,
    })

    with engine.connect() as conn:
        unique_by_code = _unique_tickers_by_code(conn)
        saved_cursor = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == CURSOR_KEY)).scalar()
        saved_digest = conn.execute(
            select(meta_kv.c.v).where(meta_kv.c.k == BACKFILL_DIGEST_KEY)
        ).scalar()
    if resume and saved_cursor and saved_digest != config.digest:
        raise RuntimeError(
            "cannot resume parent-feed backfill after the filter config changed; "
            "start a new pass without --resume"
        )
    cursor = int(after_feed_id or (saved_cursor if resume and saved_cursor else 0))
    if not dry_run:
        with engine.begin() as conn:
            conn.execute(delete(meta_kv).where(meta_kv.c.k.in_(READINESS_KEYS)))
            if not resume and after_feed_id is None:
                _put_meta(conn, CURSOR_KEY, 0)
            _put_meta(conn, BACKFILL_DIGEST_KEY, config.digest)
            _put_meta(conn, STATUS_KEY, "running")

    processed = changed = unresolved = 0
    exhausted = False
    while not exhausted:
        with engine.connect() as conn:
            rows = list(conn.execute(
                select(
                    feeds.c.feed_id,
                    feeds.c.code,
                    feeds.c.source_ticker,
                    feeds.c.title,
                    feeds.c.content,
                    feeds.c.comment_count,
                )
                .where(feeds.c.feed_id > cursor)
                .order_by(feeds.c.feed_id)
                .limit(batch_size)
            ))
            source_by_feed = _source_tickers_for_feed_ids(
                conn, [row.feed_id for row in rows]
            )
            parsed_by_feed = dict(conn.execute(
                select(comments.c.feed_id, func.count())
                .where(comments.c.feed_id.in_([row.feed_id for row in rows]))
                .group_by(comments.c.feed_id)
            ).all())
        if not rows:
            exhausted = True
            break
        if dry_run:
            _dry_run_audit(
                rows, source_by_feed, unique_by_code, parsed_by_feed, config, audit
            )
        else:
            with engine.begin() as conn:
                for row in rows:
                    source_ticker = _resolved_source_ticker(
                        row, source_by_feed, unique_by_code
                    )
                    unresolved += int(source_ticker is None)
                    if source_ticker != row.source_ticker:
                        conn.execute(update(feeds).where(
                            feeds.c.feed_id == row.feed_id
                        ).values(source_ticker=source_ticker))
                        changed += 1
                    changed += int(_replace_mentions(
                        conn,
                        row.feed_id,
                        extract_feed_mentions(row.title, row.content),
                    ))
                cursor = int(rows[-1].feed_id)
                _put_meta(conn, CURSOR_KEY, cursor)
        processed += len(rows)
        cursor = int(rows[-1].feed_id)

    superseded = 0
    if not dry_run:
        audit = _database_audit(engine, config)
        unresolved = sum(row["unresolvedSourceTickers"] for row in audit.values())
        with engine.begin() as conn:
            _put_meta(conn, STATUS_KEY, "populated")
        if activate:
            if unresolved:
                raise RuntimeError(
                    f"cannot activate parent-feed filter: {unresolved} feeds have no source_ticker"
                )
            with engine.begin() as conn:
                finalized = finalize_parent_filter(
                    conn,
                    config,
                    activate=True,
                    status_key=STATUS_KEY,
                    clear_backfill_state=True,
                )
                if not finalized["active"]:
                    raise RuntimeError(
                        "cannot activate parent-feed filter: "
                        f"{finalized['invalidSourceTickers']} feeds have invalid source_ticker"
                    )
                superseded = finalized["supersededJobs"]

    return {
        "status": "dry_run" if dry_run else "active" if activate else "populated",
        "configVersion": config.version,
        "configDigest": config.digest,
        "processed": processed,
        "changedFacts": changed,
        "lastFeedId": cursor,
        "unresolvedSourceTickers": unresolved,
        "supersededJobs": superseded,
        "nextAction": (
            "run the normal extract/full_own job to enqueue newly eligible comments"
            if activate
            else "verify the audit, then rerun with --resume --activate"
        ),
        "products": dict(sorted(audit.items())),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="回填并激活父帖评论筛选事实")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--after-feed-id", type=int)
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = run(
        make_engine(),
        batch_size=args.batch_size,
        resume=args.resume,
        activate=args.activate,
        dry_run=args.dry_run,
        after_feed_id=args.after_feed_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
