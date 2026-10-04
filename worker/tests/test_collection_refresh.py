import json
import os
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import Column, Date, DateTime, Integer, MetaData, String, Table, Text, func, insert, select, update
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ai.providers.base import RunControl, RunStopped
from collection.budget import DailyBudget
from collection.models import AiRequest, AiResult, Cursor, SyncRequest, UserObservation
from collection.normalization import normalize_feed
from collection.service import FutuRefresh
from collection import source as source_module
from collection.source import (
    MarketInsightMySqlAdapter,
    MemorySourceAdapter,
    SourceSchemaError,
    configured_symbols_fingerprint,
)
from radar_db import create_all, make_engine
from radar_db.revisions import ai_source_version
from radar_db.schema import (
    ai_daily_budget,
    annotation_jobs,
    collector_checkpoints,
    comments,
    comment_product_routes,
    feed_counter_observations,
    feed_mentions,
    feeds,
    ingestion_runs,
    meta_kv,
    mentions,
    users,
)


@pytest.mark.parametrize("ticker", ["03037.US", "03037.SZ", "US.03037"])
def test_market_insight_ticker_mapping_rejects_non_hk_discussion_sections(ticker):
    assert source_module._code_from_ticker(ticker) == ""


def engine(tmp_path):
    value = make_engine("sqlite:///" + (tmp_path / "collection.sqlite").as_posix())
    create_all(value)
    return value


def feed_observation(feed_id, observed_at, *, content="正文", comments_=None,
                     comment_count=None, body_codes=None, raw_json_broken=False,
                     code="3033", source_ticker="03033.HK"):
    comments_ = comments_ or []
    row = {
        "feed_id": feed_id,
        "stock_id": 1,
        "feed_type": 1,
        "posted_at": datetime(2026, 9, 20, 2, 0),
        "author_uid": "author-1",
        "author_name": "作者",
        "feed_title": "标题",
        "content_text": content,
        "like_count": 4,
        "comment_count": len(comments_) if comment_count is None else comment_count,
        "image_count": 0,
        "raw_json": "{broken" if raw_json_broken else json.dumps({
            "common": {"share_count": 1},
            "comment": {"comment_items": comments_, "has_more": (comment_count or 0) > len(comments_)},
            "summary": {"rich_text": [
                {"stock": {"stock_code": code, "market_type_label": "HK"}}
                for code in (body_codes or [])
            ]},
        }),
    }
    return normalize_feed(row, code, observed_at, source_ticker=source_ticker)


def comment(comment_id, text, *, likes=1):
    return {
        "comment_id": str(comment_id),
        "timestamp": "1789873200",
        "author": {"user_id": f"u{comment_id}", "nick_name": "读者"},
        "rich_text_items": [{"type": 0, "text": text}],
        "like": {"liked_num": likes},
    }


def seed_comment_jobs(conn, comment_ids, now):
    conn.execute(insert(annotation_jobs), [
        {
            "target_type": "comment",
            "target_id": comment_id,
            "subject_code": "3033",
            "task": task,
            "input_hash": f"{task}-{comment_id}",
            "status": "claimed" if task == "kol_comment_opinion" else "done",
            "stage": "llm",
            "priority": 0,
            "attempts": 0,
            "claimed_at": now if task == "kol_comment_opinion" else None,
            "lease_until": now + timedelta(minutes=5)
            if task == "kol_comment_opinion"
            else None,
            "created_at": now,
            "updated_at": now,
        }
        for comment_id in comment_ids
        for task in ("comment_product", "kol_comment_opinion")
    ])


def test_sync_is_idempotent_and_advances_only_declared_complete_day(tmp_path):
    target = engine(tmp_path)
    observed = datetime(2026, 9, 21, 3, 0)
    source = MemorySourceAdapter(
        {
            "feeds": [feed_observation(1, observed, comments_=[comment(11, "好")])],
            "users": [UserObservation({
                "user_id": "author-1", "nick_name": "作者", "follower_num": 10,
                "following_num": 2, "ip_region": "香港", "self_description": None,
            }, observed)],
        },
        complete_through=date(2026, 9, 20),
    )
    refresh = FutuRefresh(target, source)

    first = refresh.sync(SyncRequest(
        run_id="airflow-run-1", through_source_run_id="source-all-1", page_size=1
    ))
    assert first.status == "succeeded"
    assert first.complete_through == date(2026, 9, 20)
    assert first.changed_codes == ["3033"]
    with target.connect() as conn:
        assert conn.execute(select(func.count()).select_from(feeds)).scalar_one() == 1
        assert conn.execute(select(func.count()).select_from(comments)).scalar_one() == 1
        assert conn.execute(select(func.count()).select_from(users)).scalar_one() == 1
        assert conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "anchor")).scalar_one() == "2026-09-20"
        revision = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "data_revision")).scalar_one()
        run = conn.execute(select(ingestion_runs)).mappings().one()
        assert run["source_kind"] == "all"

    repeated = refresh.sync(SyncRequest(
        run_id="airflow-retry-1", through_source_run_id="source-all-1", page_size=1
    ))
    assert repeated.status == "noop"
    assert repeated.rows_read == 0
    assert repeated.inserted == 0
    assert repeated.updated == 0
    assert repeated.unchanged == 0
    assert repeated.ignored == 0
    assert repeated.changed_codes == []
    with target.connect() as conn:
        assert conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "data_revision")).scalar_one() == revision


def test_duplicate_feed_observation_cannot_change_its_original_section(tmp_path):
    target = engine(tmp_path)
    first_at = datetime(2026, 9, 20, 3, 0)
    original = feed_observation(
        1,
        first_at,
        content="$03033.HK$ original",
        code="3033",
        source_ticker="03033.HK",
    )
    FutuRefresh(target, MemorySourceAdapter({"feeds": [original]})).sync(
        SyncRequest(run_id="original-section")
    )

    duplicate = feed_observation(
        1,
        first_at + timedelta(hours=1),
        content="$03032.HK$ refreshed",
        code="3032",
        source_ticker="03032.HK",
    )
    FutuRefresh(target, MemorySourceAdapter({"feeds": [duplicate]})).sync(
        SyncRequest(run_id="duplicate-section")
    )
    detail = feed_observation(
        1,
        first_at + timedelta(hours=2),
        content="$03032.HK$ detail",
        code="3032",
        source_ticker="03032.HK",
    )
    FutuRefresh(target, MemorySourceAdapter({"feed_details": [detail]})).sync(
        SyncRequest(run_id="duplicate-detail")
    )

    with target.connect() as conn:
        stored = conn.execute(select(
            feeds.c.code,
            feeds.c.source_ticker,
            feeds.c.content,
        )).one()
        anchors = conn.execute(select(mentions.c.code).where(
            mentions.c.source == "anchor"
        )).scalars().all()
    assert stored == ("3033", "03033.HK", "$03032.HK$ detail")
    assert anchors == ["3033"]


def test_partial_snapshot_never_deletes_previously_seen_comments(tmp_path):
    target = engine(tmp_path)
    first_at = datetime(2026, 9, 20, 3, 0)
    first = MemorySourceAdapter({
        "feeds": [feed_observation(1, first_at, comments_=[comment(11, "甲"), comment(12, "乙")],
                                   comment_count=4)]
    })
    FutuRefresh(target, first).sync(SyncRequest(run_id="r1"))

    second_at = first_at + timedelta(hours=1)
    second = MemorySourceAdapter({
        "feeds": [feed_observation(1, second_at, comments_=[comment(11, "甲")], comment_count=5)]
    })
    FutuRefresh(target, second).sync(SyncRequest(run_id="r2"))

    with target.connect() as conn:
        assert conn.execute(select(func.count()).select_from(comments)).scalar_one() == 2
        row = conn.execute(select(feeds.c.comments_parsed, feeds.c.comment_count,
                                  feeds.c.comment_coverage_status)).one()
    assert row == (2, 5, "partial")


