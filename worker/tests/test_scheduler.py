import os
import sys

from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from radar_db.schema import meta_kv, metadata  # noqa: E402
from scheduler import build_scheduler, heartbeat  # noqa: E402


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
    jobs = build_scheduler(interval_seconds=30).get_jobs()
    assert [j.id for j in jobs] == [
        "heartbeat", "admin-ai-requests", "daily-refresh", "intraday-refresh",
    ]


def test_daily_refresh_runs_at_eight_hong_kong_time_and_can_be_switched_off(monkeypatch):
    monkeypatch.delenv("AI_DAILY_CRON", raising=False)
    monkeypatch.delenv("AI_INTRADAY_CRON", raising=False)
    monkeypatch.setenv("TZ", "Asia/Hong_Kong")
    jobs = {job.id: job for job in build_scheduler(interval_seconds=30).get_jobs()}
    trigger = jobs["daily-refresh"].trigger
    fields = {field.name: str(field) for field in trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("8", "0")
    assert str(trigger.timezone) == "Asia/Hong_Kong"

    off = build_scheduler(interval_seconds=30, cron="off").get_jobs()
    assert [j.id for j in off] == ["heartbeat", "admin-ai-requests", "intraday-refresh"]


def test_intraday_refresh_runs_at_thirteen_and_fifteen_hong_kong_time(monkeypatch):
    monkeypatch.delenv("AI_INTRADAY_CRON", raising=False)
    monkeypatch.setenv("TZ", "Asia/Hong_Kong")
    jobs = {job.id: job for job in build_scheduler(interval_seconds=30).get_jobs()}
    trigger = jobs["intraday-refresh"].trigger
    fields = {field.name: str(field) for field in trigger.fields}

    assert (fields["hour"], fields["minute"]) == ("13,15", "0")
    assert str(trigger.timezone) == "Asia/Hong_Kong"

    off = build_scheduler(
        interval_seconds=30,
        cron="off",
        intraday_expression="off",
    ).get_jobs()
    assert [job.id for job in off] == ["heartbeat", "admin-ai-requests"]


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
