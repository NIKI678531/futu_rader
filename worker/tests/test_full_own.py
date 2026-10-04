import json
from datetime import date, datetime

import pytest
from sqlalchemy import insert, select, update

from ai import config
from jobs.full_own import prepare, prioritize_scope, queue_status


def test_full_own_cli_fails_closed_without_both_release_reports(monkeypatch):
    from ai import config
    from jobs import full_own

    load_config = config.load
    monkeypatch.setattr(full_own.config, "load", lambda: load_config(
        model="gate-model", prompt_version="comment-product-v3",
        schema_version="v2", taxonomy_version="v2", micro_batch_size=5,
    ))
    monkeypatch.setattr(full_own, "make_engine", lambda: pytest.fail("gate must run before database access"))

    with pytest.raises(SystemExit) as exc:
        full_own.main(["--max-http-requests", "1"])
    assert exc.value.code == 2
from radar_db import create_all, make_engine
from radar_db.comment_filter import filter_readiness_values, load_comment_filter_config
from radar_db.leases import WorkerLease
from radar_db.schema import (
    analysis_scopes, annotation_jobs, comments, feed_mentions, feeds, mentions, meta_kv,
)


def _mark_filter_ready(engine):
    with engine.begin() as conn:
        conn.execute(insert(meta_kv), [
            {"k": key, "v": value}
            for key, value in filter_readiness_values(load_comment_filter_config()).items()
        ])


def test_prepare_reuses_scopes_and_leases_exclude_other_workers(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "db.sqlite").as_posix())
    create_all(engine)
    _mark_filter_ready(engine)
    with engine.begin() as conn:
        conn.execute(insert(meta_kv).values(k="anchor", v="2026-08-25"))
    cfg = config.load(model="test", prompt_version="comment-product-v2", schema_version="v2", taxonomy_version="v2")
    scopes, start = prepare(engine, cfg, date(2026, 8, 25), ["3033"])
    repeated, _ = prepare(engine, cfg, date(2026, 8, 25), ["3033"])
    assert scopes == repeated
    assert start == date(2026, 6, 27)
    assert queue_status(engine, scopes["3033"]) == {}
    from radar_db.revisions import bump_revision
    with engine.begin() as conn:
        bump_revision(conn, "data")
    changed, _ = prepare(engine, cfg, date(2026, 8, 25), ["3033"])
    assert changed != scopes
    with WorkerLease(engine, "test"):
        with pytest.raises(RuntimeError, match="already holds"):
            with WorkerLease(engine, "test"):
                pytest.fail("Second worker acquired the lease")


def test_prepare_replaces_a_legacy_scope_without_exact_rule_provenance(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "legacy.db").as_posix())
    create_all(engine)
    _mark_filter_ready(engine)
    with engine.begin() as conn:
        conn.execute(insert(meta_kv).values(k="anchor", v="2026-08-25"))
    cfg = config.load(
        model="test",
        prompt_version="comment-product-v3",
        schema_version="v2",
        taxonomy_version="v2",
    )
    first, _ = prepare(engine, cfg, date(2026, 8, 25), ["3033"], ranges=["d7"])
    with engine.begin() as conn:
        row = conn.execute(
            select(analysis_scopes.c.stats_json).where(
                analysis_scopes.c.scope_id == first["3033"]
            )
        ).scalar_one()
        stale = json.loads(row)
        stale.pop("exactRuleVersion")
        conn.execute(
            update(analysis_scopes)
            .where(analysis_scopes.c.scope_id == first["3033"])
            .values(stats_json=json.dumps(stale))
        )

    replacement, _ = prepare(
        engine, cfg, date(2026, 8, 25), ["3033"], ranges=["d7"]
    )

    assert replacement["3033"] != first["3033"]


