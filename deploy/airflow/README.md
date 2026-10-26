# Futu Radar production Airflow DAGs

## HKEX product catalog

`futu_radar_hkex_catalog` runs daily at 07:00 Asia/Hong_Kong. It reads the
official HKEX ETP export. Every product in a complete snapshot becomes active;
products absent from that complete snapshot become inactive. Short, malformed,
or sharply reduced responses fail closed and retain the previous catalog.

Production Kubernetes already runs the same 07:00 job in its worker scheduler.
Keep this Airflow DAG paused there; enable only one scheduler owner.

## Fact synchronization

`futu_radar_sync` subscribes to the Airflow Dataset
`market-insight://futu-community`. It starts paused, permits only one active
run, and retries failures twice. This exact-replay DAG is optional; the
always-on worker can use intersection/observed-partial synchronization when
MarketInsight has no receipt table. If this DAG is enabled, the producer must
publish the Dataset only
after committing a successful, complete `comments_all` receipt to
`futu_comments_collection_runs`, with this event metadata:

```python
community = Dataset("market-insight://futu-community")

@task(outlets=[community])
def publish_receipt(collection_run_id: str, *, outlet_events):
    # Run this only after the successful receipt transaction has committed.
    outlet_events[community].extra = {"source_run_id": collection_run_id}
```

The exact API for attaching event metadata depends on the installed Airflow
version; the resulting Dataset event must expose
`event.extra["source_run_id"]`. A missing or ambiguous id fails before a pod is
started. The worker then runs:

```text
python -m jobs.refresh sync --mode incremental \
  --run-id <stable airflow-derived id> \
  --through-source-run-id <exact upstream receipt id>
```

Before unpausing this DAG, run the same image once with `--dry-run`, compare
counts and `completeThrough`, then perform one manually observed real sync.

## Daily refresh and AI analysis

`futu_radar_ai_analysis` runs every day at 08:00 Asia/Hong_Kong and executes
`python -m jobs.daily --now --drain` ([ADR-0031](../../docs/adr/0031-daily-full-refresh-single-queue.md)):

1. sync MarketInsight through the latest completed `comments_all` receipt when
   available; otherwise the always-on worker path publishes an explicitly
   unverified observed-partial anchor for the available product intersection;
2. sync prices when `FMP_API_KEY` is present;
3. enqueue `d1,d2,d7,d14,d30,mtd` (`requested_by=schedule`) and execute the
   queue inside the pod until it is empty.

The pod never waits for the same day's 19:30 run (the previous design timed
out at 16:00 every day). A failed sync does not block analysis of the facts
already in Radar. Completed input fingerprints are reused, so a scheduled run
only sends model requests for missing results or inputs changed by the sync.
Each run and its result are visible on `/admin` under 最近运行 as 定时.

Use either this DAG or the always-on worker's own `AI_DAILY_CRON` (default
`0 8 * * *`), not both: set `AI_DAILY_CRON=off` on the worker when this DAG is
unpaused. If both fire anyway, active ranges are de-duplicated in the queue and
the analysis lease keeps a single executor. The DAG is paused when first
discovered.

Configure these Airflow Variables before unpausing:

- `futu_radar_worker_image`: immutable worker image reference containing
  `@sha256:<64 hex chars>`
- `futu_radar_namespace`: Kubernetes namespace
- `futu_radar_service_account`: pod service account
- `futu_radar_runtime_secret`: Kubernetes Secret name

All DAGs use the same digest-pinned image and runtime Secret. The Secret is
injected with `envFrom` and must contain
`RADAR_DB_URL` (the same current database used by the web backend),
`MARKET_INSIGHT_DATABASE_URL`, the Layer A credentials (`AI_PRIMARY_*`) and,
when Layer A is a classification-only provider such as TypeSafe, the Layer B
generation model (`AI_SYNTH_BASE_URL`, `AI_SYNTH_API_KEY`, `AI_SYNTH_MODEL`).
Do not store those values in Airflow Variables. Calibration and human-gold
reports are no longer run gates; `AI_DATA_GOVERNANCE_APPROVED=false` remains
the switch that stops all AI requests.

## Admin API

The Flask backend only needs the same `RADAR_DB_URL` as the worker. Manual
analysis requests and the daily job's requests are durable rows in the same
`ai_analysis_requests` queue; the backend has no Airflow or MarketInsight
credential.

Example Secret key inventories live in
`deploy/kubernetes/futu-radar-secrets.example.yaml`.

