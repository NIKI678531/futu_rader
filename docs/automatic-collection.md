# MarketInsight database synchronization

Futu Radar does not scrape the community site. The existing Airflow project
collects into the `market_insight` MySQL database; the Radar worker connects to
that database with a read-only account and materializes the application facts.

The durable decisions behind this flow are recorded in
[ADR-0024](adr/0024-airflow-source-boundary-and-dataset-sync.md),
[ADR-0025](adr/0025-online-fact-reconciliation-and-comment-coverage.md),
[ADR-0026](adr/0026-scheduled-ai-budget-and-calibration-gate.md), and
[ADR-0027](adr/0027-sqlite-to-mysql-cutover-and-recovery.md).

```text
Airflow collectors -> market_insight -> jobs.refresh sync -> futu_radar
                                                     \-> jobs.refresh analyze
```

`futu_api_check` is deliberately outside this flow. It checks OpenD quotes, not
the community website or its browser cookie.

## Runtime configuration

The worker accepts credentials only through environment variables. In
production these values must be injected by the Airflow/Kubernetes Secret:

```dotenv
MARKET_INSIGHT_DATABASE_URL=mysql+pymysql://readonly:...@source/market_insight
RADAR_DB_URL=mysql+pymysql://writer:...@target/futu_radar
```

Run an incremental synchronization locally:

```powershell
Set-Location worker
.venv\Scripts\python -X utf8 -m jobs.refresh sync `
  --run-id manual-20260925 `
  --through-source-run-id futu_all_comments_scrape:scheduled__2026-09-25T11:30:00+00:00
```

The `--run-id` is an idempotency key. Retrying the same Airflow run must pass the
same value. `--dry-run` reads and validates source pages without changing facts,
checkpoints or ingestion runs. Dataset-triggered production runs also pass the exact
source receipt id with `--through-source-run-id`; the Dataset event is only a wake-up
signal and never replaces the database checkpoint.

After the third-party service and Airflow have refreshed specific source rows,
repeatable `--feed-id` arguments may be used for a bounded re-read. This option
only reads the matching rows from MarketInsight; Radar does not call Futu or any
community HTTP API. It cannot recover comments that are absent from the source
database, so an upstream `partial` snapshot remains `partial` until the
third-party/Airflow pipeline writes a complete replacement.

Daily AI uses a persistent Hong Kong calendar-day budget:

```powershell
.venv\Scripts\python -X utf8 -m jobs.refresh analyze `
  --mode daily `
  --budget-date 2026-09-25 `
  --ranges d1 `
  --max-http-attempts 500 `
  --batch-size 5 `
  --concurrency 2 `
  --calibration-report C:\secure\calibration.json `
  --quality-report C:\secure\gold-eval-v3.json
```

Use `--mode weekly` on Monday to include `d7,d14,d30,mtd`. Every provider HTTP
attempt, including retries, reserves one row-level database allowance before the
request. `--budget-date` identifies the scheduled analysis day; the allowance itself
is always charged to the actual Hong Kong day of each HTTP attempt, including when a
long run crosses midnight. Re-running the task cannot reset either day's limit.
In Kubernetes, put the complete validated report JSON in the
`AI_CALIBRATION_REPORT` Secret value. Local runs may instead use the file-path CLI
option shown above. Put the independently generated human-gold report in
`AI_QUALITY_REPORT` (or pass `--quality-report`). The latter must contain at least
400 directly evaluated LLM rows from `comment-product-v3`, relevant-class precision
at least 95%, recall at least 90%, and no failed complaint regression. Both reports
must match the current model, prompt, schema and taxonomy. Counts are recomputed from
the confusion matrix; a stale, edited or policy-mismatched report stops before the
first paid request.

Backfills are staged by rerunning this command with the same persistent daily
budget: full-pool `d1` first, then `d2`, then `d7`; longer ranges only start after
those page ranges are complete. A retry does not reset the HKT-day allowance.

