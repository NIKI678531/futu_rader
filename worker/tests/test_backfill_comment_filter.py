"""Historical parent-feed-filter backfill and activation."""

import os
import sys
from datetime import datetime

import pytest
from sqlalchemy import delete, insert, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jobs import annotate
from jobs.backfill_comment_filter import prepare_comment_task_execution, run
from radar_db import create_all, make_engine
from radar_db.comment_filter import (
    COMMENT_FILTER_DIGEST_META_KEY,
    COMMENT_FILTER_READY_META_KEY,
    COMMENT_FILTER_VERSION_META_KEY,
    load_comment_filter_config,
)
from radar_db.schema import (
    annotation_jobs,
    comments,
    feed_mentions,
    feeds,
    meta_kv,
    src_feeds,
    src_stocks,
)


def _engine(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "backfill.sqlite").as_posix())
    create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(src_stocks).values(
            stock_id=1,
            ticker="03037.HK",
            market="HK",
            instrument_type="etf",
            name_zh="产品",
            name_en="Product",
        ))
        feed_rows = []
        facts = []
        for feed_id, content, count in (
            (1, "没有 cashtag", 4),
            (2, "$800000.HK$ 指数讨论", 5),
            (3, "$03037.HK$ 对比 $800000.HK$", 6),
        ):
            feed_rows.append({
                "feed_id": feed_id,
                "stock_id": 1,
                "feed_type": 1,
                "posted_at": datetime(2026, 8, 25, feed_id),
                "feed_title": "标题",
                "content_text": content,
                "like_count": 0,
                "comment_count": count,
                "image_count": 0,
                "raw_json": "{}",
                "scraped_at": datetime(2026, 8, 26),
            })
            facts.append({
                "feed_id": feed_id,
                "code": "3037",
                "source_ticker": None,
                "posted_at": datetime(2026, 8, 25, feed_id),
                "feed_type": 1,
                "title": "标题",
                "content": content,
                "like_count": 0,
                "comment_count": count,
                "image_count": 0,
                "raw_json_broken": False,
            })
        conn.execute(insert(src_feeds), feed_rows)
        conn.execute(insert(feeds), facts)
        conn.execute(insert(comments), [
            {"comment_id": 10, "feed_id": 1, "content": "保留"},
            {"comment_id": 20, "feed_id": 2, "content": "剔除"},
            {"comment_id": 30, "feed_id": 3, "content": "自身优先"},
        ])
        now = datetime(2026, 8, 26)
        conn.execute(insert(annotation_jobs), [
            {
                "job_id": 1, "target_type": "comment", "target_id": 20,
                "subject_code": "3037", "task": "comment_product", "input_hash": "excluded",
                "status": "pending", "priority": 0, "attempts": 0,
                "created_at": now, "updated_at": now,
            },
            {
                "job_id": 2, "target_type": "comment", "target_id": 10,
                "subject_code": "3033", "task": "comment_product", "input_hash": "cross-route",
                "status": "done", "priority": 0, "attempts": 1,
                "created_at": now, "updated_at": now,
            },
            {
                "job_id": 3, "target_type": "comment", "target_id": 10,
                "subject_code": "3037", "task": "comment_product", "input_hash": "eligible",
                "status": "pending", "priority": 0, "attempts": 0,
                "created_at": now, "updated_at": now,
            },
            {
                "job_id": 4, "target_type": "comment", "target_id": 20,
                "subject_code": "3037", "task": "kol_comment_opinion", "input_hash": "kol-excluded",
                "status": "done", "priority": 0, "attempts": 1,
                "created_at": now, "updated_at": now,
            },
            {
                "job_id": 50, "target_type": "comment", "target_id": 20,
                "subject_code": "3037", "task": "comment_product", "input_hash": "dead-excluded",
                "status": "dead", "priority": 0, "attempts": 3,
                "created_at": now, "updated_at": now,
            },
        ])
    return engine


def _meta(engine):
    with engine.connect() as conn:
        return dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v)).all())


def test_dry_run_audits_without_writing(tmp_path):
    engine = _engine(tmp_path)
    result = run(engine, dry_run=True, batch_size=1)

    assert result["processed"] == 3
    assert result["products"]["3037"] == {
        "rawFeeds": 3,
        "qualifyingFeeds": 2,
        "rawPlatformComments": 15,
        "filteredPlatformComments": 10,
        "parsedReplies": 2,
        "unresolvedSourceTickers": 0,
    }
    with engine.connect() as conn:
        assert conn.execute(select(feed_mentions)).all() == []
        assert conn.execute(select(feeds.c.source_ticker)).scalars().all() == [None, None, None]
    assert COMMENT_FILTER_READY_META_KEY not in _meta(engine)


def test_activation_cannot_skip_an_unverified_prefix(tmp_path):
    engine = _engine(tmp_path)

    with pytest.raises(ValueError, match="after-feed-id"):
        run(engine, activate=True, after_feed_id=1)


