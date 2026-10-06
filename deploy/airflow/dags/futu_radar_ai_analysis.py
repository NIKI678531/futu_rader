"""Scheduled daily full refresh for Futu Radar (ADR-0031).

At 08:00 Asia/Hong_Kong the pod runs the same entry point as the worker's own
cron: sync MarketInsight through the latest completed ``comments_all`` receipt
(the previous evening's 19:30 run), sync prices, enqueue every page range and
execute the queue inside the pod until it is empty.  Runtime credentials come
from one Kubernetes Secret; non-secret placement is configured with Airflow
Variables.

Run either this DAG or the always-on worker's ``AI_DAILY_CRON``, not both.  If
both fire, the queue de-duplicates active ranges and the analysis lease keeps a
single executor, so no work is done twice.
"""

from __future__ import annotations

from datetime import timedelta
import re

import pendulum
from airflow import DAG
from airflow.models import Variable
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from kubernetes.client import models as k8s


DAG_ID = "futu_radar_ai_analysis"
TIMEZONE = pendulum.timezone("Asia/Hong_Kong")
PLACEHOLDER_DIGEST_IMAGE = "configure-me@sha256:" + ("0" * 64)


def _variable(name: str, default: str) -> str:
    value = str(Variable.get(name, default_var=default)).strip()
    return value or default


worker_image = _variable("futu_radar_worker_image", PLACEHOLDER_DIGEST_IMAGE)
if re.fullmatch(r"[^\s@]+@sha256:[0-9a-fA-F]{64}", worker_image) is None:
    raise ValueError("Airflow Variable futu_radar_worker_image must pin an image digest")

namespace = _variable("futu_radar_namespace", "default")
service_account = _variable("futu_radar_service_account", "default")
runtime_secret = _variable("futu_radar_runtime_secret", "futu-radar-runtime")


with DAG(
    dag_id=DAG_ID,
    description="Daily full refresh: sync, prices and every page range of AI analysis",
    schedule="0 8 * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz=TIMEZONE),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    default_args={"retries": 0, "retry_delay": timedelta(minutes=5)},
    tags=["futu-radar", "ai", "production"],
) as dag:
    analyze = KubernetesPodOperator(
        task_id="analyze",
        name="futu-radar-daily-refresh",
        namespace=namespace,
        service_account_name=service_account,
        image=worker_image,
        image_pull_policy="IfNotPresent",
        cmds=["python", "-X", "utf8", "-m", "jobs.daily"],
        arguments=["--now", "--drain"],
        env_from=[
            k8s.V1EnvFromSource(
                secret_ref=k8s.V1SecretEnvSource(name=runtime_secret)
            )
        ],
        get_logs=True,
        do_xcom_push=False,
        is_delete_operator_pod=True,
        startup_timeout_seconds=600,
        retries=0,
    )
