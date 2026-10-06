# Futu Radar production Airflow DAGs

## HKEX product catalog

`futu_radar_hkex_catalog` runs daily at 07:00 Asia/Hong_Kong. It reads the
official HKEX ETP export and stages CSOP/exact-peer additions. New symbols are
not active until a later `comments_all` receipt proves the complete desired
symbol-set fingerprint; ambiguous matches stay in the admin review queue.
Source failures retain the previous active catalog and never delete products.

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

`futu_radar_ai_analysis` runs every day at 08:00 Asia/Hong_Kong. Monday's
`auto` mode covers every configured range; the other days cover
`d1,d2,d7`. Completed input fingerprints are reused, so a scheduled run only
sends model requests for missing results or inputs changed by synchronization.

For the one-time `comment-product-v4` cutover, run all active products with
`--ranges d1,d2,d7,d14,d30,mtd --current-window-only`. This intentionally
reprocesses no more than the latest 30 days. The previous d30 comparison stays
unavailable until a full subsequent baseline has accumulated; v2/v3 rows remain
auditable but are excluded once `product_feedback_taxonomy=product-feedback-v4`
is published after the complete run.
The Airflow logical date is passed as the Hong Kong budget date, while
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

All DAGs use the same digest-pinned image and runtime Secret. The Secret is
injected with `envFrom` and must contain
`RADAR_DB_URL` (the same current database used by the web backend),
`MARKET_INSIGHT_DATABASE_URL`, the AI credentials,
`AI_DATA_GOVERNANCE_APPROVED=true`, `AI_CALIBRATION_REPORT`, and
`AI_QUALITY_REPORT`. Do not store those values in Airflow Variables. Keep the
DAG paused until the calibration and 400-row human-gold reports were generated
for the active `commentRouteVersion` and `catalogPolicyVersion`. A compliant
additive catalog change only needs the new-product identity regression and
complete route proof; it does not invalidate the whole 400-row gold set.

## Admin API

The Flask backend only needs the same `RADAR_DB_URL` as the worker. Manual
analysis requests are durable database rows claimed by the always-on worker;
the backend has no Airflow or MarketInsight credential. These DAGs remain
independent scheduled assets and do not implement the `/admin` buttons.

Example Secret key inventories live in
`deploy/kubernetes/futu-radar-secrets.example.yaml`.