def test_targeted_feed_ids_refetch_only_requested_comment_snapshots(tmp_path):
    """截断帖子可按 feed_id 定点补抓，不扫描或改写同源的其他帖子。"""
    target = engine(tmp_path)
    first_at = datetime(2026, 9, 20, 3, 0)
    FutuRefresh(target, MemorySourceAdapter({
        "feeds": [
            feed_observation(1, first_at, comments_=[comment(11, "甲")], comment_count=2),
            feed_observation(2, first_at, comments_=[comment(21, "丙")], comment_count=2),
        ]
    })).sync(SyncRequest(run_id="initial-partial"))

    repaired_at = first_at + timedelta(hours=1)
    source = MemorySourceAdapter({
        "feeds": [
            feed_observation(
                1, repaired_at,
                comments_=[comment(11, "甲"), comment(12, "乙")],
                comment_count=2,
            ),
            feed_observation(
                2, repaired_at,
                comments_=[comment(21, "丙"), comment(22, "丁")],
                comment_count=2,
            ),
        ]
    })
    result = FutuRefresh(target, source).sync(
        SyncRequest(run_id="repair-one", feed_ids=(1,))
    )

    assert result.rows_read == 1
    with target.connect() as conn:
        state = {
            row.feed_id: (row.comments_parsed, row.comment_coverage_status)
            for row in conn.execute(select(
                feeds.c.feed_id,
                feeds.c.comments_parsed,
                feeds.c.comment_coverage_status,
            ))
        }
        run = conn.execute(select(ingestion_runs).where(
            ingestion_runs.c.run_id == result.run_id
        )).mappings().one()
    assert state == {1: (2, "complete"), 2: (1, "partial")}
    assert run["source_kind"] == "targeted"


def test_targeted_database_reread_preserves_upstream_partial_coverage(tmp_path):
    target = engine(tmp_path)
    observed = datetime(2026, 9, 20, 3, 0)
    source = MemorySourceAdapter({
        "feeds": [feed_observation(
            1, observed, comments_=[comment(11, "甲")], comment_count=2
        )]
    })

    result = FutuRefresh(target, source).sync(SyncRequest(
        run_id="database-reread", feed_ids=(1,)
    ))

    assert result.comment_coverage == "partial"
    with target.connect() as conn:
        assert conn.execute(select(
            feeds.c.comments_parsed,
            feeds.c.comment_coverage_status,
        )).one() == (1, "partial")


def test_partial_subset_does_not_report_a_false_fact_update(tmp_path):
    target = engine(tmp_path)
    first_at = datetime(2026, 9, 20, 3, 0)
    FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(
            1, first_at, comments_=[comment(11, "甲"), comment(12, "乙")],
            comment_count=4,
        )]
    })).sync(SyncRequest(run_id="partial-fuller"))
    with target.connect() as conn:
        revision = conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "data_revision"
        )).scalar_one()

    result = FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(
            1, first_at + timedelta(hours=1), comments_=[comment(11, "甲")],
            comment_count=4,
        )]
    })).sync(SyncRequest(run_id="partial-subset"))

    assert result.updated == 0
    assert result.unchanged == 1
    with target.connect() as conn:
        assert conn.execute(select(feeds.c.comments_parsed)).scalar_one() == 2
        assert conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "data_revision"
        )).scalar_one() == revision


def test_complete_snapshot_removes_absent_comments_and_invalidates_ai(tmp_path):
    target = engine(tmp_path)
    first_at = datetime(2026, 9, 20, 3, 0)
    FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(
            1, first_at, comments_=[comment(11, "甲"), comment(12, "乙")],
            comment_count=2,
        )]
    })).sync(SyncRequest(run_id="complete-two"))
    with target.begin() as conn:
        conn.execute(update(meta_kv).where(meta_kv.c.k.like("synth_dirty_%")).values(v="0"))

    result = FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(
            1, first_at + timedelta(hours=1), comments_=[comment(11, "甲")],
            comment_count=1,
        )]
    })).sync(SyncRequest(run_id="complete-one"))

    assert result.changed_codes == ["3033"]
    with target.connect() as conn:
        assert conn.execute(select(comments.c.comment_id)).scalars().all() == [11]
        assert conn.execute(select(
            feeds.c.comments_parsed, feeds.c.comment_count, feeds.c.comment_coverage_status
        )).one() == (1, 1, "complete")
        assert conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "synth_dirty_3033_d1"
        )).scalar_one() == "1"


def test_inconsistent_comment_count_is_not_an_authoritative_snapshot(tmp_path):
    target = engine(tmp_path)
    first_at = datetime(2026, 9, 20, 3, 0)
    FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(
            1, first_at, comments_=[comment(11, "甲"), comment(12, "乙")],
            comment_count=2,
        )]
    })).sync(SyncRequest(run_id="consistent-comments"))

    inconsistent = feed_observation(
        1, first_at + timedelta(hours=1), comments_=[comment(11, "甲")],
        comment_count=0,
    )
    assert inconsistent.coverage == "retryable_incomplete"
    FutuRefresh(target, MemorySourceAdapter({"feeds": [inconsistent]})).sync(
        SyncRequest(run_id="inconsistent-comments")
    )

    with target.connect() as conn:
        assert conn.execute(select(comments.c.comment_id).order_by(
            comments.c.comment_id
        )).scalars().all() == [11, 12]
        assert conn.execute(select(
            feeds.c.comments_parsed, feeds.c.comment_coverage_status
        )).one() == (2, "retryable_incomplete")


def test_missing_comment_block_is_not_marked_complete():
    row = {
        "feed_id": 1,
        "stock_id": 1,
        "feed_type": 1,
        "posted_at": datetime(2026, 9, 20, 2, 0),
        "like_count": 0,
        "comment_count": 0,
        "image_count": 0,
        "raw_json": json.dumps({"common": {}}),
    }

    observation = normalize_feed(row, "3033", datetime(2026, 9, 20, 3, 0))

    assert observation.coverage == "retryable_incomplete"


def test_embedded_comment_epoch_is_preserved_by_normalization():
    observed = datetime(2026, 9, 20, 3, 0)
    item = comment(11, "正文")
    item["timestamp"] = "1704067200"

    observation = feed_observation(1, observed, comments_=[item])

    assert observation.comments[0]["posted_at"] == datetime(2024, 1, 1)


def test_detail_replaces_authoritative_body_even_when_shorter(tmp_path):
    target = engine(tmp_path)
    started = datetime(2026, 9, 21, 3, 0)
    FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(1, started, content="一段较长的正文")]
    })).sync(SyncRequest(run_id="base"))

    FutuRefresh(target, MemorySourceAdapter({
        "feed_details": [feed_observation(1, started + timedelta(hours=1), content="短")]
    })).sync(SyncRequest(run_id="short"))
    with target.connect() as conn:
        assert conn.execute(select(feeds.c.content)).scalar_one() == "短"

    FutuRefresh(target, MemorySourceAdapter({
        "feed_details": [feed_observation(1, started + timedelta(hours=2), content="这是一段明显更长的正文内容")]
    })).sync(SyncRequest(run_id="long"))
    with target.connect() as conn:
        assert conn.execute(select(feeds.c.content)).scalar_one() == "这是一段明显更长的正文内容"


def test_shorter_detail_replaces_body_and_clears_old_mentions_atomically(tmp_path):
    """有效详情是权威编辑，正文与正文提及必须切到同一版本。"""
    target = engine(tmp_path)
    started = datetime(2026, 9, 21, 3, 0)
    FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(
            1,
            started,
            content="$03033.HK$ 一段较长的原正文",
            body_codes=["03033"],
        )]
    })).sync(SyncRequest(run_id="detail-consistency-base"))

    result = FutuRefresh(target, MemorySourceAdapter({
        "feed_details": [feed_observation(
            1,
            started + timedelta(hours=1),
            content="$07226.hk$ 短",
            body_codes=["07226"],
        )]
    })).sync(SyncRequest(run_id="detail-consistency-short"))

    with target.connect() as conn:
        assert conn.execute(select(feeds.c.content)).scalar_one() == "$07226.hk$ 短"
        body_mentions = conn.execute(
            select(mentions.c.code).where(mentions.c.source == "body")
        ).scalars().all()
        filter_mentions = conn.execute(
            select(feed_mentions.c.raw_ticker)
        ).scalars().all()
    assert result.updated == 1
    assert body_mentions == ["7226"]
    assert filter_mentions == ["07226.hk"]


