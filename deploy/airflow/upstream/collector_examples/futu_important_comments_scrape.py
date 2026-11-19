"""Important-symbol Futu comments collector.

This run records an audit receipt but never claims a complete source day and
never publishes the Radar completion Dataset.
"""

from __future__ import annotations

from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.models.baseoperator import chain
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

from common.callbacks import on_failure_power_automate
from common.constants import DEFAULT_ARGS
from common.futu_collection_receipts import (
    fail_receipt,
    finish_receipt,
    start_receipt,
    write_manifest_symbols,
)

REPO_DIR = "/opt/airflow/dags/repo/dags/pull_data/futu_comments"
WORK_DIR = "/tmp/futu_comments_important_work"
REQUIRED_COOKIE_KEYS = ("cipher_device_id", "uid", "web_sig", "ci_sig")
HKT = pendulum.timezone("Asia/Hong_Kong")
DEFAULT_SINCE_DATE = "2026-05-01"
RUN_ID_TEMPLATE = "{{ dag.dag_id }}:{{ run_id }}"
SCHEDULED_FOR_TEMPLATE = (
    "{{ data_interval_end.in_timezone('UTC').format('YYYY-MM-DD HH:mm:ss.SSSSSS') }}"
)


def _copy_repo_to_workdir():
    import shutil
    from pathlib import Path

    work = Path(WORK_DIR)
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(REPO_DIR, WORK_DIR)


def _write_cookies_from_variable():
    import os
    import stat
    from pathlib import Path

    from airflow.models import Variable

    cookie_str = Variable.get("futu_cookies").strip()
    if cookie_str.lower().startswith("cookie:"):
        cookie_str = cookie_str.split(":", 1)[1].strip()
    present = {
        item.split("=", 1)[0].strip()
        for item in cookie_str.split(";")
        if "=" in item
    }
    missing = sorted(set(REQUIRED_COOKIE_KEYS) - present)
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


with DAG(
    dag_id="futu_important_comments_scrape",
    description="Scrape important Futu community comments.",
    default_args={
        **DEFAULT_ARGS,
        "on_failure_callback": _failure_callback,
        "retries": 0,
        "retry_delay": timedelta(minutes=2),
    },
    schedule_interval="0 10-17 * * 1-5",
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
        op_kwargs={"work_dir": WORK_DIR, "symbol_scope": "important"},
    )
    start_collection_receipt = PythonOperator(
        task_id="start_collection_receipt",
        python_callable=start_receipt,
        op_kwargs={
            "collection_run_id": RUN_ID_TEMPLATE,
            "collection_kind": "comments_important",
            "scheduled_for_utc": SCHEDULED_FOR_TEMPLATE,
            "verify_all_codes": False,
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
            " --workers 3 --symbol-scope important"
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
            "complete_through": "",
            "verify_all_codes": False,
        },
    )

    chain(
        copy_repo,
        prepare_manifest_symbols,
        start_collection_receipt,
        write_cookies,
        scrape,
        finish_collection_receipt,
    )
