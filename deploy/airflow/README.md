# Futu Radar production Airflow DAGs

## Fact synchronization

`futu_radar_sync` subscribes to the Airflow Dataset
`market-insight://futu-community`. It starts paused, permits only one active
run, and retries failures twice. The producer must publish the Dataset only
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

## AI analysis

`futu_radar_ai_analysis` runs at 20:30 Asia/Hong_Kong on weekdays. Monday's
`auto` mode covers every configured range; Tuesday through Friday cover
`d1,d2`. The Airflow logical date is passed as the Hong Kong budget date, while
the analysis window itself is always built from the current Radar database's
latest complete `meta_kv.anchor`. The task waits for that day's 19:30
`comments_all` run to be successfully synchronized first. The DAG is paused
when first discovered.

Configure these Airflow Variables before unpausing:

- `futu_radar_worker_image`: immutable worker image reference containing
  `@sha256:<64 hex chars>`
- `futu_radar_namespace`: Kubernetes namespace
- `futu_radar_service_account`: pod service account
- `futu_radar_runtime_secret`: Kubernetes Secret name

Both DAGs use the same digest-pinned image and runtime Secret. The Secret is
injected with `envFrom` and must contain
`RADAR_DB_URL` (the same current database used by the web backend),
`MARKET_INSIGHT_DATABASE_URL`, the AI credentials,
`AI_DATA_GOVERNANCE_APPROVED=true`, `AI_CALIBRATION_REPORT`, and
`AI_QUALITY_REPORT`. Do not store those values in Airflow Variables. Keep the
DAG paused until the calibration and 400-row human-gold reports were generated
for the active `commentRouteVersion` and `productPoolDigest`.

## Admin API

The Flask backend needs `ADMIN_API_TOKEN`, `AIRFLOW_API_BASE_URL` and
`AIRFLOW_API_TOKEN` in its server-side Secret. The Airflow token should only be
allowed to read the two DAGs and create DAG runs. The browser never receives
the Airflow URL or token. `/admin` stores the independently entered admin token
in `sessionStorage`, so closing the tab clears it.

Example Secret key inventories live in
`deploy/kubernetes/futu-radar-secrets.example.yaml`. Keep the Airflow REST
credential in the backend-only Secret; worker pods do not need it.

