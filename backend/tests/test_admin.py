from __future__ import annotations

from datetime import date, datetime, timezone
import json

import pytest
from sqlalchemy import Column, Date, DateTime, Integer, MetaData, String, Table, create_engine, insert
from sqlalchemy.pool import StaticPool

from app import create_app
from core.admin_ops import (
    AdminConflict,
    AdminOperations,
    AirflowAdapter,
    MarketInsightStatusAdapter,
    configured_symbols_fingerprint,
    sanitize_error,
)
from radar_db.comment_routes import readiness_values
from radar_db.product_catalog import load_products
from radar_db.schema import ingestion_runs, metadata, meta_kv


class DummyOperations:
    def data_sources(self):
        return {"chainState": "current"}

    def tasks(self):
        return {"items": []}

    def job_runs(self, task, limit):
        return {"task": task, "limit": limit, "items": []}

    def trigger(self, task):
        return {"task": task, "run": {"status": "queued"}}


def _admin_client(monkeypatch, operations=None):
    monkeypatch.setenv("ADMIN_API_TOKEN", "correct horse")
    app = create_app()
    app.config.update(
        TESTING=True,
        ADMIN_OPERATIONS_FACTORY=lambda: operations or DummyOperations(),
    )
    return app.test_client()


def test_every_admin_endpoint_requires_the_independent_token(monkeypatch):
    client = _admin_client(monkeypatch)
    assert client.get("/api/v1/admin/data-sources").status_code == 401
    assert client.get(
        "/api/v1/admin/data-sources", headers={"X-Admin-Token": "wrong"}
    ).status_code == 401
    response = client.get(
        "/api/v1/admin/data-sources", headers={"X-Admin-Token": "correct horse"}
    )
    assert response.status_code == 200
    assert response.json["data"]["chainState"] == "current"


def test_admin_api_fails_closed_when_server_token_is_missing(monkeypatch):
    monkeypatch.delenv("ADMIN_API_TOKEN", raising=False)
    app = create_app()
    app.config.update(TESTING=True, ADMIN_OPERATIONS_FACTORY=DummyOperations)
    response = app.test_client().get(
        "/api/v1/admin/tasks", headers={"X-Admin-Token": "anything"}
    )
    assert response.status_code == 503
    assert response.json["error"]["type"] == "admin_not_configured"


def test_task_name_and_limit_are_whitelisted(monkeypatch):
    client = _admin_client(monkeypatch)
    headers = {"X-Admin-Token": "correct horse"}
    assert client.get("/api/v1/admin/job-runs?task=repair", headers=headers).status_code == 400
    assert client.get("/api/v1/admin/job-runs?limit=0", headers=headers).status_code == 400
    assert client.get("/api/v1/admin/job-runs?limit=201", headers=headers).status_code == 400


def test_trigger_returns_202_and_duplicate_running_returns_structured_409(monkeypatch):
    client = _admin_client(monkeypatch)
    headers = {"X-Admin-Token": "correct horse"}
    response = client.post("/api/v1/admin/tasks/sync/run", headers=headers)
    assert response.status_code == 202
    assert response.json["data"]["run"]["status"] == "queued"

    class Blocked(DummyOperations):
        def trigger(self, task):
            raise AdminConflict("任务当前不能启动", blocked_reasons=["已有同类任务正在运行"])

    blocked = _admin_client(monkeypatch, Blocked()).post(
        "/api/v1/admin/tasks/sync/run", headers=headers
    )
    assert blocked.status_code == 409
    assert blocked.json["error"]["blockedReasons"] == ["已有同类任务正在运行"]


def _engine():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    return engine


class FakeSource:
    def __init__(self, complete_through="2026-10-01"):
        self.complete_through = complete_through

    def status(self):
        run = {
            "runId": "source-42",
            "status": "succeeded",
            "completeThrough": self.complete_through,
        }
        return {
            "configured": True,
            "available": True,
            "latestRun": run,
            "latestCompleteRun": run,
            "error": None,
        }


class FakeAirflow:
    configured = True

    def __init__(self, runs=None, available=True, paused=False):
        self.runs = runs or []
        self.available = available
        self.paused = paused
        self.triggered = []

    def snapshot(self, dag_id, run_limit=20):
        return {
            "configured": True,
            "available": self.available,
            "isPaused": self.paused,
            "nextRunAt": None,
            "runs": self.runs[:run_limit],
            "error": None if self.available else "Airflow API 暂时不可用",
        }

    def trigger(self, dag_id, *, dag_run_id, conf):
        self.triggered.append((dag_id, dag_run_id, conf))
        return {"dagRunId": dag_run_id, "status": "queued", "conf": conf}


def test_chain_state_maps_newer_upstream_to_waiting_for_sync():
    engine = _engine()
    with engine.begin() as conn:
        conn.execute(insert(meta_kv), [{"k": "anchor", "v": "2026-09-30"}])
    ops = AdminOperations(
        target_engine=engine,
        source=FakeSource("2026-10-01"),
        airflow=FakeAirflow(),
        environment={},
        now=lambda: datetime(2026, 10, 2, tzinfo=timezone.utc),
    )
    result = ops.data_sources()
    assert result["chainState"] == "waiting_for_sync"
    assert result["waitingForSync"] is True