def test_broken_detail_preserves_prior_body_and_mentions(tmp_path):
    target = engine(tmp_path)
    started = datetime(2026, 9, 21, 3, 0)
    FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(
            1,
            started,
            content="$03033.HK$ 一段较长的原正文",
            body_codes=["03033"],
        )]
    })).sync(SyncRequest(run_id="broken-detail-base"))

    result = FutuRefresh(target, MemorySourceAdapter({
        "feed_details": [feed_observation(
            1,
            started + timedelta(hours=1),
            content="$07226.HK$ 短",
            raw_json_broken=True,
        )]
    })).sync(SyncRequest(run_id="broken-detail-update"))

    with target.connect() as conn:
        assert conn.execute(select(feeds.c.content)).scalar_one() == (
            "$03033.HK$ 一段较长的原正文"
        )
        body_mentions = conn.execute(
            select(mentions.c.code).where(mentions.c.source == "body")
        ).scalars().all()
        filter_mentions = conn.execute(
            select(feed_mentions.c.raw_ticker)
        ).scalars().all()
    assert result.updated == 0
    assert body_mentions == ["3033"]
    assert filter_mentions == ["03033.HK"]


def test_detail_refresh_replaces_body_mentions_with_the_longer_parent_text(tmp_path):
    target = engine(tmp_path)
    started = datetime(2026, 9, 21, 3, 0)
    FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(
            1,
            started,
            content="$03033.HK$ 原文",
            body_codes=["03033"],
        )]
    })).sync(SyncRequest(run_id="body-before-detail"))

    FutuRefresh(target, MemorySourceAdapter({
        "feed_details": [feed_observation(
            1,
            started + timedelta(hours=1),
            content="$07226.HK$ 这是一段明显更长的详情正文",
            body_codes=["07226"],
        )]
    })).sync(SyncRequest(run_id="body-after-detail"))

    with target.connect() as conn:
        stored = conn.execute(
            select(mentions.c.code, mentions.c.source).order_by(
                mentions.c.source, mentions.c.code
            )
        ).all()
    assert stored == [("3033", "anchor"), ("7226", "body")]


def test_detail_refresh_rebuilds_body_mentions_even_when_content_is_unchanged(tmp_path):
    """A parser upgrade can change derived mentions without changing source text."""

    target = engine(tmp_path)
    started = datetime(2026, 9, 21, 3, 0)
    content = "$03033.HK$ 原文"
    FutuRefresh(target, MemorySourceAdapter({
        # Simulate a row imported by the old parser, which discarded self body mentions.
        "feeds": [feed_observation(1, started, content=content, body_codes=[])]
    })).sync(SyncRequest(run_id="old-parser"))
    with target.begin() as conn:
        conn.execute(mentions.delete().where(mentions.c.source == "body"))

    result = FutuRefresh(target, MemorySourceAdapter({
        "feed_details": [feed_observation(
            1,
            started + timedelta(hours=1),
            content=content,
            body_codes=["03033"],
        )]
    })).sync(SyncRequest(run_id="new-parser-same-text"))

    with target.connect() as conn:
        stored = conn.execute(
            select(mentions.c.code, mentions.c.source).order_by(
                mentions.c.source, mentions.c.code
            )
        ).all()
    assert result.updated == 1
    assert stored == [("3033", "anchor"), ("3033", "body")]


def test_main_feed_accepts_author_edit_to_shorter_body_and_invalidates_ai(tmp_path):
    target = engine(tmp_path)
    started = datetime(2026, 9, 21, 3, 0)
    FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(1, started, content="一段较长的原正文")]
    })).sync(SyncRequest(run_id="base-long"))
    with target.begin() as conn:
        conn.execute(update(meta_kv).where(meta_kv.c.k.like("synth_dirty_%")).values(v="0"))

    result = FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(1, started + timedelta(hours=1), content="已编辑")]
    })).sync(SyncRequest(run_id="shorter-author-edit"))

    assert result.changed_codes == ["3033"]
    with target.connect() as conn:
        assert conn.execute(select(feeds.c.content)).scalar_one() == "已编辑"
        assert conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "synth_dirty_3033_d1"
        )).scalar_one() == "1"


def test_online_source_edits_supersede_comment_jobs_before_reextract(tmp_path):
    target = engine(tmp_path)
    started = datetime(2026, 9, 21, 3, 0)
    original_comments = [comment(11, "评论甲"), comment(12, "评论乙")]
    FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(
            1,
            started,
            content="$03033.HK$ 父帖原文",
            comments_=original_comments,
        )]
    })).sync(SyncRequest(run_id="job-invalidation-base"))
    with target.begin() as conn:
        seed_comment_jobs(conn, (11, 12), started)

    FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(
            1,
            started + timedelta(hours=1),
            content="$03033.HK$ 父帖原文",
            comments_=[comment(11, "评论甲已编辑"), comment(12, "评论乙")],
        )]
    })).sync(SyncRequest(run_id="job-invalidation-comment-edit"))
    with target.connect() as conn:
        states = {
            (row.target_id, row.task): (row.status, row.lease_until)
            for row in conn.execute(select(
                annotation_jobs.c.target_id,
                annotation_jobs.c.task,
                annotation_jobs.c.status,
                annotation_jobs.c.lease_until,
            ))
        }
    assert {states[(11, task)][0] for task in ("comment_product", "kol_comment_opinion")} == {
        "superseded"
    }
    assert all(states[(11, task)][1] is None for task in ("comment_product", "kol_comment_opinion"))
    assert {states[(12, task)][0] for task in ("comment_product", "kol_comment_opinion")} == {
        "done",
        "claimed",
    }

    with target.begin() as conn:
        conn.execute(update(annotation_jobs).values(status="done", lease_until=None))
    FutuRefresh(target, MemorySourceAdapter({
        "feed_details": [feed_observation(
            1,
            started + timedelta(hours=2),
            content="$03033.HK$ 父帖已编辑",
        )]
    })).sync(SyncRequest(run_id="job-invalidation-parent-edit"))
    with target.connect() as conn:
        rows = conn.execute(select(
            annotation_jobs.c.status,
            annotation_jobs.c.lease_until,
        )).all()
    assert rows and {row.status for row in rows} == {"superseded"}
    assert all(row.lease_until is None for row in rows)


def test_comment_routes_follow_partial_and_complete_comment_snapshots(tmp_path):
    target = engine(tmp_path)
    started = datetime(2026, 9, 21, 3, 0)
    FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(
            1,
            started,
            content="$03033.HK$ 父帖",
            comments_=[comment(11, "$02800.HK$ 评论"), comment(12, "沿用父帖")],
        )]
    })).sync(SyncRequest(run_id="route-complete-base"))

    def routes():
        with target.connect() as conn:
            return set(conn.execute(select(
                comment_product_routes.c.comment_id,
                comment_product_routes.c.subject_code,
            )).all())

    assert routes() == {(11, "2800"), (11, "3033"), (12, "3033")}

    # has_more=True: comment 12 is absent from this page but must be preserved,
    # together with its already materialized route.
    FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(
            1,
            started + timedelta(hours=1),
            content="$03033.HK$ 父帖",
            comments_=[comment(11, "$02800.HK$ 评论已编辑")],
            comment_count=2,
        )]
    })).sync(SyncRequest(run_id="route-partial"))
    assert routes() == {(11, "2800"), (11, "3033"), (12, "3033")}

    # A complete snapshot is authoritative and removes the missing comment and
    # only that comment's routes.
    FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(
            1,
            started + timedelta(hours=2),
            content="$03033.HK$ 父帖",
            comments_=[comment(11, "$02800.HK$ 评论已编辑")],
            comment_count=1,
        )]
    })).sync(SyncRequest(run_id="route-complete-removal"))
    assert routes() == {(11, "2800"), (11, "3033")}


