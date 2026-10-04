"""Strict content-tag routing shared by ingestion, extraction, and reads."""

from datetime import datetime

import json

from sqlalchemy import create_engine, insert, select

from jobs import backfill_comment_routes
from jobs import extract
from ai import config
from radar_db.annotations_read import current_annotations
from radar_db.comment_routes import (
    COMMENT_ROUTE_VERSION,
    is_ready,
    readiness_values,
    replace_comment_routes,
    route_exists_predicate,
    route_matches,
    route_rows,
)
from radar_db.schema import (
    annotation_jobs,
    annotations,
    comment_product_routes,
    comments,
    feeds,
    meta_kv,
    metadata,
)


def test_parent_and_comment_tags_union_and_ignore_anchor_and_offpool():
    matches = route_matches(
        "$南方恒生科技 (03033.HK)$ 与 $07226.HK$",
        "重复 $03033.hk$，普通产品名 2800",
        "我更喜欢 $02800.HK$，也提到 $99999.HK$",
        pool_codes={"3033", "7226", "2800"},
    )

    assert matches == {
        "2800": {"matched_parent": False, "matched_comment": True},
        "3033": {"matched_parent": True, "matched_comment": False},
        "7226": {"matched_parent": True, "matched_comment": False},
    }


def test_no_tag_means_no_route_even_when_feed_is_from_an_etf_section():
    assert route_matches("大市讨论", "指数今天上涨", "同意", pool_codes={"3033"}) == {}


def test_replace_routes_supports_add_update_and_empty_removal():
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine, tables=[comment_product_routes])
    now = datetime(2026, 9, 30, 12, 0)
    first = route_rows(
        10,
        1,
        "$03033.HK$",
        None,
        "$02800.HK$",
        now=now,
        pool_codes={"3033", "2800"},
    )
    with engine.begin() as conn:
        changed = replace_comment_routes(conn, 10, first)
        assert changed["added"] == {"3033", "2800"}
        assert conn.execute(
            select(comment_product_routes.c.subject_code).where(
                route_exists_predicate(10, comment_product_routes.c.subject_code)
            ).order_by(comment_product_routes.c.subject_code)
        ).scalars().all() == ["2800", "3033"]

        changed = replace_comment_routes(conn, 10, [])
        assert changed["removed"] == {"3033", "2800"}
        assert conn.execute(select(comment_product_routes)).all() == []


def test_readiness_binds_rule_and_pool_digest():
    values = readiness_values({"3033", "2800"})
    assert values["comment_route_version"] == COMMENT_ROUTE_VERSION
    assert is_ready(values, {"2800", "3033"})
    assert not is_ready({**values, "comment_route_pool_digest": "stale"}, {"2800", "3033"})


def _full_engine():
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine)
    now = datetime(2026, 9, 30, 12, 0)
    with engine.begin() as conn:
        conn.execute(insert(feeds), [
            {
                "feed_id": 1,
                "code": "3032",
                "posted_at": datetime(2026, 9, 29, 16, 0),  # HKT 9/30 00:00
                "feed_type": 1,
                "title": "$03033.HK$ / $07226.HK$",
                "content": "重复 $03033.hk$",
                "like_count": 0,
                "comment_count": 2,
                "image_count": 0,
                "raw_json_broken": False,
            },
            {
                "feed_id": 2,
                "code": "3033",
                "posted_at": datetime(2026, 9, 30, 16, 0),  # HKT 10/01 00:00
                "feed_type": 1,
                "title": "没有 tag",
                "content": None,
                "like_count": 0,
                "comment_count": 1,
                "image_count": 0,
                "raw_json_broken": False,
            },
        ])
        conn.execute(insert(comments), [
            {"comment_id": 11, "feed_id": 1, "content": "$02800.HK$ 可以关注"},
            {"comment_id": 12, "feed_id": 1, "content": "父帖已经点名"},
            {"comment_id": 13, "feed_id": 2, "content": "没有 tag"},
        ])
        conn.execute(insert(annotation_jobs).values(
            target_type="comment",
            target_id=13,
            subject_code="3033",
            task="comment_product",
            input_hash="old",
            status="done",
            created_at=now,
            updated_at=now,
        ))
        conn.execute(insert(annotations).values(
            target_type="comment",
            target_id=13,
            subject_code="3033",
            kind="relevance",
            value_json=json.dumps("relevant"),
            run_id="old",
            input_hash="old",
            review_state="pending",
            created_at=now,
        ))
    return engine