def test_running_dag_is_rejected_before_airflow_submission():
    airflow = FakeAirflow(runs=[{"status": "running", "dagRunId": "already-running"}])
    ops = AdminOperations(
        target_engine=_engine(), source=FakeSource(), airflow=airflow, environment={}
    )
    with pytest.raises(AdminConflict) as caught:
        ops.trigger("sync")
    assert "已有同类任务正在运行" in caught.value.blocked_reasons
    assert airflow.triggered == []


def test_airflow_timeout_degrades_to_read_only_status():
    def timeout(*_args, **_kwargs):
        raise TimeoutError

    adapter = AirflowAdapter("https://airflow.internal", "secret", opener=timeout)
    snapshot = adapter.snapshot("futu_radar_sync")
    assert snapshot["configured"] is True
    assert snapshot["available"] is False
    assert snapshot["runs"] == []
    assert "暂时不可用" in snapshot["error"]


def test_errors_are_redacted_and_truncated():
    value = "mysql://alice:password@db.local/x?token=abc password=hunter2 " + ("x" * 800)
    cleaned = sanitize_error(value)
    assert "alice:password" not in cleaned
    assert "hunter2" not in cleaned
    assert "abc" not in cleaned
    assert len(cleaned) <= 500


def test_upstream_adapter_only_returns_a_proven_complete_receipt():
    engine = create_engine("sqlite://", future=True, poolclass=StaticPool)
    source_meta = MetaData()
    receipts = Table(
        "futu_comments_collection_runs",
        source_meta,
        Column("run_id", String(250), primary_key=True),
        Column("collection_kind", String(40), nullable=False),
        Column("status", String(20), nullable=False),
        Column("complete_through", Date),
        Column("scheduled_for", DateTime),
        Column("finished_at", DateTime),
        Column("configured_symbol_count", Integer),
        Column("configured_symbols_fingerprint", String(128)),
        Column("attempted_symbol_count", Integer),
        Column("succeeded_symbol_count", Integer),
    )
    source_meta.create_all(engine)
    codes = [product["code"] for product in load_products()]
    common = {
        "collection_kind": "comments_all",
        "status": "succeeded",
        "scheduled_for": datetime(2026, 10, 1, 11, 30),
        "finished_at": datetime(2026, 10, 1, 11, 45),
        "configured_symbol_count": len(codes),
        "configured_symbols_fingerprint": configured_symbols_fingerprint(codes),
        "attempted_symbol_count": len(codes),
    }
    with engine.begin() as conn:
        conn.execute(insert(receipts), [
            {**common, "run_id": "partial-success", "complete_through": date(2026, 10, 1),
             "succeeded_symbol_count": len(codes) - 1},
            {**common, "run_id": "complete-success", "complete_through": date(2026, 9, 30),
             "succeeded_symbol_count": len(codes),
             "finished_at": datetime(2026, 10, 1, 11, 40)},
        ])
    status = MarketInsightStatusAdapter(engine=engine).status()
    assert status["latestRun"]["runId"] == "partial-success"
    assert status["latestCompleteRun"]["runId"] == "complete-success"


def test_ai_button_requires_reports_matching_the_active_route_generation():
    engine = _engine()
    route_values = readiness_values()
    now = datetime(2026, 10, 1, 12, 0)
    with engine.begin() as conn:
        conn.execute(insert(meta_kv), [
            {"k": "anchor", "v": "2026-10-01"},
            *({"k": key, "v": value} for key, value in route_values.items()),
        ])
        conn.execute(insert(ingestion_runs).values(
            run_id="sync-1", source="market_insight", source_run_id="source-42",
            source_kind="all", status="succeeded", started_at=now, finished_at=now,
            complete_through=date(2026, 10, 1),
        ))
    common = {
        "promptVersion": "comment-product-v3",
        "schemaVersion": "v2",
        "taxonomyVersion": "v2",
        "commentRouteVersion": route_values["comment_route_version"],
        "productPoolDigest": route_values["comment_route_pool_digest"],
    }
    calibration = {
        "batchGatePassed": True,
        "policy": {"model": "model-1", **common, "batchSize": 5,
                   "maxInputTokens": 8000, "maxPayloadBytes": 12288,
                   "maxOutputTokens": 8192},
    }
    quality = {
        "reportType": "comment-relevance-human-gold-v1", "sample_source": "llm",
        "n": 400, "qualityGate": {"passed": True},
        "policy": {"model": "returned-snapshot", "requestedModel": "model-1", **common},
    }
    environment = {
        "AI_DATA_GOVERNANCE_APPROVED": "true",
        "AI_PRIMARY_MODEL": "model-1",
        "AI_CALIBRATION_REPORT": json.dumps(calibration),
        "AI_QUALITY_REPORT": json.dumps(quality),
    }
    ops = AdminOperations(
        target_engine=engine,
        source=FakeSource("2026-10-01"),
        airflow=FakeAirflow(),
        environment=environment,
    )
    analyze_task = next(item for item in ops.tasks()["items"] if item["id"] == "analyze")
    assert analyze_task["canRun"] is True

    quality["policy"]["productPoolDigest"] = "stale"
    environment["AI_QUALITY_REPORT"] = json.dumps(quality)
    blocked = next(item for item in ops.tasks()["items"] if item["id"] == "analyze")
    assert blocked["canRun"] is False
    assert any("人工金标" in reason for reason in blocked["blockedReasons"])

