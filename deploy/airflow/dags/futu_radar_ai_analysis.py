"""Scheduled, bounded Futu Radar AI analysis.

The DAG consumes the 19:30 community collection/sync result.  It never scrapes
Futu directly.  All runtime credentials and release-gate reports come from one
Kubernetes Secret; non-secret deployment placement is configured with Airflow
Variables.
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
    description="Analyze routed ETF comments after the daily MarketInsight sync",
    schedule="30 20 * * 1-5",
    start_date=pendulum.datetime(2026, 1, 1, tz=TIMEZONE),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    default_args={"retries": 0, "retry_delay": timedelta(minutes=5)},
    tags=["futu-radar", "ai", "production"],
) as dag:
    analyze = KubernetesPodOperator(
        task_id="analyze",
        name="futu-radar-ai-analysis",
        namespace=namespace,
        service_account_name=service_account,
        image=worker_image,
        image_pull_policy="IfNotPresent",
        cmds=["python", "-X", "utf8", "-m", "jobs.refresh"],
        arguments=[
            "analyze",
            "--mode", "auto",
            "--budget-date", "{{ data_interval_end.in_timezone('Asia/Hong_Kong').to_date_string() }}",
            "--anchor", "{{ dag_run.conf.get('anchor', '') if dag_run else '' }}",
            "--wait-for-ready",
            "--wait-timeout-seconds", "28800",
            "--batch-size", "5",
            "--concurrency", "2",
            "--max-http-attempts", "500",
        ],
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