def test_backfill_dry_run_then_activate_is_resumable_and_hides_stale_conclusions():
    engine = _full_engine()

    preview = backfill_comment_routes.run(engine, batch_size=1, dry_run=True)
    assert preview["processed"] == 3
    assert preview["routes"] == 5
    with engine.connect() as conn:
        assert conn.execute(select(comment_product_routes)).all() == []

    populated = backfill_comment_routes.run(engine, batch_size=1)
    assert populated["status"] == "populated"
    activated = backfill_comment_routes.run(engine, batch_size=1, resume=True, activate_routes=True)
    assert activated["status"] == "active"
    assert activated["processed"] == 0
    with engine.connect() as conn:
        rows = conn.execute(select(
            comment_product_routes.c.comment_id,
            comment_product_routes.c.subject_code,
        ).order_by(
            comment_product_routes.c.comment_id,
            comment_product_routes.c.subject_code,
        )).all()
        assert rows == [
            (11, "2800"), (11, "3033"), (11, "7226"),
            (12, "3033"), (12, "7226"),
        ]
        assert is_ready(dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v)).all()))
        assert conn.execute(select(annotation_jobs.c.status)).scalar_one() == "superseded"

    assert current_annotations(engine, "relevance", "comment") == {}


def test_backfill_resume_requires_matching_checkpoint():
    engine = _full_engine()

    try:
        backfill_comment_routes.run(engine, resume=True)
    except RuntimeError as exc:
        assert "matching route backfill checkpoint" in str(exc)
    else:
        raise AssertionError("resume without a checkpoint must fail closed")


def test_route_candidate_window_uses_parent_hkt_time_and_supports_cross_product():
    from jobs.extract import _comment_candidates_hkt

    engine = _full_engine()
    backfill_comment_routes.run(engine, activate_routes=True)
    rows = list(_comment_candidates_hkt(
        engine,
        codes=["2800", "3033", "7226"],
        since=datetime(2026, 9, 30),
        until=datetime(2026, 10, 1),
        comment_routes=True,
    ))
    assert {(row.comment_id, row.code) for row in rows} == {
        (11, "2800"), (11, "3033"), (11, "7226"),
        (12, "3033"), (12, "7226"),
    }


def test_active_route_extraction_is_idempotent_and_does_not_requeue_done_units():
    engine = _full_engine()
    backfill_comment_routes.run(engine, activate_routes=True)
    cfg = config.load(
        model="m",
        prompt_version="comment-product-v3",
        schema_version="v2",
        taxonomy_version="v2",
    )
    kwargs = dict(
        codes=["2800", "3033", "7226"],
        date_from=datetime(2026, 9, 30),
        date_to=datetime(2026, 9, 30),
        ownership={"2800": "peer", "3033": "own", "7226": "own"},
        report_dir=None,
    )
    first = extract.run(engine, cfg, **kwargs)
    with engine.connect() as conn:
        count_after_first = conn.execute(select(annotation_jobs.c.job_id)).all()
    second = extract.run(engine, cfg, **kwargs)
    with engine.connect() as conn:
        count_after_second = conn.execute(select(annotation_jobs.c.job_id)).all()

    assert first["commentRouteVersion"] == COMMENT_ROUTE_VERSION
    assert second["comments"]["queued_new"] == 0
    assert count_after_second == count_after_first
