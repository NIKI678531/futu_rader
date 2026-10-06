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


def test_heartbeat_job_is_registered():
    jobs = build_scheduler(interval_seconds=30).get_jobs()
    assert [j.id for j in jobs] == ["heartbeat", "admin-ai-requests"]


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
