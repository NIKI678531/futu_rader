import json

import pytest
from datetime import datetime, timezone
from sqlalchemy import delete, insert, select, update

from jobs.ingest import ingest
from collection.normalization import normalize_feed
from collection.models import SyncRequest
from collection.service import FutuRefresh
from collection.source import MemorySourceAdapter
from radar_db import create_all, make_engine
from radar_db.schema import (
    annotation_jobs,
    comment_product_routes,
    feed_mentions,
    feeds,
    mentions,
    meta_kv,
)


def seed_comment_jobs(conn, comment_ids, now):
    conn.execute(insert(annotation_jobs), [
        {
            "target_type": "comment",
            "target_id": comment_id,
            "subject_code": "3033",
            "task": task,
            "input_hash": f"{task}-{comment_id}",
            "status": "done",
            "stage": "llm",
            "priority": 0,
            "attempts": 0,
            "created_at": now,
            "updated_at": now,
        }
        for comment_id in comment_ids
        for task in ("comment_product", "kol_comment_opinion")
    ])


def test_sources_share_identity_and_update_without_rebuild(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "facts.sqlite").as_posix())
    create_all(engine)
    row = {"feed_id": 1, "code": "3033", "posted_at": "2026-08-01T10:00:00", "observed_at": "2026-08-02T10:00:00",
           "feed_type": 1, "content": "ETF", "like_count": 1, "comment_count": 0, "image_count": 0}
    assert ingest(engine, [row], "export-a", ["3033"])["updated"] == 1
    assert ingest(engine, [row], "export-b", ["3033"])["unchanged"] == 1
    with engine.connect() as conn:
        previous = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "data_revision")).scalar()
    assert ingest(engine, [{**row, "share_count": 0}], "export-b", ["3033"])["updated"] == 1
    with engine.connect() as conn:
        assert conn.execute(select(feeds.c.share_count)).scalar_one() == 0
        assert conn.execute(select(feeds.c.source_ticker)).scalar_one() is None
        assert conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "data_revision")).scalar() != previous
    with pytest.raises(ValueError, match="same observation"):
        ingest(engine, [{**row, "observed_at": "2026-09-01T10:00:00"}], "export-b", ["3033"])


def test_aware_timestamps_are_stored_as_utc_naive(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "facts-utc.sqlite").as_posix())
    create_all(engine)
    row = {
        "feed_id": 2, "code": "3033", "posted_at": "2026-08-01T18:00:00+08:00",
        "observed_at": "2026-08-02T18:00:00+08:00", "feed_type": 1,
        "content": "ETF", "like_count": 1, "comment_count": 0, "image_count": 0,
    }
    ingest(engine, [row], "export-a", ["3033"])
    with engine.connect() as conn:
        stored = conn.execute(select(feeds.c.posted_at, feeds.c.source_observed_at)).one()
    assert stored == (datetime(2026, 8, 1, 10, 0), datetime(2026, 8, 2, 10, 0))


def test_jsonl_rebuilds_parent_feed_mentions_authoritatively(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "facts-mentions.sqlite").as_posix())
    create_all(engine)
    base = {
        "feed_id": 3,
        "code": "3033",
        "source_ticker": "HK.03033",
        "posted_at": "2026-08-01T10:00:00",
        "observed_at": "2026-08-02T10:00:00",
        "feed_type": 1,
        "title": "$03033.HK$",
        "content": "$NVDA.US$ $NVDA.US$",
        "like_count": 1,
        "comment_count": 1,
        "image_count": 0,
        "comments": [{"comment_id": 30, "content": "$800000.HK$"}],
    }
    ingest(engine, [base], "repair-a", ["3033"])
    ingest(engine, [{**base, "content": "$brk.b.us$"}], "repair-b", ["3033"])

    with engine.connect() as conn:
        assert conn.execute(select(feeds.c.source_ticker)).scalar_one() == "03033.HK"
        mentions = {
            row.raw_ticker: row.occurrences
            for row in conn.execute(select(feed_mentions))
        }
    assert mentions == {"03033.HK": 1, "brk.b.us": 1}


