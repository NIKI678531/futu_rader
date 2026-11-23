import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from radar_db.schema import meta_kv, metadata  # noqa: E402
from scheduler import (  # noqa: E402
    build_scheduler,
    heartbeat,
    hkex_catalog_refresh,
    recover_analysis_requests,
    recover_prices,
)


def _engine():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    return engine


def test_heartbeat_queue_and_daily_jobs_are_registered(monkeypatch):
    monkeypatch.delenv("AI_DAILY_CRON", raising=False)
    monkeypatch.delenv("AI_INTRADAY_CRON", raising=False)
    monkeypatch.delenv("RADAR_INTRADAY_SYNC_CRON", raising=False)
    jobs = build_scheduler(interval_seconds=30).get_jobs()
    assert [j.id for j in jobs] == [
        "heartbeat", "admin-ai-requests", "startup-price-recovery",
        "hkex-catalog-refresh", "daily-refresh", "intraday-sync", "intraday-refresh",
    ]


def test_hkex_catalog_refresh_delegates_to_the_official_catalog_sync(monkeypatch):
    engine = _engine()
    expected = {"active": 250, "activated": 2, "reactivated": 1, "deactivated": 3}
    monkeypatch.setattr(
        "jobs.hkex_catalog.sync",
        lambda supplied: expected if supplied is engine else None,
    )

    assert hkex_catalog_refresh(engine=engine) == expected


def test_startup_recovery_delegates_to_missing_analysis_check(monkeypatch):
    engine = _engine()
    expected = {"status": "queued", "queued": [{"range": "d7"}]}
    monkeypatch.setattr(
        "jobs.daily.recover_missing_analysis",
        lambda supplied: expected if supplied is engine else None,
    )

    assert recover_analysis_requests(engine=engine) == expected


def test_startup_price_recovery_delegates_to_missing_price_check(monkeypatch):
    engine = _engine()
    expected = {"status": "complete", "missing": []}
    monkeypatch.setattr(
        "jobs.daily.recover_missing_prices",
        lambda supplied: expected if supplied is engine else None,
    )

    assert recover_prices(engine=engine) == expected


def test_startup_price_recovery_tolerates_initialization_delay():
    jobs = {job.id: job for job in build_scheduler(interval_seconds=30).get_jobs()}

    assert jobs["startup-price-recovery"].misfire_grace_time == 300


