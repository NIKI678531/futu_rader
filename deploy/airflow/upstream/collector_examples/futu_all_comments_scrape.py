"""Full Futu community comments collector with a verified completion receipt."""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.datasets import Dataset
from airflow.models.baseoperator import chain
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator, ShortCircuitOperator

from common.callbacks import on_failure_power_automate
from common.constants import DEFAULT_ARGS
from common.futu_collection_receipts import (
    fail_receipt,
    finish_receipt,
    start_receipt,
    write_manifest_symbols,
)

REPO_DIR = "/opt/airflow/dags/repo/dags/pull_data/futu_comments"
WORK_DIR = "/tmp/futu_comments_all_work"
REQUIRED_COOKIE_KEYS = ("cipher_device_id", "uid", "web_sig", "ci_sig")
HKT = pendulum.timezone("Asia/Hong_Kong")
DEFAULT_SINCE_DATE = "2026-05-01"
COMMUNITY_DATASET = Dataset("market-insight://futu-community")
RUN_ID_TEMPLATE = "{{ dag.dag_id }}:{{ run_id }}"
SCHEDULED_FOR_TEMPLATE = (
    "{{ data_interval_end.in_timezone('UTC').format('YYYY-MM-DD HH:mm:ss.SSSSSS') }}"
)
COMPLETE_THROUGH_TEMPLATE = (
    "{% set t = data_interval_end.in_timezone('Asia/Hong_Kong') %}"
    "{% if t.hour == 19 and t.minute == 30 %}{{ t.to_date_string() }}{% endif %}"
)


def _copy_repo_to_workdir():
    import shutil
    from pathlib import Path

    work = Path(WORK_DIR)
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(REPO_DIR, WORK_DIR)


def _cookie_keys(cookie_str: str) -> set[str]:
    return {
        piece.split("=", 1)[0].strip()
        for piece in cookie_str.split(";")
        if "=" in piece
    }


def _write_cookies_from_variable():
    import os
    import stat
    from pathlib import Path

    from airflow.models import Variable

    cookie_str = Variable.get("futu_cookies").strip()
    if cookie_str.lower().startswith("cookie:"):
        cookie_str = cookie_str.split(":", 1)[1].strip()
    missing = sorted(set(REQUIRED_COOKIE_KEYS) - _cookie_keys(cookie_str))
    if missing:
        raise ValueError("futu_cookies is missing: " + ", ".join(missing))
    cookie_file = Path(WORK_DIR) / ".cookies"
    cookie_file.write_text(cookie_str + "\n", encoding="utf-8")
    os.chmod(cookie_file, stat.S_IRUSR | stat.S_IWUSR)


def _failure_callback(context):
    run_id = f"{context['dag'].dag_id}:{context['run_id']}"
    exception = context.get("exception")
    fail_receipt(
        collection_run_id=run_id,
        reason=type(exception).__name__ if exception else "Airflow task failed",
    )
    on_failure_power_automate(context)


def _is_closing_run(data_interval_end, **_):
    at = data_interval_end.in_timezone(HKT)
    return at.hour == 19 and at.minute == 30


def _publish_receipt(collection_run_id: str, *, outlet_events, **_):
    outlet_events[COMMUNITY_DATASET].extra = {"source_run_id": collection_run_id}


with DAG(
    dag_id="futu_all_comments_scrape",
    description="Scrape all Futu community comments and prove closing completeness.",
    default_args={
        **DEFAULT_ARGS,
        "on_failure_callback": _failure_callback,
        "retries": 0,
        "retry_delay": timedelta(minutes=2),
    },
    schedule_interval="30 8,13,19 * * 1-5",
    start_date=pendulum.datetime(2026, 4, 13, tz=HKT),
    catchup=False,
    max_active_runs=1,
    tags=["futu", "scraper"],
) as dag:
    copy_repo = PythonOperator(
        task_id="copy_repo", python_callable=_copy_repo_to_workdir
    )
    prepare_manifest_symbols = PythonOperator(
        task_id="prepare_manifest_symbols",
        python_callable=write_manifest_symbols,
        op_kwargs={"work_dir": WORK_DIR, "symbol_scope": "all"},
    )
    start_collection_receipt = PythonOperator(
        task_id="start_collection_receipt",
        python_callable=start_receipt,
        op_kwargs={
            "collection_run_id": RUN_ID_TEMPLATE,
            "collection_kind": "comments_all",
            "scheduled_for_utc": SCHEDULED_FOR_TEMPLATE,
            "verify_all_codes": True,
        },
    )
    write_cookies = PythonOperator(
        task_id="write_cookies", python_callable=_write_cookies_from_variable
    )
    scrape = BashOperator(
        task_id="scrape",
        execution_timeout=timedelta(hours=8),
        env={
            "MARKET_INSIGHT_DATABASE_URL": "{{ var.value.market_insight_database_url }}",
        },
        append_env=True,
        bash_command=(
            f"cd {WORK_DIR} && "
            "pip install -q -r requirements.txt && "
            "python -u -m futu_scraper.cli scrape"
            " --db \"$MARKET_INSIGHT_DATABASE_URL\""
            " --workers 3 --symbol-scope all"
            " --symbols collection_symbols.json"
            "{% set since_date = var.value.get('futu_comments_since_date', '"
            f"{DEFAULT_SINCE_DATE}"
            "') | trim %}"
            "{% if not since_date %}"
            f"{{% set since_date = '{DEFAULT_SINCE_DATE}' %}}"
            "{% endif %}"
            " --since-date {{ since_date }}"
        ),
    )
    finish_collection_receipt = PythonOperator(
        task_id="finish_collection_receipt",
        python_callable=finish_receipt,
        op_kwargs={
            "collection_run_id": RUN_ID_TEMPLATE,
            "complete_through": COMPLETE_THROUGH_TEMPLATE,
            "verify_all_codes": True,
        },
    )
    closing_run_only = ShortCircuitOperator(
        task_id="closing_run_only", python_callable=_is_closing_run
    )
    publish_complete_receipt = PythonOperator(
        task_id="publish_complete_receipt",
        python_callable=_publish_receipt,
        op_kwargs={"collection_run_id": RUN_ID_TEMPLATE},
        outlets=[COMMUNITY_DATASET],
    )

    chain(
        copy_repo,
        prepare_manifest_symbols,
        start_collection_receipt,
        write_cookies,
        scrape,
        finish_collection_receipt,
        closing_run_only,
        publish_complete_receipt,
    )