def test_feed_detail_parent_edit_recomputes_every_child_route(tmp_path):
    target = engine(tmp_path)
    started = datetime(2026, 9, 21, 3, 0)
    FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(
            1,
            started,
            content="$03033.HK$ 父帖",
            comments_=[comment(11, "沿用父帖"), comment(12, "也是")],
        )]
    })).sync(SyncRequest(run_id="detail-route-base"))

    FutuRefresh(target, MemorySourceAdapter({
        "feed_details": [feed_observation(
            1,
            started + timedelta(hours=1),
            content="$02800.HK$ 父帖已编辑",
        )]
    })).sync(SyncRequest(run_id="detail-route-edit"))

    with target.connect() as conn:
        routes = set(conn.execute(select(
            comment_product_routes.c.comment_id,
            comment_product_routes.c.subject_code,
        )).all())
    assert routes == {(11, "2800"), (12, "2800")}


def test_comment_counter_change_refreshes_facts_without_invalidating_ai(tmp_path):
    target = engine(tmp_path)
    started = datetime(2026, 9, 21, 3, 0)
    FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(1, started, comments_=[comment(11, "原文", likes=1)])]
    })).sync(SyncRequest(run_id="base"))
    with target.begin() as conn:
        conn.execute(update(meta_kv).where(meta_kv.c.k.like("synth_dirty_%")).values(v="0"))
        first_revision = conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "data_revision"
        )).scalar_one()
        first_ai_version = ai_source_version(dict(conn.execute(
            select(meta_kv.c.k, meta_kv.c.v)
        ).all()))

    result = FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(
            1, started + timedelta(hours=1), comments_=[comment(11, "原文", likes=2)]
        )]
    })).sync(SyncRequest(run_id="counter-update"))

    assert result.updated == 1
    assert result.changed_codes == []
    with target.connect() as conn:
        assert conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "data_revision")).scalar_one() != first_revision
        assert ai_source_version(dict(conn.execute(
            select(meta_kv.c.k, meta_kv.c.v)
        ).all())) == first_ai_version
        assert set(conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k.like("synth_dirty_%")
        )).scalars()) == {"0"}


def test_removed_body_mention_is_deleted_and_marks_ai_stale(tmp_path):
    target = engine(tmp_path)
    started = datetime(2026, 9, 21, 3, 0)
    FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(1, started, body_codes=["07226"])]
    })).sync(SyncRequest(run_id="with-mention"))
    with target.begin() as conn:
        conn.execute(update(meta_kv).where(meta_kv.c.k.like("synth_dirty_%")).values(v="0"))

    result = FutuRefresh(target, MemorySourceAdapter({
        "feeds": [feed_observation(1, started + timedelta(hours=1), body_codes=[])]
    })).sync(SyncRequest(run_id="without-mention"))

    assert result.changed_codes == ["3033", "7226"]
    with target.connect() as conn:
        rows = conn.execute(select(mentions.c.code, mentions.c.source)).all()
        assert rows == [("3033", "anchor")]
        assert conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "synth_dirty_3033_d1"
        )).scalar_one() == "1"
        assert conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "synth_dirty_7226_d1"
        )).scalar_one() == "1"


def test_checkpoint_recovers_after_page_failure(tmp_path):
    target = engine(tmp_path)
    start = datetime(2026, 9, 21, 3, 0)
    observations = [
        feed_observation(1, start, comment_count=1),
        feed_observation(2, start + timedelta(seconds=1)),
    ]
    failing = MemorySourceAdapter({"feeds": observations}, fail_after_pages={"feeds": 2})

    with pytest.raises(RuntimeError, match="simulated feeds failure"):
        FutuRefresh(target, failing).sync(SyncRequest(run_id="retry-me", page_size=1))
    with target.connect() as conn:
        assert conn.execute(select(func.count()).select_from(feeds)).scalar_one() == 1
        assert conn.execute(select(ingestion_runs.c.status)).scalar_one() == "failed"
        assert conn.execute(select(collector_checkpoints.c.cursor_id).where(
            collector_checkpoints.c.stream == "feeds"
        )).scalar_one() == "1"

    recovered = FutuRefresh(target, MemorySourceAdapter({"feeds": observations})).sync(
        SyncRequest(run_id="retry-me", page_size=1)
    )
    assert recovered.status == "succeeded"
    assert recovered.comment_coverage == "partial"
    with target.connect() as conn:
        assert conn.execute(select(func.count()).select_from(feeds)).scalar_one() == 2
        assert conn.execute(select(ingestion_runs.c.status)).scalar_one() == "succeeded"

    repeated = FutuRefresh(target, MemorySourceAdapter({"feeds": observations})).sync(
        SyncRequest(run_id="retry-me", page_size=1)
    )
    assert repeated.status == "noop"
    assert repeated.comment_coverage == "partial"


def test_failed_uncommitted_page_does_not_double_audit_counts_on_retry(tmp_path):
    target = engine(tmp_path)
    started = datetime(2026, 9, 21, 3, 0)
    first = feed_observation(1, started)
    bad_second = feed_observation(2, started + timedelta(seconds=1))
    bad_second.feed["like_count"] = -1

    with pytest.raises(ValueError, match="negative counter"):
        FutuRefresh(target, MemorySourceAdapter({
            "feeds": [first, bad_second]
        })).sync(SyncRequest(run_id="bad-second-page", page_size=1))

    with target.connect() as conn:
        failed_counts = json.loads(conn.execute(select(
            ingestion_runs.c.counts_json
        )).scalar_one())
    assert failed_counts["rows_read"] == 1

    good_second = feed_observation(2, started + timedelta(seconds=1))
    result = FutuRefresh(target, MemorySourceAdapter({
        "feeds": [first, good_second]
    })).sync(SyncRequest(run_id="bad-second-page", page_size=1))

    assert result.rows_read == 2
    assert result.inserted == 2


def test_backfill_promotes_checkpoint_for_following_incremental_run(tmp_path):
    target = engine(tmp_path)
    started = datetime(2026, 9, 21, 3, 0)
    first = feed_observation(1, started)
    FutuRefresh(target, MemorySourceAdapter({"feeds": [first]})).sync(
        SyncRequest(run_id="reconciliation", mode="backfill")
    )

    second = feed_observation(2, started + timedelta(seconds=1))
    result = FutuRefresh(target, MemorySourceAdapter({"feeds": [first, second]})).sync(
        SyncRequest(run_id="first-incremental")
    )

    assert result.rows_read == 1
    with target.connect() as conn:
        assert conn.execute(select(collector_checkpoints.c.cursor_id).where(
            collector_checkpoints.c.stream == "feeds",
            collector_checkpoints.c.partition_key == "live",
        )).scalar_one() == "2"


def test_older_backfill_never_regresses_an_existing_live_checkpoint(tmp_path):
    target = engine(tmp_path)
    started = datetime(2026, 9, 21, 3, 0)
    at_t1 = feed_observation(1, started)
    at_t2 = feed_observation(2, started + timedelta(seconds=1))
    at_t3 = feed_observation(3, started + timedelta(seconds=2))
    FutuRefresh(target, MemorySourceAdapter({"feeds": [at_t2]})).sync(
        SyncRequest(run_id="live-at-t2")
    )
    FutuRefresh(target, MemorySourceAdapter({"feeds": [at_t1]})).sync(
        SyncRequest(run_id="historical-through-t1", mode="backfill")
    )

    result = FutuRefresh(target, MemorySourceAdapter({
        "feeds": [at_t1, at_t2, at_t3]
    })).sync(SyncRequest(run_id="incremental-after-backfill"))

    assert result.rows_read == 1
    with target.connect() as conn:
        assert conn.execute(select(collector_checkpoints.c.cursor_id).where(
            collector_checkpoints.c.stream == "feeds",
            collector_checkpoints.c.partition_key == "live",
        )).scalar_one() == "3"


