import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, insert, select, update

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "worker"))

from radar_db import make_engine
from radar_db.revisions import bump_revision, ensure_ai_revision, mark_synthesis
from radar_db.schema import comments, feeds, mentions, source_snapshots, src_feeds, meta_kv


SEMANTIC_FEED_FIELDS = ("code", "posted_at", "feed_type", "title", "content")
SEMANTIC_COMMENT_FIELDS = ("feed_id", "posted_at", "author_uid", "content", "reply_to_comment_id")


def _changed(previous, values, fields):
    return any(previous.get(field) != values.get(field) for field in fields)


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
            if snapshot and snapshot["input_hash"] == digest:
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
            values.update(
                raw_json_broken=previous["raw_json_broken"] if previous else False,
                source_observed_at=observed,
                comment_coverage_status=(previous["comment_coverage_status"] if previous else "unknown"),
            )
            affected_codes = {model.code}
            if previous and previous.get("code"):
                affected_codes.add(previous["code"])
            affected_codes.update(conn.execute(select(mentions.c.code).where(
                mentions.c.feed_id == model.feed_id,
                mentions.c.in_pool.is_(True),
            )).scalars())
            if previous:
                values = {key: value for key, value in values.items() if value is not None or previous.get(key) is None}
                semantic_changed = _changed(previous, values, SEMANTIC_FEED_FIELDS)
                conn.execute(update(feeds).where(feeds.c.feed_id == model.feed_id).values(**values))
            else:
                semantic_changed = True
                conn.execute(insert(feeds).values(**values))
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
                else:
                    values = {
                        key: value if value is not None or existing_comment.get(key) is None
                        else existing_comment[key]
                        for key, value in values.items()
                    }
                    semantic_changed = semantic_changed or _changed(
                        existing_comment, values, SEMANTIC_COMMENT_FIELDS
                    )
                    conn.execute(update(comments).where(comments.c.comment_id == comment.comment_id).values(**values))
            for code in set(model.mentioned_codes) | {model.code}:
                kind = "anchor" if code == model.code else "body"
                match = (mentions.c.feed_id == model.feed_id, mentions.c.code == code, mentions.c.source == kind)
                if conn.execute(select(mentions.c.feed_id).where(*match)).first() is None:
                    conn.execute(insert(mentions).values(feed_id=model.feed_id, code=code, source=kind, in_pool=True))
                    semantic_changed = True
                    affected_codes.add(code)
            parsed = conn.execute(select(func.count()).select_from(comments).where(
                comments.c.feed_id == model.feed_id
            )).scalar_one()
            conn.execute(update(feeds).where(feeds.c.feed_id == model.feed_id).values(
                comments_parsed=parsed
            ))
            values = {"feed_id": model.feed_id, "source": source, "input_hash": digest,
                      "payload_json": json.dumps(payload, ensure_ascii=False), "observed_at": observed}
            if snapshot:
                conn.execute(update(source_snapshots).where(source_snapshots.c.feed_id == model.feed_id).values(**values))
            else:
                conn.execute(insert(source_snapshots).values(**values))
            ensure_ai_revision(conn)
            bump_revision(conn, "data")
            if semantic_changed:
                bump_revision(conn, "ai_input")
                mark_synthesis(conn, affected_codes | set(model.mentioned_codes), True)
            counts["updated"] += 1
    return counts


def main():
    parser = argparse.ArgumentParser(description="Import normalized Futu JSONL without rebuilding the database")
    parser.add_argument("--file", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--complete-through", type=date.fromisoformat,
                        help="Upstream-declared complete HKT day; omit for partial exports")
    args = parser.parse_args()
    master = json.loads((ROOT / "backend/fixtures/demo/master.json").read_text(encoding="utf-8"))
    with open(args.file, encoding="utf-8") as stream:
        rows = (json.loads(line) for line in stream if line.strip())
        engine = make_engine()
        result = ingest(engine, rows, args.source, [row["code"] for row in master["products"]])
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
