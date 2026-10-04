from __future__ import annotations

from pathlib import Path

import pytest


airflow = pytest.importorskip("airflow")


def test_dag_contract(monkeypatch):
    from airflow.models import DagBag, Variable

    values = {
        "futu_radar_worker_image": "registry.example/futu-radar@sha256:" + ("a" * 64),
        "futu_radar_namespace": "radar",
        "futu_radar_service_account": "radar-ai",
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
    dag = bag.get_dag("futu_radar_ai_analysis")
    task = dag.get_task("analyze")
    assert str(dag.timezone) == "Asia/Hong_Kong"
    assert dag.schedule_interval == "30 20 * * 1-5"
    assert dag.catchup is False
    assert dag.max_active_runs == 1
    assert dag.is_paused_upon_creation is True
    assert task.retries == 0
    assert task.image.endswith("@sha256:" + ("a" * 64))
    assert task.namespace == "radar"
    assert task.service_account_name == "radar-ai"
    assert task.cmds == ["python", "-X", "utf8", "-m", "jobs.refresh"]
    assert task.arguments == [
        "analyze", "--mode", "auto", "--budget-date",
        "{{ data_interval_end.in_timezone('Asia/Hong_Kong').to_date_string() }}",
        "--anchor", "{{ dag_run.conf.get('anchor', '') if dag_run else '' }}",
        "--wait-for-ready",
        "--wait-timeout-seconds", "28800", "--batch-size", "5",
        "--concurrency", "2", "--max-http-attempts", "500",
    ]
    assert task.env_from[0].secret_ref.name == "radar-runtime"

