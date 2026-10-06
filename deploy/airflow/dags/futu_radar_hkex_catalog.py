"""Daily HKEX official ETP catalog discovery at 07:00 Hong Kong time."""

from datetime import timedelta
import re

import pendulum
from airflow import DAG
from airflow.models import Variable
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from kubernetes.client import models as k8s

DAG_ID = "futu_radar_hkex_catalog"
TIMEZONE = pendulum.timezone("Asia/Hong_Kong")
PLACEHOLDER_DIGEST_IMAGE = "configure-me@sha256:" + ("0" * 64)


def _variable(name, default):
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
    description="Discover and stage products from the official HKEX ETP catalog",
    schedule="0 7 * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz=TIMEZONE),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=10)},
    tags=["futu-radar", "hkex", "catalog", "production"],
) as dag:
    sync_catalog = KubernetesPodOperator(
        task_id="sync_hkex_catalog",
        name="futu-radar-hkex-catalog",
        namespace=namespace,
        service_account_name=service_account,
        image=worker_image,
        image_pull_policy="IfNotPresent",
        cmds=["python", "-X", "utf8", "-m", "jobs.hkex_catalog"],
        arguments=["sync"],
        env_from=[k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name=runtime_secret))],
        get_logs=True,
        do_xcom_push=False,
        is_delete_operator_pod=True,
        startup_timeout_seconds=600,
    )