def test_jsonl_reapplies_same_payload_when_materialized_facts_were_overwritten(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "stale-snapshot.sqlite").as_posix())
    create_all(engine)
    record = {
        "feed_id": 31,
        "code": "3033",
        "source_ticker": "03033.HK",
        "posted_at": "2026-08-01T10:00:00",
        "observed_at": "2026-08-02T10:00:00",
        "feed_type": 1,
        "content": "$03033.hk$ 修复正文",
        "like_count": 1,
        "comment_count": 1,
        "image_count": 0,
        "comments": [{"comment_id": 310, "content": "仍由父帖 tag 路由"}],
    }
    assert ingest(engine, [record], "repair", ["3033"])["updated"] == 1
    assert ingest(engine, [record], "repair", ["3033"])["unchanged"] == 1

    # Simulate a later dump/online writer replacing the facts while leaving the
    # repair audit snapshot intact.
    with engine.begin() as conn:
        conn.execute(update(feeds).where(feeds.c.feed_id == 31).values(
            content="$800000.HK$ 上游旧正文"
        ))
        conn.execute(delete(feed_mentions).where(feed_mentions.c.feed_id == 31))
        conn.execute(delete(comment_product_routes).where(
            comment_product_routes.c.feed_id == 31
        ))

    assert ingest(engine, [record], "repair", ["3033"])["updated"] == 1
    with engine.connect() as conn:
        assert conn.execute(select(feeds.c.content)).scalar_one() == "$03033.hk$ 修复正文"
        assert conn.execute(select(feed_mentions.c.raw_ticker)).scalars().all() == [
            "03033.hk"
        ]
        assert conn.execute(select(
            comment_product_routes.c.comment_id,
            comment_product_routes.c.subject_code,
        )).all() == [(310, "3033")]


def test_jsonl_duplicate_feed_id_preserves_original_section_anchor(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "anchor.sqlite").as_posix())
    create_all(engine)
    base = {
        "feed_id": 4,
        "code": "3033",
        "source_ticker": "03033.HK",
        "posted_at": "2026-08-01T10:00:00",
        "observed_at": "2026-08-02T10:00:00",
        "feed_type": 1,
        "content": "$03033.HK$",
        "like_count": 1,
        "comment_count": 0,
        "image_count": 0,
    }
    ingest(engine, [base], "export-a", ["3033", "3032"])
    ingest(
        engine,
        [{**base, "code": "3032", "source_ticker": "03032.HK", "content": "$03032.HK$"}],
        "export-b",
        ["3033", "3032"],
    )

    with engine.connect() as conn:
        stored = conn.execute(select(
            feeds.c.code,
            feeds.c.source_ticker,
            feeds.c.content,
        )).one()
        anchors = conn.execute(select(mentions.c.code).where(
            mentions.c.source == "anchor"
        )).scalars().all()
    assert stored == ("3033", "03033.HK", "$03032.HK$")
    assert anchors == ["3033"]


def test_jsonl_rejects_an_explicit_ticker_for_another_section(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "mismatch.sqlite").as_posix())
    create_all(engine)
    row = {
        "feed_id": 5,
        "code": "3033",
        "source_ticker": "03032.HK",
        "posted_at": "2026-08-01T10:00:00",
        "observed_at": "2026-08-02T10:00:00",
        "feed_type": 1,
        "like_count": 1,
        "comment_count": 0,
        "image_count": 0,
    }

    with pytest.raises(ValueError, match="discussion-section code"):
        ingest(engine, [row], "bad-export", ["3033"])


