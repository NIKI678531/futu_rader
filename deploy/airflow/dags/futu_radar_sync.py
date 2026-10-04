"""Synchronize one exact, complete MarketInsight collection receipt.

The Dataset event is only a wake-up signal.  Its ``source_run_id`` is required
and is forwarded to the worker, where the receipt and completeness proof are
checked again before the Radar anchor can move.  Manual admin triggers use the
same field in ``dag_run.conf`` and therefore have identical locking and retry
semantics.
"""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.datasets import Dataset
from airflow.models import Variable
from airflow.operators.python import PythonOperator
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from kubernetes.client import models as k8s


DAG_ID = "futu_radar_sync"
DATASET_URI = "market-insight://futu-community"
TIMEZONE = pendulum.timezone("Asia/Hong_Kong")
PLACEHOLDER_DIGEST_IMAGE = "configure-me@sha256:" + ("0" * 64)
UPSTREAM_DATASET = Dataset(DATASET_URI)


def _variable(name: str, default: str) -> str:
    value = str(Variable.get(name, default_var=default)).strip()
    return value or default


def resolve_source_run(**context):
    """Return one exact receipt id or fail before a worker pod is created."""

    dag_run = context.get("dag_run")
    conf = getattr(dag_run, "conf", None) or {}
    candidates = []
    if conf.get("sourceRunId"):
        candidates.append(str(conf["sourceRunId"]).strip())

    for dataset, events in (context.get("triggering_dataset_events") or {}).items():
        if getattr(dataset, "uri", None) != DATASET_URI:
            continue
        for event in events or []:
            extra = getattr(event, "extra", None) or {}
            if extra.get("source_run_id"):
                candidates.append(str(extra["source_run_id"]).strip())

    unique = sorted(set(filter(None, candidates)))
    if len(unique) != 1:
        raise ValueError(
            "futu_radar_sync requires exactly one source_run_id from the "
            "Dataset event or dag_run.conf.sourceRunId"
        )
    airflow_run_id = str(getattr(dag_run, "run_id", "unknown"))
    digest = hashlib.sha256(f"{unique[0]}|{airflow_run_id}".encode("utf-8")).hexdigest()
    return {"sourceRunId": unique[0], "radarRunId": f"airflow-{digest[:32]}"}


worker_image = _variable("futu_radar_worker_image", PLACEHOLDER_DIGEST_IMAGE)
if re.fullmatch(r"[^\s@]+@sha256:[0-9a-fA-F]{64}", worker_image) is None:
    raise ValueError("Airflow Variable futu_radar_worker_image must pin an image digest")

namespace = _variable("futu_radar_namespace", "default")
service_account = _variable("futu_radar_service_account", "default")
runtime_secret = _variable("futu_radar_runtime_secret", "futu-radar-runtime")


with DAG(
    dag_id=DAG_ID,
    description="Sync one exact complete MarketInsight comments_all receipt",
    schedule=[UPSTREAM_DATASET],
    start_date=pendulum.datetime(2026, 1, 1, tz=TIMEZONE),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    tags=["futu-radar", "sync", "production"],
) as dag:
    resolve = PythonOperator(
        task_id="resolve_source_run",
        python_callable=resolve_source_run,
    )

    sync = KubernetesPodOperator(
        task_id="sync",
        name="futu-radar-sync",
        namespace=namespace,
        service_account_name=service_account,
        image=worker_image,
        image_pull_policy="IfNotPresent",
        cmds=["python", "-X", "utf8", "-m", "jobs.refresh"],
        arguments=[
            "sync",
            "--mode",
            "incremental",
            "--run-id",
            "{{ ti.xcom_pull(task_ids='resolve_source_run')['radarRunId'] }}",
            "--through-source-run-id",
            "{{ ti.xcom_pull(task_ids='resolve_source_run')['sourceRunId'] }}",
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
    )

    resolve >> sync