def test_late_older_complete_watermark_is_a_monotonic_noop(tmp_path):
    target = engine(tmp_path)
    FutuRefresh(target, MemorySourceAdapter(complete_through=date(2026, 9, 25))).sync(
        SyncRequest(through_source_run_id="all-newer")
    )

    result = FutuRefresh(
        target, MemorySourceAdapter(complete_through=date(2026, 9, 24))
    ).sync(SyncRequest(through_source_run_id="all-older"))

    assert result.status == "noop"
    assert result.complete_through == date(2026, 9, 25)
    with target.connect() as conn:
        assert conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "source_complete_through"
        )).scalar_one() == "2026-09-25"

    repeated = FutuRefresh(
        target, MemorySourceAdapter(complete_through=date(2026, 9, 24))
    ).sync(SyncRequest(through_source_run_id="all-older"))
    assert repeated.status == "noop"
    assert repeated.complete_through == date(2026, 9, 25)


def test_sparse_snapshot_does_not_erase_known_feed_or_comment_fields(tmp_path):
    target = engine(tmp_path)
    started = datetime(2026, 9, 21, 3, 0)
    original = feed_observation(1, started, comments_=[comment(11, "评论正文")])
    original.feed.update(author_name="作者名", title="帖子标题", share_count=7)
    FutuRefresh(target, MemorySourceAdapter({"feeds": [original]})).sync(SyncRequest(run_id="full"))

    sparse = feed_observation(
        1, started + timedelta(hours=1), content=None, comments_=[comment(11, None)]
    )
    sparse.feed.update(author_name=None, title=None, share_count=None)
    FutuRefresh(target, MemorySourceAdapter({"feeds": [sparse]})).sync(SyncRequest(run_id="sparse"))

    with target.connect() as conn:
        feed = conn.execute(select(feeds)).mappings().one()
        saved_comment = conn.execute(select(comments)).mappings().one()
    assert (feed["author_name"], feed["title"], feed["content"], feed["share_count"]) == (
        "作者名", "帖子标题", "正文", 7,
    )
    assert saved_comment["content"] == "评论正文"


def test_sparse_snapshot_preserves_body_cashtag_from_retained_content(tmp_path):
    target = engine(tmp_path)
    started = datetime(2026, 9, 21, 3, 0)
    original = feed_observation(1, started, content="$07226.HK$ 旧正文")
    original.feed["title"] = "旧标题"
    FutuRefresh(target, MemorySourceAdapter({"feeds": [original]})).sync(
        SyncRequest(run_id="full-with-body-cashtag")
    )

    sparse = feed_observation(1, started + timedelta(hours=1), content=None)
    sparse.feed["title"] = None
    FutuRefresh(target, MemorySourceAdapter({"feeds": [sparse]})).sync(
        SyncRequest(run_id="sparse-without-text")
    )

    with target.connect() as conn:
        saved_feed = conn.execute(select(feeds.c.title, feeds.c.content)).one()
        body_mentions = conn.execute(
            select(mentions.c.code).where(mentions.c.source == "body")
        ).scalars().all()
    assert saved_feed == ("旧标题", "$07226.HK$ 旧正文")
    assert body_mentions == ["7226"]


def test_daily_budget_is_shared_and_hard_capped(tmp_path):
    target = engine(tmp_path)
    day = date(2026, 9, 25)
    clock = lambda: datetime(2026, 9, 24, 16, 30)  # 2026-09-25 HKT
    first = DailyBudget(target, "policy-a", 2, now=clock)
    second = DailyBudget(target, "policy-a", 2, now=clock)
    first.reserve()
    second.reserve()
    with pytest.raises(RunStopped, match="budget_exhausted"):
        first.reserve()
    assert first.snapshot() == {
        "budgetDate": "2026-09-25", "requestLimit": 2, "requestsUsed": 2,
        "requestsRemaining": 0, "status": "exhausted",
    }
    with pytest.raises(ValueError, match="policy changed"):
        DailyBudget(target, "policy-b", 2, now=clock)
    with pytest.raises(ValueError, match="between 1 and 500"):
        DailyBudget(target, "policy-a", 501, now=clock)


def test_ai_request_enforces_safety_bounds():
    assert AiRequest(date(2026, 9, 29), mode="daily").ranges == ("d1", "d2")
    assert AiRequest(date(2026, 9, 28), mode="weekly").ranges == (
        "d1", "d2", "d7", "d14", "d30", "mtd",
    )
    with pytest.raises(ValueError, match="hard daily cap"):
        AiRequest(date(2026, 9, 25), max_http_attempts=501)
    with pytest.raises(ValueError, match="batch_size"):
        AiRequest(date(2026, 9, 25), batch_size=6)
    with pytest.raises(ValueError, match="concurrency"):
        AiRequest(date(2026, 9, 25), concurrency=5)
    with pytest.raises(TypeError, match="explicit bool"):
        AiRequest(date(2026, 9, 25), data_governance_approved="true")
    with pytest.raises(ValueError, match="numeric product codes"):
        AiRequest(date(2026, 9, 25), codes=("3037", "bad"))
    with pytest.raises(ValueError, match="unsupported range"):
        AiRequest(date(2026, 9, 25), range_keys=("d7", "d90"))

    staged = AiRequest(
        date(2026, 9, 25),
        mode="weekly",
        codes=("3037", "3037", "3042"),
        range_keys=("d7", "d7", "d14"),
    )
    assert staged.codes == ("3037", "3042")
    assert staged.ranges == ("d7", "d14")


def test_automatic_analysis_fails_closed_before_loading_ai_configuration(tmp_path, monkeypatch):
    from ai import config as ai_config

    monkeypatch.setattr(
        ai_config,
        "load",
        lambda: pytest.fail("AI configuration must not load before governance approval"),
    )

    request = AiRequest(date(2026, 9, 25))
    assert request.data_governance_approved is False
    with pytest.raises(ValueError, match="AI_DATA_GOVERNANCE_APPROVED=true"):
        FutuRefresh(engine(tmp_path), None).analyze(request)


def test_explicit_governance_approval_opens_the_calibration_gate(tmp_path, monkeypatch):
    from ai import config as ai_config
    import jobs

    analysis_stub = type("AnalysisStub", (), {})()
    full_own_stub = type("FullOwnStub", (), {})()
    monkeypatch.setattr(jobs, "analyze", analysis_stub, raising=False)
    monkeypatch.setattr(jobs, "full_own", full_own_stub, raising=False)
    monkeypatch.setitem(sys.modules, "jobs.analyze", analysis_stub)
    monkeypatch.setitem(sys.modules, "jobs.full_own", full_own_stub)

    monkeypatch.setattr(
        ai_config,
        "load",
        lambda: (_ for _ in ()).throw(RuntimeError("calibration boundary reached")),
    )

    with pytest.raises(RuntimeError, match="calibration boundary reached"):
        FutuRefresh(engine(tmp_path), None).analyze(AiRequest(
            date(2026, 9, 25),
            data_governance_approved=True,
        ))


def test_automatic_analysis_checks_human_gold_before_provider_or_budget(tmp_path, monkeypatch):
    from ai import config as ai_config
    from ai import providers
    from jobs import analyze as analysis_job

    cfg = ai_config.load(
        model="gpt-5.6-luna", prompt_version="comment-product-v3",
        schema_version="v2", taxonomy_version="v2", micro_batch_size=5,
    )
    monkeypatch.setattr(ai_config, "load", lambda: cfg)
    monkeypatch.setattr(analysis_job, "check_calibration", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        analysis_job,
        "check_quality",
        lambda _cfg, report: (_ for _ in ()).throw(RuntimeError(f"quality boundary: {report['name']}")),
    )
    monkeypatch.setattr(providers, "build", lambda *_args, **_kwargs: pytest.fail("provider must not be built"))

    with pytest.raises(RuntimeError, match="quality boundary: gold-v3"):
        FutuRefresh(engine(tmp_path), None).analyze(AiRequest(
            date(2026, 9, 25),
            data_governance_approved=True,
            calibration_report={"batchGatePassed": True},
            quality_report={"name": "gold-v3"},
        ))