The same runtime Secret must set `AI_DATA_GOVERNANCE_APPROVED=true` only after the
operator has confirmed the processing region, log retention, training-use and
deletion-policy questions recorded in ADR-0017/0019. Missing, false or malformed
approval fails closed before calibration and before the first provider call; it is a
data-export authorization, not a human review gate for generated conclusions.

## Initial SQLite to MySQL migration

Stop the current backend/worker writers first, then create and retain a byte-for-byte
backup plus SHA-256 manifest. The backup command flushes SQLite's WAL and holds an
exclusive lock while copying; stopping writers is still required so the cutover has
an explicit freeze point:

```powershell
$env:SOURCE_RADAR_DB_URL = 'sqlite:///C:/Users/<user>/AppData/Local/futu-radar/radar.db'
$env:RADAR_BACKUP_DIR = 'D:/secure-backups/futu-radar'
Set-Location worker
.venv\Scripts\python -X utf8 -m jobs.backup_sqlite
```

Create an empty MySQL database and migrate it to the current Alembic head first:

```powershell
$env:RADAR_DB_URL = $env:RADAR_TARGET_DB_URL
worker\.venv\Scripts\python -m alembic -c radar_db\alembic.ini upgrade head
```

Then stream the local history into it. The target URL is intentionally not a CLI
argument so it does not appear in shell history or process listings:

```powershell
$env:SOURCE_RADAR_DB_URL = 'sqlite:///C:/Users/<user>/AppData/Local/futu-radar/radar.db'
$env:RADAR_TARGET_DB_URL = 'mysql+pymysql://.../futu_radar?charset=utf8mb4'
Set-Location worker
.venv\Scripts\python -X utf8 -m jobs.migrate_sqlite_to_mysql --batch-size 500
```

The command requires an empty target, copies only durable tables, releases stale
AI claims, compares every table count and compares deterministic samples. Use
`--resume` only after an interrupted copy. Keep the SQLite file as a read-only
rollback backup until source reconciliation and Radar API smoke tests pass.

Before cutover, run a source reconciliation in shadow mode and then publish it:

```powershell
.venv\Scripts\python -X utf8 -m jobs.refresh sync --mode backfill --dry-run `
  --run-id initial-reconciliation-check
.venv\Scripts\python -X utf8 -m jobs.refresh sync --mode backfill `
  --run-id initial-reconciliation
```

Compare the migration report counts/hashes, smoke-test `/api/v1/meta` and product
endpoints against MySQL, then switch Flask, the worker and Airflow together. Keep
the SQLite copy until the shadow results and the first Dataset-triggered sync agree.

## Parent-feed comment-filter rollout

Alembic `0011` adds `feeds.source_ticker` and `feed_mentions`. Deploy the schema
and normal ingestion dual-write before changing product reads. Then run the
historical pass in bounded batches; the saved cursor makes `--resume` safe after an
interruption:

```powershell
$env:RADAR_DB_URL = $env:RADAR_TARGET_DB_URL
worker\.venv\Scripts\python -m alembic -c radar_db\alembic.ini upgrade head
Set-Location worker
.venv\Scripts\python -X utf8 -m jobs.backfill_comment_filter --dry-run --batch-size 500
.venv\Scripts\python -X utf8 -m jobs.backfill_comment_filter --batch-size 500
.venv\Scripts\python -X utf8 -m jobs.backfill_comment_filter --resume --activate --batch-size 500
```

Archive the JSON output from the dry run and activated pass. It reports, per
product, raw parent posts, qualified parent posts, raw platform comments, filtered
platform comments, parsed replies and unresolved source tickers. Activation is
refused while any source ticker is unresolved. It also binds readiness to the
version and SHA-256 digest of `radar_db/comment_filters.json`, supersedes stale
cross-product/ineligible jobs, advances data/AI revisions and marks synthesis dirty.

