"""`jobs/etl.py` 的 `stamp()` —— 事实表的「代」。

ETL 重建事实表后会刷新数据/AI 输入 revision，并用一个内容敏感的稳定代标记这批事实。
没有这些标记，仍在运行的后端会继续服务旧缓存，合成层也无法知道输入已经改变。

（ETL 主体要 `src_*` 镜像与真实 dump 才跑得动，不在这里测；见 `test_dumpio.py`。）
"""

import os
import sys

import json
from dataclasses import replace
from datetime import datetime

import pytest
from sqlalchemy import insert, select, update

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from jobs import etl  # noqa: E402
from radar_db import create_all, make_engine  # noqa: E402
from radar_db.comment_filter import (  # noqa: E402
    COMMENT_FILTER_READY_META_KEY,
    load_comment_filter_config,
)
from radar_db.schema import (  # noqa: E402
    annotation_jobs,
    comments,
    feed_mentions,
    feeds,
    mentions,
    meta_kv,
    src_feeds,
    src_stocks,
)

STATS = {
    "feeds": 504400,
    "broken": 456,
    "comments": 1200000,
    "mentions": 600000,
    "feed_mentions": 250000,
}


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine("sqlite:///" + (tmp_path / "t.db").as_posix())
    create_all(eng)
    return eng


def _meta(engine):
    with engine.connect() as conn:
        return {k: v for k, v in conn.execute(select(meta_kv.c.k, meta_kv.c.v))}


def test_stamp_rejects_filter_config_version_that_worker_cannot_execute(engine):
    incompatible = replace(load_comment_filter_config(), version="parent-feed-v999")

    with pytest.raises(RuntimeError, match="worker rule version"):
        etl.stamp(engine, STATS, incompatible)


def test_stamp_records_what_the_fact_tables_contain(engine):
    etl.stamp(engine, STATS)
    v = _meta(engine)["etl_generation"]
    for part in (
        "feeds=504400", "comments=1200000", "mentions=600000",
        "feed_mentions=250000", "broken=456",
    ):
        assert part in v


def test_rerunning_on_the_same_source_does_not_change_the_marker(engine):
    """内容代取规范化事实的 digest 而不是时间戳。

    ETL 是可重跑的：同样的 `src_*` 重跑出来就是同一批行，因此内容代保持稳定；独立
    revision 仍会刷新，以覆盖破坏性重建中断或外部消费者持有旧快照的情况。
    """
    etl.stamp(engine, STATS)
    first = _meta(engine)["etl_generation"]
    etl.stamp(engine, STATS)
    assert _meta(engine)["etl_generation"] == first


def test_a_different_row_count_changes_the_marker(engine):
    etl.stamp(engine, STATS)
    first = _meta(engine)["etl_generation"]
    etl.stamp(engine, {**STATS, "comments": 1200001})
    assert _meta(engine)["etl_generation"] != first


def test_same_counts_but_different_content_digest_changes_the_marker(engine):
    etl.stamp(engine, {**STATS, "content_digest": "a" * 64})
    first = _meta(engine)["etl_generation"]
    etl.stamp(engine, {**STATS, "content_digest": "b" * 64})
    assert _meta(engine)["etl_generation"] != first