def test_ai_result_exposes_completed_pending_ranges_and_failure_reason():
    result = AiResult(
        status="blocked",
        attempts_used=12,
        attempts_remaining=488,
        pending_products=3,
        completed_ranges=["d1"],
        pending_ranges=["d2"],
        failure_reason="budget_exhausted",
    )

    assert result.as_dict() == {
        "status": "blocked",
        "attempts_used": 12,
        "attempts_remaining": 488,
        "pending_products": 3,
        "completed_ranges": ["d1"],
        "pending_ranges": ["d2"],
        "failure_reason": "budget_exhausted",
        "detail": {},
    }


def test_analysis_range_state_preserves_partial_progress(tmp_path):
    target = engine(tmp_path)
    with target.begin() as conn:
        conn.execute(insert(meta_kv), [
            {"k": "synth_dirty_3033_d1", "v": "0"},
            {"k": "synth_dirty_3067_d1", "v": "0"},
            {"k": "synth_dirty_3033_d2", "v": "0"},
            {"k": "synth_dirty_3067_d2", "v": "1"},
        ])

    completed, pending = FutuRefresh(target, None)._analysis_range_state(
        ["3033", "3067"], ["d1", "d2", "d7"]
    )

    assert completed == ["d1"]
    assert pending == ["d2", "d7"]


def test_dry_run_simulates_cross_stream_scope_without_publishing(tmp_path):
    target = engine(tmp_path)
    observed = datetime(2026, 9, 21, 3, 0)
    source = MemorySourceAdapter({
        "feeds": [feed_observation(1, observed)],
        "users": [UserObservation({
            "user_id": "author-1",
            "nick_name": "作者",
            "follower_num": 10,
            "following_num": 2,
            "ip_region": "香港",
            "self_description": None,
        }, observed)],
    })

    result = FutuRefresh(target, source).sync(SyncRequest(
        run_id="dry-run", page_size=1, dry_run=True
    ))

    assert result.status == "dry_run"
    assert result.inserted == 2
    assert result.ignored == 0
    with target.connect() as conn:
        assert conn.execute(select(func.count()).select_from(feeds)).scalar_one() == 0
        assert conn.execute(select(func.count()).select_from(users)).scalar_one() == 0
        assert conn.execute(select(func.count()).select_from(ingestion_runs)).scalar_one() == 0
        assert conn.execute(select(func.count()).select_from(
            collector_checkpoints
        )).scalar_one() == 0


def test_daily_budget_uses_actual_hkt_day_and_rolls_over_at_midnight(tmp_path):
    target = engine(tmp_path)
    current = [datetime(2026, 9, 24, 15, 59)]  # Sep 24 23:59 HKT
    budget = DailyBudget(target, "policy", 500, now=lambda: current[0])
    budget.reserve()
    current[0] = datetime(2026, 9, 24, 16, 1)  # Sep 25 00:01 HKT
    budget.reserve()

    with target.connect() as conn:
        rows = conn.execute(select(
            ai_daily_budget.c.budget_date, ai_daily_budget.c.requests_used
        ).order_by(ai_daily_budget.c.budget_date)).all()
    assert rows == [(date(2026, 9, 24), 1), (date(2026, 9, 25), 1)]
    assert budget.snapshot()["budgetDate"] == "2026-09-25"


def test_daily_budget_reopens_a_fresh_snapshot_after_losing_insert_race():
    day = date(2026, 9, 25)

    class Result:
        def __init__(self, row):
            self.row = row

        def mappings(self):
            return self

        def first(self):
            return self.row

    class Connection:
        def __init__(self, owner, snapshot, can_insert=False):
            self.owner = owner
            self.snapshot = snapshot
            self.can_insert = can_insert

        def execute(self, statement):
            if statement.is_select:
                return Result(self.snapshot)
            if statement.is_insert and self.can_insert:
                # Model a concurrent transaction committing the winning row
                # while this transaction waits on the primary-key insert.
                self.owner.committed = {
                    "budget_date": day, "policy_hash": "policy", "request_limit": 500,
                    "requests_used": 0, "status": "open",
                }
                raise IntegrityError("INSERT", {}, RuntimeError("duplicate key"))
            raise AssertionError(f"Unexpected statement: {statement}")

    class SnapshotEngine:
        def __init__(self):
            self.committed = None
            self.connect_count = 0

        @contextmanager
        def connect(self):
            self.connect_count += 1
            yield Connection(self, self.committed)

        @contextmanager
        def begin(self):
            yield Connection(self, self.committed, can_insert=True)

    target = SnapshotEngine()
    DailyBudget(target, "policy", 500, now=lambda: datetime(2026, 9, 24, 16, 30))

    assert target.connect_count == 2


def test_identical_counter_observations_are_append_only_and_settlement_changes_only_in_repair(
    tmp_path,
):
    target = engine(tmp_path)
    posted = datetime(2026, 9, 20, 2, 0)
    observations = [
        feed_observation(1, posted + timedelta(hours=23), comment_count=7),
        feed_observation(1, posted + timedelta(hours=25), comment_count=7),
        feed_observation(1, posted + timedelta(hours=30), comment_count=7),
        feed_observation(1, posted + timedelta(hours=36), comment_count=7),
    ]

    # A source observation is evidence in its own right. The second snapshot
    # must be retained even though every platform counter is unchanged.
    for index, observation in enumerate(observations[:2], 1):
        FutuRefresh(target, MemorySourceAdapter({"feeds": [observation]})).sync(
            SyncRequest(run_id=f"counter-{index}")
        )

    with target.connect() as conn:
        rows = conn.execute(select(
            feed_counter_observations.c.observed_at,
            feed_counter_observations.c.like_count,
            feed_counter_observations.c.comment_count,
            feed_counter_observations.c.image_count,
            feed_counter_observations.c.share_count,
            feed_counter_observations.c.browse_count,
            feed_counter_observations.c.is_settled,
        ).order_by(feed_counter_observations.c.observed_at)).all()
    assert len(rows) == 2
    assert [row.observed_at for row in rows] == [
        posted + timedelta(hours=23),
        posted + timedelta(hours=25),
    ]
    assert len({(
        row.like_count,
        row.comment_count,
        row.image_count,
        row.share_count,
        row.browse_count,
    ) for row in rows}) == 1
    assert [row.observed_at for row in rows if row.is_settled] == [posted + timedelta(hours=25)]

    # A later ordinary observation remains append-only and cannot replace the
    # first eligible settled observation.
    FutuRefresh(target, MemorySourceAdapter({"feeds": [observations[2]]})).sync(
        SyncRequest(run_id="counter-3")
    )
    with target.connect() as conn:
        assert conn.execute(select(feeds.c.comment_count)).scalar_one() == 7
        rows = conn.execute(select(
            feed_counter_observations.c.observed_at,
            feed_counter_observations.c.is_settled,
        ).order_by(feed_counter_observations.c.observed_at)).all()
    assert len(rows) == 3
    assert [row.observed_at for row in rows if row.is_settled] == [
        posted + timedelta(hours=25)
    ]

    # Simulate a bad historical settlement. A subsequent ordinary sync must
    # preserve it; only explicit repair may reselect from observation history.
    with target.begin() as conn:
        conn.execute(update(feed_counter_observations).where(
            feed_counter_observations.c.feed_id == 1
        ).values(is_settled=False))
        conn.execute(update(feed_counter_observations).where(
            feed_counter_observations.c.feed_id == 1,
            feed_counter_observations.c.observed_at == posted + timedelta(hours=30),
        ).values(is_settled=True))
    FutuRefresh(target, MemorySourceAdapter({"feeds": [observations[3]]})).sync(
        SyncRequest(run_id="counter-4")
    )
    with target.connect() as conn:
        rows = conn.execute(select(
            feed_counter_observations.c.observed_at,
            feed_counter_observations.c.is_settled,
        ).order_by(feed_counter_observations.c.observed_at)).all()
    assert len(rows) == 4
    assert [row.observed_at for row in rows if row.is_settled] == [
        posted + timedelta(hours=30)
    ]

    FutuRefresh(target, MemorySourceAdapter({"feeds": observations})).sync(
        SyncRequest(run_id="counter-repair", mode="repair")
    )
    with target.connect() as conn:
        assert conn.execute(select(feeds.c.comment_count)).scalar_one() == 7
        settled_at = conn.execute(select(feed_counter_observations.c.observed_at).where(
            feed_counter_observations.c.is_settled.is_(True)
        )).scalar_one()
    assert settled_at == posted + timedelta(hours=25)


