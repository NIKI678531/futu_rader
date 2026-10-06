"""DAG policy checks that also run on developer machines without Airflow."""

from pathlib import Path


DAGS = Path(__file__).resolve().parents[1] / "dags"


def source(name):
    return (DAGS / name).read_text(encoding="utf-8")


def test_sync_dag_keeps_the_dataset_and_runtime_safety_contract():
    text = source("futu_radar_sync.py")
    assert 'DATASET_URI = "market-insight://futu-community"' in text
    assert "max_active_runs=1" in text
    assert 'default_args={"retries": 2' in text
    assert "is_paused_upon_creation=True" in text
    assert "@sha256:" in text
    assert "futu_radar_runtime_secret" in text
    assert "--through-source-run-id" in text
    assert "source_run_id" in text


def test_ai_dag_keeps_the_schedule_budget_and_runtime_contract():
    text = source("futu_radar_ai_analysis.py")
    assert 'schedule="0 8 * * *"' in text
    assert "max_active_runs=1" in text
    assert "is_paused_upon_creation=True" in text
    assert "@sha256:" in text
    assert "futu_radar_runtime_secret" in text
    assert '"--concurrency", "2"' in text
    assert '"--max-http-attempts", "500"' in text
    assert '"--ranges", "{{ dag_run.conf.get(\'ranges\', \'\') if dag_run else \'\' }}"' in text
    assert '"--codes", "{{ dag_run.conf.get(\'codes\', \'\') if dag_run else \'\' }}"' in text


def test_hkex_catalog_dag_runs_daily_at_0700_hong_kong_time():
    text = source("futu_radar_hkex_catalog.py")
    assert 'schedule="0 7 * * *"' in text
    assert 'pendulum.timezone("Asia/Hong_Kong")' in text
    assert "max_active_runs=1" in text
    assert "is_paused_upon_creation=True" in text
    assert "@sha256:" in text
    assert "futu_radar_runtime_secret" in text
    assert 'arguments=["sync"]' in text