def test_daily_refresh_runs_at_eight_hong_kong_time_and_can_be_switched_off(monkeypatch):
    monkeypatch.delenv("AI_DAILY_CRON", raising=False)
    monkeypatch.delenv("AI_INTRADAY_CRON", raising=False)
    monkeypatch.delenv("RADAR_INTRADAY_SYNC_CRON", raising=False)
    monkeypatch.setenv("TZ", "Asia/Hong_Kong")
    jobs = {job.id: job for job in build_scheduler(interval_seconds=30).get_jobs()}
    trigger = jobs["daily-refresh"].trigger
    fields = {field.name: str(field) for field in trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("8", "0")
    assert str(trigger.timezone) == "Asia/Hong_Kong"

    off = build_scheduler(interval_seconds=30, cron="off").get_jobs()
    assert [j.id for j in off] == [
        "heartbeat", "admin-ai-requests", "startup-price-recovery",
        "hkex-catalog-refresh", "intraday-sync", "intraday-refresh",
    ]


def test_hkex_catalog_refresh_runs_daily_at_seven_hong_kong_time(monkeypatch):
    monkeypatch.delenv("HKEX_CATALOG_CRON", raising=False)
    monkeypatch.setenv("TZ", "Asia/Hong_Kong")
    jobs = {job.id: job for job in build_scheduler(interval_seconds=30).get_jobs()}
    trigger = jobs["hkex-catalog-refresh"].trigger
    fields = {field.name: str(field) for field in trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("7", "0")
    assert str(trigger.timezone) == "Asia/Hong_Kong"

    job_ids = [job.id for job in build_scheduler(
        interval_seconds=30,
        catalog_expression="off",
    ).get_jobs()]
    assert "hkex-catalog-refresh" not in job_ids


def test_intraday_sync_is_half_hourly_and_analysis_is_hourly_in_hong_kong(monkeypatch):
    monkeypatch.delenv("AI_INTRADAY_CRON", raising=False)
    monkeypatch.delenv("RADAR_INTRADAY_SYNC_CRON", raising=False)
    monkeypatch.setenv("TZ", "Asia/Hong_Kong")
    jobs = {job.id: job for job in build_scheduler(interval_seconds=30).get_jobs()}
    analysis_fields = {
        field.name: str(field) for field in jobs["intraday-refresh"].trigger.fields
    }
    sync_fields = {
        field.name: str(field) for field in jobs["intraday-sync"].trigger.fields
    }

    assert (sync_fields["hour"], sync_fields["minute"]) == ("*", "5,35")
    assert (analysis_fields["hour"], analysis_fields["minute"]) == ("*", "40")
    assert str(jobs["intraday-refresh"].trigger.timezone) == "Asia/Hong_Kong"

    off = build_scheduler(
        interval_seconds=30,
        cron="off",
        catalog_expression="off",
        intraday_expression="off",
        intraday_sync_expression="off",
    ).get_jobs()
    assert [job.id for job in off] == [
        "heartbeat", "admin-ai-requests", "startup-price-recovery",
    ]


def test_startup_upgrade_applies_pending_migrations(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    from radar_db import make_engine
    from scheduler import ROOT, upgrade_schema

    url = "sqlite:///" + (tmp_path / "radar.db").as_posix()
    monkeypatch.setenv("RADAR_DB_URL", url)
    config = Config()
    config.set_main_option("script_location", str(ROOT / "radar_db" / "migrations"))
    config.set_main_option("prepend_sys_path", str(ROOT))
    command.upgrade(config, "0014")
    engine = make_engine(url)
    assert "requested_by" not in {c["name"] for c in inspect(engine).get_columns("ai_analysis_requests")}

    assert upgrade_schema(engine) is True
    assert "requested_by" in {c["name"] for c in inspect(engine).get_columns("ai_analysis_requests")}


def test_startup_upgrade_leaves_unversioned_databases_alone():
    from scheduler import upgrade_schema

    assert upgrade_schema(_engine()) is False


def test_interval_comes_from_the_argument():
    jobs = {job.id: job for job in build_scheduler(interval_seconds=30, poll_seconds=7).get_jobs()}
    assert jobs["heartbeat"].trigger.interval.total_seconds() == 30
    assert jobs["admin-ai-requests"].trigger.interval.total_seconds() == 7


def test_interval_falls_back_to_the_environment(monkeypatch):
    monkeypatch.setenv("WORKER_HEARTBEAT_INTERVAL_SECONDS", "120")
    monkeypatch.setenv("AI_REQUEST_POLL_SECONDS", "9")
    jobs = {job.id: job for job in build_scheduler().get_jobs()}
    assert jobs["heartbeat"].trigger.interval.total_seconds() == 120
    assert jobs["admin-ai-requests"].trigger.interval.total_seconds() == 9


def test_heartbeat_is_persisted_for_admin_queue_health(monkeypatch):
    engine = _engine()
    monkeypatch.setattr(
        "scheduler.runtime_readiness",
        lambda: {"ready": False, "error": "质量报告未配置"},
    )

    heartbeat(engine=engine, now="2026-10-06T01:02:03Z")

    with engine.connect() as conn:
        values = dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v)).all())
    assert values["ai_analysis_worker_heartbeat"] == "2026-10-06T01:02:03Z"
    assert values["ai_analysis_worker_readiness"] == (
        '{"ready": false, "error": "质量报告未配置"}'
    )


def test_heartbeat_never_rewrites_the_data_backed_anchor(monkeypatch):
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(meta_kv.insert(), [
            {"k": "anchor", "v": "2026-11-11"},
            {"k": "anchor_ts", "v": "2026-11-11 23:59:59"},
        ])
    monkeypatch.setenv("RADAR_ANCHOR_MODE", "system")
    monkeypatch.setattr(
        "scheduler.runtime_readiness",
        lambda: {"ready": True, "error": None},
    )

    heartbeat(
        engine=engine,
        now=datetime(2026, 11, 12, 21, 45, tzinfo=ZoneInfo("Asia/Hong_Kong")),
    )

    with engine.connect() as conn:
        values = dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v)).all())
    assert values["anchor"] == "2026-11-11"
    assert values["anchor_ts"] == "2026-11-11 23:59:59"


def test_local_heartbeat_precedes_database_access(monkeypatch, tmp_path):
    import pytest

    health_file = tmp_path / "heartbeat"
    monkeypatch.setenv("WORKER_HEALTH_FILE", str(health_file))

    def unavailable():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("scheduler.make_engine", unavailable)
    with pytest.raises(RuntimeError, match="database unavailable"):
        heartbeat()
    assert health_file.exists()
