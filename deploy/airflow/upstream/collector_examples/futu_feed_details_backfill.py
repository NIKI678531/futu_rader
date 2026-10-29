"""Backfill full Futu feed bodies and record a non-completing audit receipt."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pendulum
from airflow import DAG
from airflow.models.baseoperator import chain
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

from common.callbacks import on_failure_power_automate
from common.constants import DEFAULT_ARGS
from common.futu_collection_receipts import fail_receipt, finish_receipt, start_receipt

REPO_DIR = "/opt/airflow/dags/repo/dags/pull_data/futu_comments"
WORK_DIR = "/tmp/futu_feed_details_backfill_work"
REQUIRED_COOKIE_KEYS = ("cipher_device_id", "uid", "web_sig", "ci_sig")
HKT = pendulum.timezone("Asia/Hong_Kong")
DEFAULT_RECENT_DAYS = "30"
DEFAULT_MAX_FEEDS = "1000"
KOL_ROSTER_FILE = Path(__file__).with_name("futu_radar_kol_roster.json")
RUN_ID_TEMPLATE = "{{ dag.dag_id }}:{{ run_id }}"
SCHEDULED_FOR_TEMPLATE = (
    "{{ data_interval_end.in_timezone('UTC').format('YYYY-MM-DD HH:mm:ss.SSSSSS') }}"
)


def _active_kol_user_ids():
    """Load the same stable Futu UIDs used by Radar's account domain.

    Nicknames are not identities: several active KOLs publish under a previous
    display name, and newly activated KOLs may be absent from the scraper's
    historical ``kol.json``.  The committed roster copy is checked against the
    application source in the Radar repository test suite.
    """
    payload = json.loads(KOL_ROSTER_FILE.read_text(encoding="utf-8"))
    active = payload.get("activeRoster")
    user_ids = payload.get("userIds")
    if not isinstance(active, list) or not isinstance(user_ids, dict):
        raise ValueError("futu_radar_kol_roster.json has an invalid roster shape")
    values = []
    for name in active:
        for user_id in user_ids.get(name, []):
            value = str(user_id).strip()
            if not value.isdigit():
                raise ValueError(f"Invalid Futu user ID for active KOL {name!r}")
            if value not in values:
                values.append(value)
    if not values:
        raise ValueError("Active KOL roster contains no stable Futu user IDs")
    return tuple(values)


ACTIVE_KOL_USER_IDS = _active_kol_user_ids()
ACTIVE_KOL_USER_ID_ARGS = " ".join(ACTIVE_KOL_USER_IDS)


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
    dag_id="futu_feed_details_backfill",
    description="Continuously refresh complete Futu post content from detail pages.",
    default_args={
        **DEFAULT_ARGS,
        "on_failure_callback": _failure_callback,
        "retries": 0,
        "retry_delay": timedelta(minutes=2),
    },
    # The list-feed collector runs throughout the trading day. Refresh detail
    # bodies shortly afterwards so late-paragraph ETF mentions do not remain
    # absent from the account pages until the following evening.
    schedule_interval="15 9-20 * * 1-5",
    start_date=pendulum.datetime(2026, 6, 22, tz=HKT),
    catchup=False,
    max_active_runs=1,
    tags=["futu", "scraper", "feed-details"],
) as dag:
    copy_repo = PythonOperator(
        task_id="copy_repo", python_callable=_copy_repo_to_workdir
    )
    start_collection_receipt = PythonOperator(
        task_id="start_collection_receipt",
        python_callable=start_receipt,
        op_kwargs={
            "collection_run_id": RUN_ID_TEMPLATE,
            "collection_kind": "feed_details",
            "scheduled_for_utc": SCHEDULED_FOR_TEMPLATE,
            "verify_all_codes": False,
        },
    )
    write_cookies = PythonOperator(
        task_id="write_cookies", python_callable=_write_cookies_from_variable
    )
    fetch_feed_details = BashOperator(
        task_id="fetch_feed_details",
        execution_timeout=timedelta(hours=4),
        env={
            "MARKET_INSIGHT_DATABASE_URL": "{{ var.value.market_insight_database_url }}",
        },
        append_env=True,
        bash_command=(
            f"cd {WORK_DIR} && "
            "python -m venv .detail-venv && "
            ".detail-venv/bin/pip install -q -r requirements.txt && "
            ".detail-venv/bin/python -u -m futu_scraper.cli fetch-feed-details"
            " --db \"$MARKET_INSIGHT_DATABASE_URL\" --kol-file kol.json"
            f" --author-id {ACTIVE_KOL_USER_ID_ARGS}"
            f" --recent-days {DEFAULT_RECENT_DAYS}"
            f" --max-feeds {DEFAULT_MAX_FEEDS}"
            " --workers 2 && "
            ".detail-venv/bin/python -u verify_futu_feed_details.py"
            " --db \"$MARKET_INSIGHT_DATABASE_URL\""
            " --roster futu_radar_kol_roster.json"
            f" --recent-days {DEFAULT_RECENT_DAYS}"
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
        start_collection_receipt,
        write_cookies,
        fetch_feed_details,
        finish_collection_receipt,
    )