Until activation, product metrics deliberately return unavailable; global collection
metadata and account-domain pages continue to expose raw ingestion facts. After
activation run the existing extraction/analysis workflow to enqueue only newly
qualified comment/product units, then refresh synthesis and API caches. A mutating
comment extraction also checks the same readiness marker and fails closed, so it
cannot publish against a half-backfilled database.

## Data guarantees

- Source cursors are `(source timestamp, stable id)` keysets with a fixed
  high-watermark per run.
- A source page and its target checkpoint commit in one transaction.
- Replaying a source or Dataset event is safe and does not duplicate facts.
- Missing comments in a partial snapshot never delete previously observed rows.
- `feeds.comment_count` remains the platform counter; product metrics sum it only
  across qualified parent feeds. `comments` contains only the bodies actually
  available to AI, and replies never create parent `feed_mentions`.
- Counter observations are append-only. Before a post is 24 hours old the latest
  value is provisional; afterwards the first observation is frozen. A historical
  correction requires an explicit `sync --mode repair` run.
- Only a successful `comments_all` source run may advance the complete HKT date.
- Fact publication never waits for or rolls back because of AI work.

Operational state is available through `python -m jobs.refresh status` and the
additive `/api/v1/meta.dataCollection` response.

## Admin control plane

The independent `/admin` route presents the same chain in three plain-language
steps: upstream receipt, Radar synchronization/checkpoint, and AI progress/budget.
Configure the Flask deployment with `ADMIN_API_TOKEN`, the read-only
`MARKET_INSIGHT_DATABASE_URL`, `AIRFLOW_API_BASE_URL`, and a narrowly scoped
`AIRFLOW_API_TOKEN`. The five business pages do not depend on Airflow: when its API
is down, `/admin` becomes read-only and the business API keeps serving the last
committed facts.

The two buttons call only the fixed `futu_radar_sync` and
`futu_radar_ai_analysis` DAG IDs. The sync button re-reads the latest verified
receipt immediately before submission and sends its exact `sourceRunId`. The AI
button is disabled until the route generation is active, the target anchor is
complete and current, governance is approved, and both release reports match the
active model, Prompt, schema, taxonomy and product-pool digest. Airflow and the
worker enforce the same gates again; `/admin` never accepts shell commands,
historical backfill ranges, repair mode, or a budget override.

## Comment AI routing and scheduled analysis

After Alembic `0012`, populate strict content-cashtag routes before allowing any
automatic model call:

```powershell
$env:RADAR_DB_URL = $env:RADAR_TARGET_DB_URL
Set-Location worker
.venv\Scripts\python -X utf8 -m jobs.backfill_comment_routes --dry-run --batch-size 500
.venv\Scripts\python -X utf8 -m jobs.backfill_comment_routes --batch-size 500
.venv\Scripts\python -X utf8 -m jobs.backfill_comment_routes --resume --activate --batch-size 500
```

The first pass is an audit only. The second is resumable and keeps readiness
closed. The final command atomically publishes the route version and product-pool
digest, supersedes ineligible comment tasks, invalidates the previous quality
claim and marks Layer B dirty. `/api/v1/meta.aiCommentRouting` exposes the active
state without changing business endpoint shapes.

Generate a fresh batch-5 calibration report and a fresh 400-row LLM-only human
gold report after activation. Both artifacts contain `commentRouteVersion` and
`productPoolDigest`; automatic analysis rejects artifacts from the previous
candidate universe.

Deploy `deploy/airflow/dags/futu_radar_ai_analysis.py` and configure the four
non-secret Airflow Variables described in `deploy/airflow/README.md`. Database
and AI credentials, governance approval, calibration JSON and gold-report JSON
must be fields of the configured Kubernetes Secret. `RADAR_DB_URL` must be the
same current database used by the web backend. After the 19:30 sync proof is
visible there, the worker reads the latest `meta_kv.anchor`; backend providers
refresh that metadata on each request, so the page and AI share one current
window. The DAG is intentionally paused on first discovery; unpause it only
after one successful manual run.