def test_existing_pending_jobs_are_prioritized_without_requeuing_done(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "priority.db").as_posix())
    create_all(engine)
    with engine.begin() as conn:
        for feed_id, posted_at in ((1, datetime(2026, 7, 1)), (2, datetime(2026, 8, 25))):
            conn.execute(insert(feeds).values(feed_id=feed_id, code="3033", posted_at=posted_at,
                                             feed_type=1, like_count=0, comment_count=1, image_count=0,
                                             raw_json_broken=False))
            conn.execute(insert(comments).values(comment_id=feed_id, feed_id=feed_id, content="ETF"))
        for job_id, status in ((1, "pending"), (2, "pending"), (3, "done")):
            conn.execute(insert(annotation_jobs).values(job_id=job_id, target_type="comment",
                         target_id=1 if job_id == 1 else 2, subject_code="3033", task="comment_product",
                         input_hash=str(job_id), status=status, priority=0, attempts=0, scope_id="scope-test",
                         created_at=datetime(2026, 9, 1), updated_at=datetime(2026, 9, 1)))
    assert prioritize_scope(engine, "scope-test", date(2026, 8, 25), own=True) == 2
    assert prioritize_scope(engine, "scope-test", date(2026, 8, 25), own=True) == 0
    with engine.connect() as conn:
        priorities = dict(conn.execute(select(annotation_jobs.c.job_id, annotation_jobs.c.priority)).all())
    assert priorities == {1: 3, 2: 33, 3: 0}


def test_selected_ranges_only_queue_their_current_and_baseline_windows(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "recent.db").as_posix())
    create_all(engine)
    _mark_filter_ready(engine)
    with engine.begin() as conn:
        conn.execute(insert(meta_kv).values(k="anchor", v="2026-08-25"))
        for feed_id, posted_at in ((1, datetime(2026, 7, 1)), (2, datetime(2026, 8, 12)),
                                   (3, datetime(2026, 8, 25))):
            conn.execute(insert(feeds).values(feed_id=feed_id, code="3033",
                         source_ticker="03033.HK", posted_at=posted_at,
                         feed_type=1, like_count=0, comment_count=1, image_count=0,
                         raw_json_broken=False))
            conn.execute(insert(comments).values(comment_id=feed_id, feed_id=feed_id,
                         content="ETF fee too high", author_uid=f"reader-{feed_id}"))
            conn.execute(insert(mentions), [
                {"feed_id": feed_id, "code": "3033", "source": source, "in_pool": True}
                for source in ("anchor", "body")
            ])
            conn.execute(insert(feed_mentions).values(
                feed_id=feed_id, raw_ticker="03033.HK", market="HK", occurrences=1
            ))
    cfg = config.load(model="test", prompt_version="comment-product-v2", schema_version="v2", taxonomy_version="v2")
    scopes, start = prepare(engine, cfg, date(2026, 8, 25), ["3033"], ranges=["d1", "d2", "d7"])
    assert start == date(2026, 8, 12)
    assert queue_status(engine, scopes["3033"]) == {"pending": 2}
    with engine.connect() as conn:
        targets = set(conn.execute(select(annotation_jobs.c.target_id)).scalars())
    assert targets == {2, 3}


def test_optimized_prepare_matches_candidates_and_reuses_scopes(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "paged.db").as_posix())
    create_all(engine)
    _mark_filter_ready(engine)
    with engine.begin() as conn:
        conn.execute(insert(meta_kv).values(k="anchor", v="2026-08-25"))
        for feed_id, code in ((1, "3033"), (2, "2802")):
            ticker = f"{code.zfill(5)}.HK"
            conn.execute(insert(feeds).values(feed_id=feed_id, code=code,
                         source_ticker=ticker, posted_at=datetime(2026, 8, 25),
                         feed_type=1, like_count=0, comment_count=1, image_count=0,
                         raw_json_broken=False))
            conn.execute(insert(comments).values(comment_id=feed_id, feed_id=feed_id,
                         content="ETF fee too high", author_uid=f"reader-{feed_id}"))
            conn.execute(insert(mentions), [
                {"feed_id": feed_id, "code": code, "source": source, "in_pool": True}
                for source in ("anchor", "body")
            ])
            conn.execute(insert(feed_mentions).values(
                feed_id=feed_id, raw_ticker=ticker, market="HK", occurrences=1
            ))
    cfg = config.load(model="test", prompt_version="comment-product-v2", schema_version="v2", taxonomy_version="v2")
    scopes, start = prepare(engine, cfg, date(2026, 8, 25), ["3033", "2802"],
                            ranges=["d1", "d2"], optimized=True, page_size=1)
    assert start == date(2026, 8, 22)
    assert all(queue_status(engine, scope) == {"pending": 1} for scope in scopes.values())
    assert prepare(engine, cfg, date(2026, 8, 25), ["3033", "2802"],
                   ranges=["d1", "d2"], optimized=True)[0] == scopes