def test_etl_content_change_with_same_counts_changes_generation(engine):
    with engine.begin() as conn:
        conn.execute(insert(src_stocks).values(
            stock_id=1,
            ticker="03033.HK",
            market="HK",
            instrument_type="etf",
            name_zh="产品",
            name_en="Product",
        ))
        conn.execute(insert(src_feeds).values(
            feed_id=1,
            stock_id=1,
            feed_type=1,
            posted_at=datetime(2026, 8, 25),
            feed_title="标题",
            content_text="$03033.HK$ first",
            like_count=0,
            comment_count=0,
            image_count=0,
            raw_json="{}",
            scraped_at=datetime(2026, 8, 26),
        ))

    first_stats = etl.run(engine, batch_size=1)
    first_generation = _meta(engine)["etl_generation"]
    with engine.begin() as conn:
        conn.execute(insert(comments).values(
            comment_id=99, feed_id=1, content="old parsed reply",
        ))
        now = datetime(2026, 8, 27)
        conn.execute(insert(annotation_jobs), [
            {
                "job_id": 1, "target_type": "comment", "target_id": 99,
                "subject_code": "3033", "task": "comment_product",
                "input_hash": "old-product", "status": "done", "priority": 0,
                "attempts": 1, "created_at": now, "updated_at": now,
            },
            {
                "job_id": 2, "target_type": "comment", "target_id": 99,
                "subject_code": "3033", "task": "kol_comment_opinion",
                "input_hash": "old-kol", "status": "pending", "priority": 0,
                "attempts": 0, "created_at": now, "updated_at": now,
            },
        ])
        conn.execute(update(src_feeds).where(
            src_feeds.c.feed_id == 1
        ).values(content_text="$03033.HK$ second"))
    second_stats = etl.run(engine, batch_size=1)

    assert first_stats["feeds"] == second_stats["feeds"] == 1
    assert first_stats["content_digest"] != second_stats["content_digest"]
    assert second_stats["superseded_comment_jobs"] == 2
    assert _meta(engine)["etl_generation"] != first_generation
    with engine.connect() as conn:
        assert set(conn.execute(select(annotation_jobs.c.status)).scalars()) == {
            "superseded"
        }


def test_stamp_leaves_the_import_keys_alone(engine):
    """锚点是 `import_dump` 写的。ETL 顺手清掉它会让后端的区间整个变成未知。"""
    from sqlalchemy import insert

    with engine.begin() as conn:
        conn.execute(insert(meta_kv), [
            {"k": "anchor", "v": "2026-08-25"},
            {"k": "anchor_ts", "v": "2026-08-25 23:59:59"},
        ])
    etl.stamp(engine, STATS)
    etl.stamp(engine, {**STATS, "feeds": 1})  # 改写自己那一行，不牵连别人
    m = _meta(engine)
    assert m["anchor"] == "2026-08-25"
    assert m["anchor_ts"] == "2026-08-25 23:59:59"
    assert "etl_generation" in m


def test_stamp_does_not_activate_filter_with_unresolved_source_tickers(engine):
    etl.stamp(engine, STATS)
    assert _meta(engine)[COMMENT_FILTER_READY_META_KEY] == "1"

    etl.stamp(engine, {**STATS, "unresolved_source_tickers": 1})
    assert COMMENT_FILTER_READY_META_KEY not in _meta(engine)


def test_stamp_validates_database_tickers_even_when_counter_claims_zero(engine):
    with engine.begin() as conn:
        conn.execute(insert(feeds).values(
            feed_id=1,
            code="3033",
            source_ticker="WRONG.HK",
            posted_at=datetime(2026, 8, 25),
            feed_type=1,
            like_count=0,
            comment_count=0,
            image_count=0,
            raw_json_broken=False,
        ))

    result = etl.stamp(engine, {**STATS, "unresolved_source_tickers": 0})

    assert result["active"] is False
    assert result["invalidSourceTickers"] == 1
    assert COMMENT_FILTER_READY_META_KEY not in _meta(engine)