def test_run_control_counts_persistent_reservations_including_retries(tmp_path):
    target = engine(tmp_path)
    budget = DailyBudget(
        target, "policy", 2, now=lambda: datetime(2026, 9, 24, 16, 30)
    )
    control = RunControl(10)
    control.reserve_hook = budget.reserve
    control.reserve()
    control.reserve(retry=True)
    with pytest.raises(RunStopped, match="budget_exhausted"):
        control.reserve()
    assert control.attempts == 2
    assert control.retries == 1
    assert control.reason == "budget_exhausted"


def receipt_source_engine(tmp_path, run, *, with_completion_proof=True, ticker="03033.HK"):
    source_engine = make_engine(
        "sqlite:///" + (tmp_path / "source-receipts.sqlite").as_posix()
    )
    metadata = MetaData()
    stocks = Table(
        "futu_comments_stocks", metadata,
        Column("stock_id", Integer, primary_key=True),
        Column("ticker", String(20), nullable=False),
    )
    Table(
        "futu_comments_feeds", metadata,
        Column("feed_id", Integer, primary_key=True),
        Column("stock_id", Integer, nullable=False),
        Column("feed_type", Integer, nullable=False),
        Column("posted_at", DateTime, nullable=False),
        Column("author_uid", String(40)),
        Column("feed_title", Text),
        Column("content_text", Text),
        Column("like_count", Integer, nullable=False),
        Column("comment_count", Integer, nullable=False),
        Column("image_count", Integer, nullable=False),
        Column("raw_json", Text, nullable=False),
        Column("scraped_at", DateTime, nullable=False),
        Column("detail_updated_at", DateTime),
    )
    Table(
        "futu_comments_users", metadata,
        Column("user_id", String(40), primary_key=True),
        Column("nick_name", String(200)),
        Column("follower_num", Integer),
        Column("following_num", Integer),
        Column("ip_region", String(80)),
        Column("self_description", Text),
        Column("scraped_at", DateTime, nullable=False),
    )
    run_columns = [
        Column("run_id", String(250), primary_key=True),
        Column("collection_kind", String(40), nullable=False),
        Column("status", String(20), nullable=False),
        Column("scheduled_for", DateTime),
        Column("finished_at", DateTime),
        Column("high_watermark_at", DateTime),
        Column("complete_through", Date),
    ]
    if with_completion_proof:
        run_columns.extend([
            Column("configured_symbol_count", Integer),
            Column("configured_symbols_fingerprint", String(128)),
            Column("attempted_symbol_count", Integer),
            Column("succeeded_symbol_count", Integer),
        ])
    source_runs = Table("futu_comments_collection_runs", metadata, *run_columns)
    metadata.create_all(source_engine)
    with source_engine.begin() as conn:
        conn.execute(insert(stocks).values(stock_id=1, ticker=ticker))
        conn.execute(insert(source_runs).values(**run))
    return source_engine


def full_run_receipt(*, pool_codes=("3033",), **overrides):
    pool_codes = set(pool_codes)
    values = {
        "run_id": "all-1",
        "collection_kind": "comments_all",
        "status": "succeeded",
        "scheduled_for": datetime(2026, 9, 24, 11, 30),
        "finished_at": datetime(2026, 9, 24, 11, 40),
        "high_watermark_at": datetime(2026, 9, 24, 11, 39),
        "complete_through": date(2026, 9, 24),
        "configured_symbol_count": len(pool_codes),
        "configured_symbols_fingerprint": configured_symbols_fingerprint(pool_codes),
        "attempted_symbol_count": len(pool_codes),
        "succeeded_symbol_count": len(pool_codes),
    }
    values.update(overrides)
    return values


def test_market_insight_adapter_rejects_numeric_symbol_from_another_market(tmp_path):
    source_engine = receipt_source_engine(
        tmp_path,
        full_run_receipt(),
        ticker="03033.US",
    )

    with pytest.raises(SourceSchemaError, match="missing from source"):
        MarketInsightMySqlAdapter(source_engine, {"3033"})


def test_configured_symbols_fingerprint_has_a_stable_cross_repository_format():
    assert configured_symbols_fingerprint(["3408", "3033", "3033"]) == (
        "sha256:2899b01b85382df59ab72bcec475d5f5482126f397a303ccabce1f675a069056"
    )


@pytest.mark.parametrize(
    "proof_override",
    [
        {"configured_symbol_count": 0, "attempted_symbol_count": 0,
         "succeeded_symbol_count": 0},
        {"configured_symbols_fingerprint": None},
        {"configured_symbols_fingerprint": "   "},
    ],
    ids=("zero-configured-symbols", "missing-fingerprint", "blank-fingerprint"),
)
def test_completion_claim_requires_complete_configuration_proof(tmp_path, proof_override):
    source_engine = receipt_source_engine(
        tmp_path, full_run_receipt(**proof_override)
    )
    adapter = MarketInsightMySqlAdapter(source_engine, {"3033"})

    assert adapter.complete_through("all-1") is None
    assert adapter.closing_run(date(2026, 9, 24)) is None


@pytest.mark.parametrize(
    "proof_override",
    [
        {"attempted_symbol_count": 119},
        {"succeeded_symbol_count": 119},
    ],
    ids=("attempted-mismatch", "succeeded-mismatch"),
)
def test_completion_claim_requires_all_configured_symbols_to_succeed(
    tmp_path, proof_override
):
    source_engine = receipt_source_engine(
        tmp_path, full_run_receipt(**proof_override)
    )
    adapter = MarketInsightMySqlAdapter(source_engine, {"3033"})

    assert adapter.complete_through("all-1") is None
    assert adapter.closing_run(date(2026, 9, 24)) is None


def test_completion_claim_accepts_a_valid_symbol_proof(tmp_path):
    source_engine = receipt_source_engine(tmp_path, full_run_receipt())
    adapter = MarketInsightMySqlAdapter(source_engine, {"3033"})

    assert adapter.complete_through("all-1") == date(2026, 9, 24)
    assert adapter.closing_run(date(2026, 9, 24))["run_id"] == "all-1"


@pytest.mark.parametrize(
    "receipt",
    [
        full_run_receipt(pool_codes=("3033", "3408")),
        full_run_receipt(
            configured_symbols_fingerprint=configured_symbols_fingerprint({"3408"})
        ),
    ],
    ids=("different-count", "different-fingerprint"),
)
def test_completion_claim_rejects_receipt_for_a_different_symbol_pool(
    tmp_path, receipt
):
    source_engine = receipt_source_engine(tmp_path, receipt)
    adapter = MarketInsightMySqlAdapter(source_engine, {"3033"})

    assert adapter.complete_through("all-1") is None
    assert adapter.closing_run(date(2026, 9, 24)) is None


