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

## Daily full refresh (08:00)

One entry point keeps every page complete
([ADR-0031](adr/0031-daily-full-refresh-single-queue.md)):

```powershell
Set-Location worker
.venv\Scripts\python -X utf8 -m jobs.daily --now           # enqueue; the running worker executes
.venv\Scripts\python -X utf8 -m jobs.daily --now --drain   # also execute here until the queue is empty
```

The always-on worker (`python scheduler.py`) runs the same function at
`AI_DAILY_CRON` (default `0 8 * * *`, Hong Kong time; `off` disables it):

1. **Sync** when `MARKET_INSIGHT_DATABASE_URL` is set: the latest successful
   `comments_all` receipt for today or the previous evening is passed as
   `--through-source-run-id`, so the complete date (`meta_kv.anchor`) advances.
   Without the URL the database is the imported dump and the step is skipped.
2. **Prices** when `FMP_API_KEY` is set.
3. **Analysis**: `d1,d2,d7,d14,d30,mtd` are enqueued as scheduled requests and
   executed by the worker for every active product (`AI_OWN_TOP_LIMIT=N`
   restricts own products to the comment-volume Top N plus every peer).

A failed sync or price step never blocks analysis of the facts already in Radar.
The step report is stored in `meta_kv.ai_daily_last_run` and shown on `/admin`.
Each provider HTTP attempt still reserves a row in the Hong Kong day counter;
the default limit is 100000 per day (`AI_MANUAL_MAX_HTTP_REQUESTS`).
Calibration and human-gold reports are offline tools, not run gates.
`AI_DATA_GOVERNANCE_APPROVED` defaults to on; set it to `false` to stop every AI
request.

Layer B (page summaries, theme names, stage opinions, competitor reasons) needs a
model that can write text. When Layer A uses TypeSafe System One, configure
`AI_SYNTH_BASE_URL`, `AI_SYNTH_API_KEY` and `AI_SYNTH_MODEL` (OpenAI compatible).

`jobs.refresh analyze` remains available for explicit `--codes/--ranges`
backfills and uses the same selection and run policy.

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

The independent `/admin` route reads Radar only:

- **链路检查**: worker heartbeat, Layer A model, Layer B generation model,
  comment attribution, anchor, data mode (dump vs production) and the daily job.
- **页面数据完整度**: for each of `d1,d2,d7,d14,d30,mtd`, how many products have
  every Layer B kind published for the current anchor, how many are served as
  「待更新」, and which are missing; each range has its own fill button, and
  「一键补齐全部」 enqueues all six.
- **评论进入**: the per-product comment funnel (the exact `/pool` counts).
- **AI 分析**: Layer A queue counts and Layer B per kind (generated / stale /
  missing) for the selected range.
- **最近运行**: manual and scheduled requests with their summary, remaining
  gaps, errors and reasons.

Buttons insert rows into `ai_analysis_requests`; the always-on worker claims them
in range priority order. Duplicate active requests for one range return the
existing row. Buttons are disabled while no ready worker heartbeat exists, so a
request cannot wait forever. The backend needs only `RADAR_DB_URL`; it does not
connect to MarketInsight or Airflow and holds no AI credentials. Stable input
fingerprints ensure unchanged results are reused and a repeat run can make zero
model requests.

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

Calibration and human-gold reports remain useful offline checks after a
routing-policy change, but they no longer gate analysis (ADR-0031).

In Kubernetes, deploy `deploy/airflow/dags/futu_radar_ai_analysis.py` (it runs
`jobs.daily --now --drain`) and configure the four non-secret Airflow Variables
described in `deploy/airflow/README.md`. Database and AI credentials must be
fields of the configured Kubernetes Secret, and `RADAR_DB_URL` must be the same
database used by the web backend. Backend providers refresh metadata on each
request, so the page and AI share one current window. Enable either this DAG or
the worker's `AI_DAILY_CRON`, not both. The DAG is paused on first discovery;
unpause it after one successful manual run.