def test_backfill_resume_activate_is_idempotent_and_writes_readiness(tmp_path):
    engine = _engine(tmp_path)
    populated = run(engine, batch_size=1)
    assert populated["status"] == "populated"
    assert populated["changedFacts"] == 5  # three source tickers + two feeds with cashtags

    active = run(engine, batch_size=1, resume=True, activate=True)
    assert active["status"] == "active"
    assert active["processed"] == 0
    assert active["supersededJobs"] == 4
    assert active["products"]["3037"]["filteredPlatformComments"] == 10

    config = load_comment_filter_config()
    metadata = _meta(engine)
    assert metadata[COMMENT_FILTER_READY_META_KEY] == "1"
    assert metadata[COMMENT_FILTER_VERSION_META_KEY] == config.version
    assert metadata[COMMENT_FILTER_DIGEST_META_KEY] == config.digest
    with engine.connect() as conn:
        assert conn.execute(select(feeds.c.source_ticker).distinct()).scalar_one() == "03037.HK"
        stored = {
            (row.feed_id, row.raw_ticker, row.occurrences)
            for row in conn.execute(select(feed_mentions))
        }
        job_statuses = dict(conn.execute(select(
            annotation_jobs.c.job_id,
            annotation_jobs.c.status,
        )).all())
    assert stored == {
        (2, "800000.HK", 1),
        (3, "03037.HK", 1),
        (3, "800000.HK", 1),
    }
    assert job_statuses == {
        1: "superseded",
        2: "superseded",
        3: "pending",
        4: "superseded",
        50: "superseded",
    }

    # Once a later policy makes this unchanged input eligible, the normal job
    # insertion seam can revive it because activation retired `dead` as
    # `superseded` instead of leaving an unrecoverable terminal row.
    now = datetime(2026, 8, 27)
    assert annotate._insert_jobs(engine, [{
        "target_type": "comment",
        "target_id": 20,
        "subject_code": "3037",
        "task": "comment_product",
        "input_hash": "dead-excluded",
        "status": "pending",
        "priority": 0,
        "attempts": 0,
        "scope_id": None,
        "stage": "student",
        "created_at": now,
        "updated_at": now,
    }]) == 1
    with engine.connect() as conn:
        assert conn.execute(select(annotation_jobs.c.status).where(
            annotation_jobs.c.job_id == 50
        )).scalar_one() == "pending"

    rerun = run(engine, batch_size=2, resume=True, activate=True)
    assert rerun["changedFacts"] == 0
    assert rerun["status"] == "active"


def test_execution_guard_fails_on_digest_mismatch(tmp_path):
    engine = _engine(tmp_path)
    run(engine, activate=True)
    with engine.begin() as conn:
        conn.execute(
            meta_kv.update()
            .where(meta_kv.c.k == COMMENT_FILTER_DIGEST_META_KEY)
            .values(v="stale")
        )

    with pytest.raises(RuntimeError, match="not ready"):
        prepare_comment_task_execution(engine)


def test_guard_retires_invalid_jobs_and_claim_sql_cannot_execute_them(tmp_path):
    engine = _engine(tmp_path)
    run(engine, activate=True)
    config = load_comment_filter_config()
    with engine.begin() as conn:
        # Simulate an old producer reviving a task for the excluded parent.
        conn.execute(
            annotation_jobs.update()
            .where(annotation_jobs.c.job_id == 1)
            .values(status="pending")
        )

    guarded, retired = prepare_comment_task_execution(
        engine,
        config,
        tasks=("comment_product",),
        supersede=True,
    )
    with engine.begin() as conn:
        # Even if an obsolete producer races after the retirement sweep, the
        # claim query remains fail-closed and never sends this row to a model.
        conn.execute(
            annotation_jobs.update()
            .where(annotation_jobs.c.job_id == 1)
            .values(status="pending")
        )
    claimed = annotate.claim(
        engine,
        "comment_product",
        10,
        grouped=True,
        parent_filter_config=guarded,
    )

    assert retired == 1
    assert [row["job_id"] for row in claimed] == [3]
    with engine.connect() as conn:
        assert conn.execute(select(annotation_jobs.c.status).where(
            annotation_jobs.c.job_id == 1
        )).scalar_one() == "pending"


def test_claim_loads_the_filter_when_caller_omits_config(tmp_path):
    engine = _engine(tmp_path)
    run(engine, activate=True)

    claimed = annotate.claim(engine, "comment_product", 10)

    assert [row["job_id"] for row in claimed] == [3]


def test_claim_rechecks_readiness_after_it_is_revoked(tmp_path):
    engine = _engine(tmp_path)
    run(engine, activate=True)
    now = datetime(2026, 8, 26)
    with engine.begin() as conn:
        conn.execute(insert(annotation_jobs).values(
            job_id=6,
            target_type="comment",
            target_id=30,
            subject_code="3037",
            task="comment_product",
            input_hash="eligible-self",
            status="pending",
            priority=0,
            attempts=0,
            created_at=now,
            updated_at=now,
        ))

    assert [row["job_id"] for row in annotate.claim(
        engine,
        "comment_product",
        1,
    )] == [3]

    with engine.begin() as conn:
        conn.execute(delete(meta_kv).where(
            meta_kv.c.k == COMMENT_FILTER_READY_META_KEY
        ))

    with pytest.raises(RuntimeError, match="not ready"):
        annotate.claim(engine, "comment_product", 1)
    with engine.connect() as conn:
        assert conn.execute(select(annotation_jobs.c.status).where(
            annotation_jobs.c.job_id == 6
        )).scalar_one() == "pending"
