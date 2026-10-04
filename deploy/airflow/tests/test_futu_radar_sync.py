from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


airflow = pytest.importorskip("airflow")


def _load(monkeypatch):
    from airflow.models import DagBag, Variable

    values = {
        "futu_radar_worker_image": "registry.example/futu-radar@sha256:" + ("b" * 64),
        "futu_radar_namespace": "radar",
        "futu_radar_service_account": "radar-worker",
        "futu_radar_runtime_secret": "radar-runtime",
    }
    monkeypatch.setattr(
        Variable,
        "get",
        staticmethod(lambda name, default_var=None: values.get(name, default_var)),
    )
    path = Path(__file__).resolve().parents[1] / "dags"
    bag = DagBag(dag_folder=str(path), include_examples=False)
    assert bag.import_errors == {}
    return bag.get_dag("futu_radar_sync")


def test_sync_dag_contract(monkeypatch):
    dag = _load(monkeypatch)
    resolve = dag.get_task("resolve_source_run")
    sync = dag.get_task("sync")
    assert dag.catchup is False
    assert dag.max_active_runs == 1
    assert dag.is_paused_upon_creation is True
    assert resolve.retries == 2
    assert sync.retries == 2
    assert sync.image.endswith("@sha256:" + ("b" * 64))
    assert sync.env_from[0].secret_ref.name == "radar-runtime"
    assert sync.arguments[:3] == ["sync", "--mode", "incremental"]
    assert "--through-source-run-id" in sync.arguments
    assert resolve.downstream_task_ids == {"sync"}
    dataset_uris = {dataset.uri for dataset in dag.dataset_triggers}
    assert dataset_uris == {"market-insight://futu-community"}


def test_exact_source_run_is_required(monkeypatch):
    dag = _load(monkeypatch)
    callable_ = dag.get_task("resolve_source_run").python_callable
    with pytest.raises(ValueError, match="exactly one source_run_id"):
        callable_(dag_run=SimpleNamespace(conf={}, run_id="scheduled__1"))


def test_manual_source_run_is_forwarded_stably(monkeypatch):
    dag = _load(monkeypatch)
    callable_ = dag.get_task("resolve_source_run").python_callable
    context = {
        "dag_run": SimpleNamespace(
            conf={"sourceRunId": "receipt-42"}, run_id="manual__42"
        )
    }
    first = callable_(**context)
    second = callable_(**context)
    assert first == second
    assert first["sourceRunId"] == "receipt-42"
    assert first["radarRunId"].startswith("airflow-")
    assert len(first["radarRunId"]) <= 64