def test_legacy_completion_claim_without_proof_schema_fails_closed(tmp_path):
    legacy_run = full_run_receipt()
    for column in (
        "configured_symbol_count",
        "configured_symbols_fingerprint",
        "attempted_symbol_count",
        "succeeded_symbol_count",
    ):
        legacy_run.pop(column)
    source_engine = receipt_source_engine(
        tmp_path, legacy_run, with_completion_proof=False
    )
    adapter = MarketInsightMySqlAdapter(source_engine, {"3033"})

    with pytest.raises(SourceSchemaError, match="unverifiable completion claim"):
        adapter.complete_through("all-1")
    with pytest.raises(SourceSchemaError, match="unverifiable completion claim"):
        adapter.closing_run(date(2026, 9, 24))


def test_partial_source_run_can_sync_without_completion_proof_schema(tmp_path):
    partial_run = full_run_receipt(
        run_id="important-1",
        collection_kind="comments_important",
        scheduled_for=datetime(2026, 9, 24, 8, 0),
        complete_through=None,
    )
    for column in (
        "configured_symbol_count",
        "configured_symbols_fingerprint",
        "attempted_symbol_count",
        "succeeded_symbol_count",
    ):
        partial_run.pop(column)
    source_engine = receipt_source_engine(
        tmp_path, partial_run, with_completion_proof=False
    )
    adapter = MarketInsightMySqlAdapter(source_engine, {"3033"})

    result = FutuRefresh(engine(tmp_path), adapter).sync(
        SyncRequest(through_source_run_id="important-1")
    )

    assert result.complete_through is None
    assert adapter.complete_through("important-1") is None


def test_market_insight_adapter_uses_stable_keyset_and_all_run_watermark(tmp_path, monkeypatch):
    source_engine = make_engine("sqlite:///" + (tmp_path / "source.sqlite").as_posix())
    metadata = MetaData()
    stocks = Table(
        "futu_comments_stocks", metadata,
        Column("stock_id", Integer, primary_key=True), Column("ticker", String(20), nullable=False),
    )
    source_feeds = Table(
        "futu_comments_feeds", metadata,
        Column("feed_id", Integer, primary_key=True), Column("stock_id", Integer, nullable=False),
        Column("feed_type", Integer, nullable=False), Column("posted_at", DateTime, nullable=False),
        Column("author_uid", String(40)), Column("author_name", String(200)),
        Column("feed_title", Text), Column("content_text", Text),
        Column("like_count", Integer, nullable=False), Column("comment_count", Integer, nullable=False),
        Column("image_count", Integer, nullable=False), Column("raw_json", Text, nullable=False),
        Column("scraped_at", DateTime, nullable=False), Column("detail_updated_at", DateTime),
    )
    source_users = Table(
        "futu_comments_users", metadata,
        Column("user_id", String(40), primary_key=True), Column("nick_name", String(200)),
        Column("follower_num", Integer), Column("following_num", Integer), Column("ip_region", String(80)),
        Column("self_description", Text), Column("scraped_at", DateTime, nullable=False),
    )
    source_runs = Table(
        "futu_comments_collection_runs", metadata,
        Column("run_id", String(250), primary_key=True), Column("collection_kind", String(40), nullable=False),
        Column("status", String(20), nullable=False), Column("scheduled_for", DateTime),
        Column("finished_at", DateTime), Column("high_watermark_at", DateTime),
        Column("complete_through", Date),
        Column("configured_symbol_count", Integer),
        Column("configured_symbols_fingerprint", String(128)),
        Column("attempted_symbol_count", Integer),
        Column("succeeded_symbol_count", Integer),
    )
    metadata.create_all(source_engine)
    observed = datetime(2026, 9, 25, 1, 0)
    with source_engine.begin() as conn:
        conn.execute(insert(stocks), [
            {"stock_id": 1, "ticker": "03033.HK"},
            {"stock_id": 2, "ticker": "09999.HK"},
        ])
        base = {
            "feed_type": 1, "posted_at": datetime(2026, 9, 24, 2), "author_uid": "u1",
            "author_name": "作者", "feed_title": None, "content_text": "正文", "like_count": 1,
            "comment_count": 0, "image_count": 0, "raw_json": "{}", "scraped_at": observed,
            "detail_updated_at": None,
        }
        conn.execute(insert(source_feeds), [
            {**base, "feed_id": 10, "stock_id": 1},
            {**base, "feed_id": 11, "stock_id": 2, "author_uid": "u2"},
            {**base, "feed_id": 12, "stock_id": 1,
             "scraped_at": observed + timedelta(seconds=1)},
        ])
        conn.execute(insert(source_users), [
            {"user_id": "u1", "nick_name": "作者", "scraped_at": observed},
            {"user_id": "u2", "nick_name": "范围外作者", "scraped_at": observed},
        ])
        conn.execute(insert(source_runs).values(
            run_id="all-1", collection_kind="comments_all", status="succeeded",
            scheduled_for=datetime(2026, 9, 24, 11, 30), finished_at=observed,
            high_watermark_at=observed,
            complete_through=date(2026, 9, 24),
            configured_symbol_count=1,
            configured_symbols_fingerprint=configured_symbols_fingerprint({"3033"}),
            attempted_symbol_count=1,
            succeeded_symbol_count=1,
        ))

    adapter = MarketInsightMySqlAdapter(source_engine, {"3033"})
    high = adapter.high_watermark("feeds", "all-1")
    assert high == Cursor(observed, 11)
    assert adapter.high_watermark("feeds") == Cursor(observed + timedelta(seconds=1), 12)
    page = adapter.pull_page("feeds", None, high, 100)
    assert [item.feed["feed_id"] for item in page.items] == [10]
    assert page.ignored == 1
    assert page.exhausted is True
    user_page = adapter.pull_page("users", None, adapter.high_watermark("users"), 100)
    assert [item.user["user_id"] for item in user_page.items] == ["u1", "u2"]
    assert adapter.complete_through() == date(2026, 9, 24)
    assert adapter.complete_through("not-an-all-run") is None
    assert adapter.closing_run(date(2026, 9, 24))["run_id"] == "all-1"

    target = engine(tmp_path)
    result = FutuRefresh(target, adapter).sync(SyncRequest(
        through_source_run_id="all-1"
    ))
    with target.connect() as conn:
        assert conn.execute(select(users.c.user_id)).scalars().all() == ["u1"]
    # One out-of-pool feed and its otherwise unrelated author were both read
    # and explicitly accounted for as ignored.
    assert result.ignored == 2

    real_table = source_module.Table

    def omit_collection_run_table(name, *args, **kwargs):
        if name == "futu_comments_collection_runs":
            raise source_module.NoSuchTableError(name)
        return real_table(name, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(source_module, "Table", omit_collection_run_table)
        assert MarketInsightMySqlAdapter(source_engine, {"3033"}).collection_runs is None

    def fail_collection_run_reflection(name, *args, **kwargs):
        if name == "futu_comments_collection_runs":
            raise RuntimeError("permission denied")
        return real_table(name, *args, **kwargs)

    monkeypatch.setattr(source_module, "Table", fail_collection_run_reflection)
    with pytest.raises(SourceSchemaError, match="collection-run table: permission denied"):
        MarketInsightMySqlAdapter(source_engine, {"3033"})


def test_ai_readiness_requires_the_exact_closing_all_run_to_be_synced(tmp_path):
    target = engine(tmp_path)
    day = date(2026, 9, 24)
    source = MemorySourceAdapter(
        complete_through=day,
        closing_runs={day: {"run_id": "futu_all_comments_scrape:scheduled__2026-09-24"}},
    )
    refresh = FutuRefresh(target, source)

    with pytest.raises(RuntimeError, match="Timed out"):
        refresh._wait_for_ready(day, 0)

    refresh.sync(SyncRequest(
        run_id="radar-dataset-run", through_source_run_id="futu_all_comments_scrape:scheduled__2026-09-24"
    ))
    assert refresh._wait_for_ready(day, 0)["run_id"].startswith("futu_all_comments_scrape:")