def test_stamp_invalidates_revisions_marks_dirty_and_retires_both_comment_tasks(engine):
    now = datetime(2026, 8, 26)
    with engine.begin() as conn:
        conn.execute(insert(feeds), [
            {
                "feed_id": 1, "code": "3033", "source_ticker": "03033.HK",
                "posted_at": now, "feed_type": 1, "like_count": 0,
                "comment_count": 1, "image_count": 0, "raw_json_broken": False,
            },
            {
                "feed_id": 2, "code": "3033", "source_ticker": "03033.HK",
                "posted_at": now, "feed_type": 1, "like_count": 0,
                "comment_count": 1, "image_count": 0, "raw_json_broken": False,
            },
        ])
        conn.execute(insert(feed_mentions).values(
            feed_id=1, raw_ticker="03033.HK", market="HK", occurrences=1,
        ))
        conn.execute(insert(comments), [
            {"comment_id": 1, "feed_id": 1, "content": "qualified"},
            {"comment_id": 2, "feed_id": 2, "content": "unqualified"},
        ])
        conn.execute(insert(annotation_jobs), [
            {
                "job_id": 1, "target_type": "comment", "target_id": 1,
                "subject_code": "3033", "task": "comment_product",
                "input_hash": "ok", "status": "pending", "priority": 0,
                "attempts": 0, "created_at": now, "updated_at": now,
            },
            {
                "job_id": 2, "target_type": "comment", "target_id": 2,
                "subject_code": "3033", "task": "comment_product",
                "input_hash": "bad", "status": "pending", "priority": 0,
                "attempts": 0, "created_at": now, "updated_at": now,
            },
            {
                "job_id": 3, "target_type": "comment", "target_id": 2,
                "subject_code": "3033", "task": "kol_comment_opinion",
                "input_hash": "kol-bad", "status": "done", "priority": 0,
                "attempts": 1, "created_at": now, "updated_at": now,
            },
        ])

    result = etl.stamp(engine, STATS)
    metadata = _meta(engine)
    with engine.connect() as conn:
        statuses = dict(conn.execute(select(
            annotation_jobs.c.job_id, annotation_jobs.c.status,
        )).all())

    assert result["active"] is True
    assert result["supersededJobs"] == 2
    assert statuses == {1: "pending", 2: "superseded", 3: "superseded"}
    assert metadata[COMMENT_FILTER_READY_META_KEY] == "1"
    assert metadata["data_revision"]
    assert metadata["ai_input_revision"]
    assert metadata["synth_dirty_3033_d7"] == "1"


def test_etl_reuses_live_normalization_for_self_body_comments_and_coverage(engine):
    payload = {
        "summary": {
            "rich_text": [
                {
                    "type": 3,
                    "stock": {"stock_code": "03037", "market_type_label": "HK"},
                }
            ]
        },
        "comment": {
            "has_more": True,
            "comment_items": [
                {
                    "comment_id": "9001",
                    "content": "评论正文 fallback",
                    "rich_text_items": [],
                    "author": {"user_id": "u1", "nick_name": "用户"},
                    "timestamp": "1780000000",
                    "like": {"liked_num": 2},
                }
            ],
        },
    }
    with engine.begin() as conn:
        conn.execute(insert(src_stocks).values(
            stock_id=1,
            ticker="03037.HK",
            market="HK",
            instrument_type="etf",
            name_zh="产品",
            name_en="Product",
        ))
        conn.execute(insert(src_feeds).values(
            feed_id=1001,
            stock_id=1,
            feed_type=1,
            posted_at=datetime(2026, 8, 25, 1),
            author_uid=None,
            author_name=None,
            feed_title="$产品 (03037.HK)$",
            content_text="$华夏比特币ETF (03042.hk)$",
            like_count=1,
            comment_count=2,
            image_count=0,
            raw_json=json.dumps(payload, ensure_ascii=False),
            scraped_at=datetime(2026, 8, 25, 2),
        ))

    etl.run(engine, batch_size=1)

    with engine.connect() as conn:
        feed = conn.execute(select(feeds).where(feeds.c.feed_id == 1001)).mappings().one()
        comment = conn.execute(
            select(comments).where(comments.c.comment_id == 9001)
        ).mappings().one()
        body_mentions = set(conn.execute(
            select(mentions.c.code).where(
                mentions.c.feed_id == 1001,
                mentions.c.source == "body",
            )
        ).scalars())
        raw_mentions = {
            row.raw_ticker: row.occurrences
            for row in conn.execute(select(feed_mentions).where(
                feed_mentions.c.feed_id == 1001
            ))
        }

    assert feed["comments_parsed"] == 1
    assert feed["comments_truncated"] is True
    assert feed["comment_coverage_status"] == "partial"
    assert feed["source_observed_at"] == datetime(2026, 8, 25, 2)
    assert feed["source_ticker"] == "03037.HK"
    assert comment["content"] == "评论正文 fallback"
    assert {"3037", "3042"} <= body_mentions
    assert raw_mentions == {"03037.HK": 1, "03042.hk": 1}
