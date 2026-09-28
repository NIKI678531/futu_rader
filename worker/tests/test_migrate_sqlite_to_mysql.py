import os
import sys
import json
from datetime import datetime

import pytest
from sqlalchemy import insert, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from jobs import migrate_sqlite_to_mysql as migration_job
from jobs.migrate_sqlite_to_mysql import _expected_alembic_heads, migrate
from radar_db import create_all, make_engine
from radar_db.schema import (
    ai_daily_budget,
    annotation_jobs,
    collector_checkpoints,
    feed_counter_observations,
    feeds,
    ingestion_runs,
    meta_kv,
)


def database(path, *, stamped=False, revision=None):
    engine = make_engine("sqlite:///" + path.as_posix())
    create_all(engine)
    if stamped:
        revision = revision or next(iter(_expected_alembic_heads()))
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
            conn.exec_driver_sql(
                "INSERT INTO alembic_version (version_num) VALUES (?)", (revision,)
            )
    return engine


def test_streaming_migration_preserves_rows_and_releases_claims(tmp_path):
    source = database(tmp_path / "source.sqlite")
    target = database(tmp_path / "target.sqlite", stamped=True)
    now = datetime(2026, 9, 25, 1, 0)
    with source.begin() as conn:
        conn.execute(insert(feeds).values(
            feed_id=1, code="3033", posted_at=now, feed_type=1,
            like_count=1, comment_count=0, image_count=0, raw_json_broken=False,
            comment_coverage_status="unknown",
        ))
        conn.execute(insert(annotation_jobs).values(
            job_id=1, target_type="feed", target_id=1, subject_code="", task="post_annotation",
            input_hash="x", status="claimed", priority=0, attempts=0, created_at=now,
            updated_at=now, claimed_at=now, lease_until=now, stage="llm",
        ))
        conn.execute(insert(ingestion_runs).values(
            run_id="sync-old", source="market_insight", source_run_id="source-old",
            source_kind="partial", status="succeeded", started_at=now, finished_at=now,
        ))
        conn.execute(insert(collector_checkpoints).values(
            source="market_insight", stream="feeds", partition_key="live",
            cursor_at=now, cursor_id="1", updated_at=now, last_run_id="sync-old",
            config_hash="config",
        ))
        conn.execute(insert(ai_daily_budget).values(
            budget_date=now.date(), policy_hash="policy", request_limit=500,
            requests_used=17, status="open", updated_at=now,
        ))
        conn.execute(insert(feed_counter_observations).values(
            feed_id=1, observed_at=now, like_count=1, comment_count=0,
            image_count=0, is_settled=False, source_run_id="source-old",
        ))
        conn.execute(insert(meta_kv), [
            {"k": "anchor", "v": "2026-09-24"},
            {"k": "data_revision", "v": "stable-revision"},
            {"k": "own_analysis_progress", "v": json.dumps({
                "sourceVersion": {"data_revision": "stable-revision"}
            })},
        ])

    report = migrate(source, target, batch_size=1, progress=lambda _message: None)
    assert report["sourceCounts"]["feeds"] == report["targetCounts"]["feeds"] == 1
    with target.connect() as conn:
        job = conn.execute(select(annotation_jobs)).mappings().one()
        assert job["status"] == "pending"
        assert job["claimed_at"] is None and job["lease_until"] is None
        assert conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "anchor")).scalar_one() == "2026-09-24"
        assert conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "data_revision"
        )).scalar_one() == "stable-revision"
        assert conn.execute(select(ingestion_runs.c.run_id)).scalar_one() == "sync-old"
        assert conn.execute(select(collector_checkpoints.c.cursor_id)).scalar_one() == "1"
        assert conn.execute(select(ai_daily_budget.c.requests_used)).scalar_one() == 17
        assert conn.execute(select(feed_counter_observations.c.feed_id)).scalar_one() == 1


def test_migration_rejects_nonempty_target_without_resume(tmp_path):
    source = database(tmp_path / "source.sqlite")
    target = database(tmp_path / "target.sqlite", stamped=True)
    now = datetime(2026, 9, 25, 1, 0)
    row = dict(feed_id=1, code="3033", posted_at=now, feed_type=1,
               like_count=1, comment_count=0, image_count=0, raw_json_broken=False,
               comment_coverage_status="unknown")
    with source.begin() as conn:
        conn.execute(insert(feeds).values(**row))
    with target.begin() as conn:
        conn.execute(insert(feeds).values(**row))
    with pytest.raises(ValueError, match="Target is not empty"):
        migrate(source, target, progress=lambda _message: None)


def test_migration_rejects_target_before_current_alembic_head(tmp_path):
    source = database(tmp_path / "source.sqlite")
    target = database(tmp_path / "target.sqlite", stamped=True, revision="0009")

    with pytest.raises(ValueError, match="current Alembic head"):
        migrate(source, target, progress=lambda _message: None)


def test_migration_checks_target_tables_missing_from_older_source(tmp_path):
    source = database(tmp_path / "source.sqlite")
    ai_daily_budget.drop(source)
    target = database(tmp_path / "target.sqlite", stamped=True)
    now = datetime(2026, 9, 25, 1, 0)
    with target.begin() as conn:
        conn.execute(insert(ai_daily_budget).values(
            budget_date=now.date(), policy_hash="stale", request_limit=500,
            requests_used=1, status="open", updated_at=now,
        ))

    with pytest.raises(ValueError, match="Target is not empty"):
        migrate(source, target, progress=lambda _message: None)
    with pytest.raises(RuntimeError, match="ai_daily_budget"):
        migrate(source, target, resume=True, progress=lambda _message: None)


def test_production_cli_requires_sqlite_source_and_mysql_target(monkeypatch, capsys):
    monkeypatch.setenv("SOURCE_RADAR_DB_URL", "postgresql://source.invalid/radar")
    monkeypatch.setenv("RADAR_TARGET_DB_URL", "mysql+pymysql://target.invalid/radar")
    monkeypatch.setattr(
        migration_job, "make_engine", lambda _url: pytest.fail("invalid source must fail before connecting")
    )
    with pytest.raises(SystemExit):
        migration_job.main([])
    assert "must point to the SQLite source" in capsys.readouterr().err

    calls = []
    monkeypatch.setenv("SOURCE_RADAR_DB_URL", "sqlite://")
    monkeypatch.setenv("RADAR_TARGET_DB_URL", "postgresql://target.invalid/radar")
    monkeypatch.setattr(migration_job, "make_engine", lambda url: calls.append(url) or object())
    with pytest.raises(SystemExit):
        migration_job.main([])
    assert calls == ["sqlite://"]
    assert "must be MySQL" in capsys.readouterr().err