def test_jsonl_source_edits_supersede_comment_jobs_before_reextract(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "job-invalidation.sqlite").as_posix())
    create_all(engine)
    observed = datetime(2026, 8, 2, 10, 0)
    base = {
        "feed_id": 6,
        "code": "3033",
        "source_ticker": None,
        "posted_at": "2026-08-01T10:00:00",
        "observed_at": observed.isoformat(),
        "feed_type": 1,
        "title": "父帖",
        "content": "$03033.HK$ 原文",
        "like_count": 1,
        "comment_count": 2,
        "image_count": 0,
        "comments": [
            {"comment_id": 61, "content": "评论甲"},
            {"comment_id": 62, "content": "评论乙"},
        ],
    }
    ingest(engine, [base], "repair", ["3033"])
    with engine.begin() as conn:
        seed_comment_jobs(conn, (61, 62), observed)

    edited_reply = {
        **base,
        "comments": [
            {"comment_id": 61, "content": "评论甲已编辑"},
            {"comment_id": 62, "content": "评论乙"},
        ],
    }
    ingest(engine, [edited_reply], "repair", ["3033"])
    with engine.connect() as conn:
        states = {
            (row.target_id, row.task): row.status
            for row in conn.execute(select(
                annotation_jobs.c.target_id,
                annotation_jobs.c.task,
                annotation_jobs.c.status,
            ))
        }
    assert {states[(61, task)] for task in ("comment_product", "kol_comment_opinion")} == {
        "superseded"
    }
    assert {states[(62, task)] for task in ("comment_product", "kol_comment_opinion")} == {"done"}

    with engine.begin() as conn:
        conn.execute(update(annotation_jobs).values(status="done"))
    ingest(engine, [{**edited_reply, "source_ticker": "03033.HK"}], "repair", ["3033"])
    with engine.connect() as conn:
        assert set(conn.execute(select(annotation_jobs.c.status)).scalars()) == {"superseded"}


def test_jsonl_recovery_advances_semantic_revision_but_counter_only_change_does_not(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "recovery-revision.sqlite").as_posix())
    create_all(engine)
    observed = datetime(2026, 8, 2, 10, 0)
    source_row = {
        "feed_id": 1, "stock_id": 1, "feed_type": 1,
        "posted_at": datetime(2026, 8, 1, 10, 0), "author_uid": "u1",
        "author_name": "作者", "feed_title": "标题", "content_text": "原正文",
        "like_count": 1, "comment_count": 0, "image_count": 0,
        "raw_json": json.dumps({"common": {}, "comment": {"comment_items": []}}),
    }
    online = normalize_feed(source_row, "3033", observed)
    FutuRefresh(engine, MemorySourceAdapter({"feeds": [online]})).sync(
        SyncRequest(run_id="online")
    )
    with engine.begin() as conn:
        # Simulate a database migrated from before semantic revisions existed.
        conn.execute(delete(meta_kv).where(meta_kv.c.k == "ai_input_revision"))
        conn.execute(update(meta_kv).where(meta_kv.c.k == "data_revision").values(v="legacy-revision"))
        original_ai_revision = "legacy-revision"
        conn.execute(update(meta_kv).where(meta_kv.c.k.like("synth_dirty_%")).values(v="0"))

    recovery = {
        "feed_id": 1, "code": "3033", "posted_at": "2026-08-01T10:00:00",
        "observed_at": "2026-08-02T10:00:00", "feed_type": 1,
        "title": "标题", "content": "原正文", "like_count": 2,
        "comment_count": 0, "image_count": 0,
    }
    ingest(engine, [recovery], "recovery", ["3033"])
    with engine.connect() as conn:
        assert conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "ai_input_revision"
        )).scalar_one() == original_ai_revision
        assert conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "synth_dirty_3033_d1"
        )).scalar_one() == "0"

    ingest(engine, [{**recovery, "content": "修复后的正文"}], "recovery", ["3033"])
    with engine.connect() as conn:
        assert conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "ai_input_revision"
        )).scalar_one() != original_ai_revision
        assert conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "synth_dirty_3033_d1"
        )).scalar_one() == "1"
