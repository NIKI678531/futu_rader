import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, insert, select, update

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "worker"))

from collection.exact import normalize_futu_ticker
from radar_db import make_engine
from radar_db.comment_filter import extract_feed_mentions, normalize_source_ticker
from radar_db.comment_job_invalidation import supersede_comment_analysis_jobs
from radar_db.comment_routes import replace_comment_routes, route_rows
from radar_db.product_catalog import load_products
from radar_db.revisions import bump_revision, ensure_ai_revision, mark_synthesis
from radar_db.schema import (
    comments,
    comment_product_routes,
    feed_mentions,
    feeds,
    mentions,
    source_snapshots,
    src_feeds,
    meta_kv,
)


SEMANTIC_FEED_FIELDS = (
    "code", "source_ticker", "posted_at", "feed_type", "title", "content"
)
SEMANTIC_COMMENT_FIELDS = ("feed_id", "posted_at", "author_uid", "content", "reply_to_comment_id")


def _changed(previous, values, fields):
    return any(previous.get(field) != values.get(field) for field in fields)


def _materialized_fact_hash(conn, feed_id):
    """Fingerprint every fact a JSONL repair can materialize for one feed.

    ``source_snapshots`` outlives other writers, so an input hash alone cannot
    prove that the repair is still present.  Binding idempotency to the current
    materialized rows makes the shortcut safe after dump ETL, online refreshes,
    backfills, or an interrupted rebuild.
    """

    feed = conn.execute(
        select(feeds).where(feeds.c.feed_id == feed_id)
    ).mappings().first()
    if feed is None:
        return None
    facts = {
        "feed": dict(feed),
        "comments": [
            dict(row)
            for row in conn.execute(
                select(comments)
                .where(comments.c.feed_id == feed_id)
                .order_by(comments.c.comment_id)
            ).mappings()
        ],
        "mentions": [
            dict(row)
            for row in conn.execute(
                select(mentions)
                .where(mentions.c.feed_id == feed_id)
                .order_by(mentions.c.code, mentions.c.source)
            ).mappings()
        ],
        "feed_mentions": [
            dict(row)
            for row in conn.execute(
                select(feed_mentions)
                .where(feed_mentions.c.feed_id == feed_id)
                .order_by(feed_mentions.c.raw_ticker)
            ).mappings()
        ],
        "comment_product_routes": [
            dict(row)
            for row in conn.execute(
                select(comment_product_routes)
                .where(comment_product_routes.c.feed_id == feed_id)
                .order_by(
                    comment_product_routes.c.comment_id,
                    comment_product_routes.c.subject_code,
                )
            ).mappings()
        ],
    }
    material = json.dumps(
        facts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _snapshot_hash(input_hash, fact_hash):
    if fact_hash is None:
        return None
    return hashlib.sha256(f"{input_hash}:{fact_hash}".encode("ascii")).hexdigest()


class CommentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    comment_id: int = Field(gt=0)
    content: str
    posted_at: datetime | None = None
    author_uid: str | None = None
    author_name: str | None = None
    like_count: int | None = Field(default=None, ge=0)
    reply_to_comment_id: int | None = None


class FeedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feed_id: int = Field(gt=0)
    code: str
    source_ticker: str | None = None
    posted_at: datetime
    observed_at: datetime
    feed_type: int
    title: str | None = None
    content: str | None = None
    author_uid: str | None = None
    author_name: str | None = None
    like_count: int = Field(ge=0)
    comment_count: int = Field(ge=0)
    image_count: int = Field(ge=0)
    share_count: int | None = Field(default=None, ge=0)
    browse_count: int | None = Field(default=None, ge=0)
    original_lang: int | None = None
    comments: list[CommentRecord] = Field(default_factory=list)
    mentioned_codes: list[str] = Field(default_factory=list)


def local_time(value):
    """Return the database's canonical UTC-naive timestamp.

    Historical imports already store UTC-naive values. Converting aware JSONL
    timestamps to HKT here made online and historical rows incomparable.
    """
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def ingest(engine, records, source, pool):
    counts = {"updated": 0, "unchanged": 0}
    for record in records:
        model = FeedRecord.model_validate(record)
        if model.code not in pool or not set(model.mentioned_codes) <= set(pool):
            raise ValueError("Unknown product mapping")
        payload = model.model_dump(mode="json")
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with engine.begin() as conn:
            snapshot = conn.execute(select(source_snapshots).where(source_snapshots.c.feed_id == model.feed_id)).mappings().first()
            current_snapshot_hash = _snapshot_hash(
                digest,
                _materialized_fact_hash(conn, model.feed_id),
            )
            if (
                snapshot
                and snapshot["input_hash"] == current_snapshot_hash
            ):
                counts["unchanged"] += 1
                continue
            previous = conn.execute(select(feeds).where(feeds.c.feed_id == model.feed_id)).mappings().first()
            observed = local_time(model.observed_at)
            original_scrape = conn.execute(select(src_feeds.c.scraped_at).where(src_feeds.c.feed_id == model.feed_id)).scalar()
            expected_observed = snapshot["observed_at"] if snapshot else original_scrape
            if previous and expected_observed and observed != expected_observed:
                raise ValueError("Historical counter repair requires the same observation timestamp")
            values = model.model_dump(exclude={"observed_at", "comments", "mentioned_codes"})
            values["posted_at"] = local_time(model.posted_at)
            source_ticker = normalize_source_ticker(model.source_ticker)
            if model.source_ticker is not None and source_ticker is None:
                raise ValueError("Invalid source_ticker")
            if source_ticker is not None and normalize_futu_ticker(source_ticker) != model.code:
                raise ValueError("source_ticker does not match the feed's discussion-section code")
            values["source_ticker"] = source_ticker
            values.update(
                raw_json_broken=previous["raw_json_broken"] if previous else False,
                source_observed_at=observed,
                comment_coverage_status=(previous["comment_coverage_status"] if previous else "unknown"),
            )
            affected_codes = {
                previous["code"] if previous and previous.get("code") else model.code
            }
            parent_input_changed = False
            if previous:
                incoming_anchor_code = values["code"]
                values["code"] = previous["code"]
                if previous.get("source_ticker") is not None:
                    values["source_ticker"] = previous["source_ticker"]
                elif incoming_anchor_code != previous["code"]:
                    values["source_ticker"] = None
                values = {key: value for key, value in values.items() if value is not None or previous.get(key) is None}
                parent_input_changed = _changed(
                    previous, values, ("source_ticker", "title", "content")
                )
                semantic_changed = _changed(previous, values, SEMANTIC_FEED_FIELDS)
                conn.execute(update(feeds).where(feeds.c.feed_id == model.feed_id).values(**values))
            else:
                semantic_changed = True
                conn.execute(insert(feeds).values(**values))
            changed_comment_ids = set()
            for comment in model.comments:
                values = comment.model_dump()
                values.update(feed_id=model.feed_id, posted_at=local_time(comment.posted_at))
                existing_comment = conn.execute(select(comments).where(
                    comments.c.comment_id == comment.comment_id
                )).mappings().first()
                if existing_comment is not None and existing_comment["feed_id"] != model.feed_id:
                    raise ValueError("Comment identity belongs to another feed")
                if existing_comment is None:
                    conn.execute(insert(comments).values(**values))
                    semantic_changed = True
                    changed_comment_ids.add(comment.comment_id)
                else:
                    values = {
                        key: value if value is not None or existing_comment.get(key) is None
                        else existing_comment[key]
                        for key, value in values.items()
                    }
                    comment_changed = _changed(
                        existing_comment, values, SEMANTIC_COMMENT_FIELDS
                    )
                    semantic_changed = semantic_changed or comment_changed
                    if comment_changed:
                        changed_comment_ids.add(comment.comment_id)
                    conn.execute(update(comments).where(comments.c.comment_id == comment.comment_id).values(**values))
            canonical_anchor = previous["code"] if previous else model.code
            mention_pairs = [(canonical_anchor, "anchor")]
            mention_pairs.extend((code, "body") for code in set(model.mentioned_codes))
            for code, kind in mention_pairs:
                match = (mentions.c.feed_id == model.feed_id, mentions.c.code == code, mentions.c.source == kind)
                if conn.execute(select(mentions.c.feed_id).where(*match)).first() is None:
                    conn.execute(insert(mentions).values(feed_id=model.feed_id, code=code, source=kind, in_pool=True))
                    semantic_changed = True
            removed_anchors = conn.execute(delete(mentions).where(
                mentions.c.feed_id == model.feed_id,
                mentions.c.source == "anchor",
                mentions.c.code != canonical_anchor,
            )).rowcount
            semantic_changed = semantic_changed or bool(removed_anchors)
            persisted_parent = conn.execute(
                select(feeds.c.title, feeds.c.content).where(
                    feeds.c.feed_id == model.feed_id
                )
            ).one()
            desired_feed_mentions = {
                normalize_source_ticker(mention.raw_ticker): mention
                for mention in extract_feed_mentions(
                    persisted_parent.title,
                    persisted_parent.content,
                )
            }
            existing_feed_mentions = {
                normalize_source_ticker(row["raw_ticker"]): row
                for row in conn.execute(select(feed_mentions).where(
                    feed_mentions.c.feed_id == model.feed_id
                )).mappings()
            }
            filter_changed = False
            for ticker, mention in desired_feed_mentions.items():
                prior = existing_feed_mentions.get(ticker)
                mention_values = mention.as_row(model.feed_id)
                if prior is None:
                    conn.execute(insert(feed_mentions).values(**mention_values))
                    semantic_changed = True
                    filter_changed = True
                elif any(
                    prior.get(field) != mention_values[field]
                    for field in ("raw_ticker", "market", "occurrences")
                ):
                    conn.execute(update(feed_mentions).where(
                        feed_mentions.c.feed_id == model.feed_id,
                        feed_mentions.c.raw_ticker == prior["raw_ticker"],
                    ).values(
                        raw_ticker=mention_values["raw_ticker"],
                        market=mention_values["market"],
                        occurrences=mention_values["occurrences"],
                    ))
                    semantic_changed = True
                    filter_changed = True
            stale_tickers = set(existing_feed_mentions) - set(desired_feed_mentions)
            if stale_tickers:
                conn.execute(delete(feed_mentions).where(
                    feed_mentions.c.feed_id == model.feed_id,
                    feed_mentions.c.raw_ticker.in_([
                        existing_feed_mentions[ticker]["raw_ticker"]
                        for ticker in stale_tickers
                    ]),
                ))
                semantic_changed = True
                filter_changed = True
            parsed = conn.execute(select(func.count()).select_from(comments).where(
                comments.c.feed_id == model.feed_id
            )).scalar_one()
            conn.execute(update(feeds).where(feeds.c.feed_id == model.feed_id).values(
                comments_parsed=parsed
            ))
            if parent_input_changed or filter_changed or changed_comment_ids:
                supersede_comment_analysis_jobs(
                    conn,
                    feed_ids=(model.feed_id,)
                    if parent_input_changed or filter_changed
                    else (),
                    comment_ids=changed_comment_ids,
                    reason=(
                        "Parent feed input changed"
                        if parent_input_changed or filter_changed
                        else "Comment input changed"
                    ),
                )
            route_comment_ids = set(changed_comment_ids)
            materialized_repair = (
                snapshot is None
                or snapshot["input_hash"] != current_snapshot_hash
            )
            if parent_input_changed or filter_changed or previous is None or materialized_repair:
                route_comment_ids.update(
                    conn.execute(
                        select(comments.c.comment_id).where(
                            comments.c.feed_id == model.feed_id
                        )
                    ).scalars()
                )
            if route_comment_ids:
                parent_text = conn.execute(
                    select(feeds.c.title, feeds.c.content).where(
                        feeds.c.feed_id == model.feed_id
                    )
                ).one()
                comment_texts = dict(
                    conn.execute(
                        select(comments.c.comment_id, comments.c.content).where(
                            comments.c.comment_id.in_(route_comment_ids)
                        )
                    ).all()
                )
                for comment_id in route_comment_ids:
                    route_change = replace_comment_routes(
                        conn,
                        comment_id,
                        route_rows(
                            comment_id,
                            model.feed_id,
                            parent_text.title,
                            parent_text.content,
                            comment_texts.get(comment_id),
                            now=observed,
                            pool_codes=pool,
                        ),
                    )
                    affected_codes.update(route_change["added"])
                    affected_codes.update(route_change["removed"])
                    semantic_changed = semantic_changed or bool(
                        route_change["added"] or route_change["removed"]
                    )
            values = {
                "feed_id": model.feed_id,
                "source": source,
                "input_hash": _snapshot_hash(
                    digest,
                    _materialized_fact_hash(conn, model.feed_id),
                ),
                "payload_json": json.dumps(payload, ensure_ascii=False),
                "observed_at": observed,
            }
            if snapshot:
                conn.execute(update(source_snapshots).where(source_snapshots.c.feed_id == model.feed_id).values(**values))
            else:
                conn.execute(insert(source_snapshots).values(**values))
            ensure_ai_revision(conn)
            bump_revision(conn, "data")
            if semantic_changed:
                bump_revision(conn, "ai_input")
                mark_synthesis(conn, affected_codes, True)
            counts["updated"] += 1
    return counts


def main():
    parser = argparse.ArgumentParser(description="Import normalized Futu JSONL without rebuilding the database")
    parser.add_argument("--file", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--complete-through", type=date.fromisoformat,
                        help="Upstream-declared complete HKT day; omit for partial exports")
    args = parser.parse_args()
    with open(args.file, encoding="utf-8") as stream:
        rows = (json.loads(line) for line in stream if line.strip())
        engine = make_engine()
        result = ingest(engine, rows, args.source, [row["code"] for row in load_products()])
    if args.complete_through:
        with engine.begin() as conn:
            current = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "anchor")).scalar()
            if current is not None and args.complete_through.isoformat() < current:
                raise ValueError("A source watermark cannot move backwards")
            values = {"anchor": args.complete_through.isoformat(),
                      "anchor_ts": args.complete_through.isoformat() + " 23:59:59"}
            for key, value in values.items():
                if not conn.execute(update(meta_kv).where(meta_kv.c.k == key).values(v=value)).rowcount:
                    conn.execute(insert(meta_kv).values(k=key, v=value))
            bump_revision(conn, "data")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
