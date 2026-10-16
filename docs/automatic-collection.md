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

## Read-only source preflight

Before changing the fact-reading path, check the source without starting a sync,
creating application tables, or running AI:

```powershell
Set-Location worker
.venv\Scripts\python -X utf8 -m jobs.refresh check-source --codes 3033,2802 --sample-size 25
```

The CLI loads `worker/.env` without overriding injected environment variables.
It connects only to `MARKET_INSIGHT_DATABASE_URL`; no application connection is
opened by `check-source`. MySQL source sessions default to read-only transactions
and the source engine rejects non-read statements. Use a database account with
SELECT-only grants as the security boundary, and configure verified TLS in the
deployment connection settings. The application URL must point to a different
database/schema. Both `make_engine()` and Alembic reject matching source and
application database identities before connecting. This configuration check
cannot resolve host aliases; distinct credentials and database grants remain
mandatory. An existing SQLite snapshot can also be checked using a `sqlite:///`
source URL; it is opened in file-level read-only mode and is never created.

The report includes reflected columns, primary keys, indexes, bounded per-product
sample counts, JSON parsing failures, null-field counts and coverage states. It
does not include connection credentials, comment bodies or user profiles.
`--sample-size` is limited to 1-100 feeds per requested product. Missing required
tables/columns, invalid samples or absent verified complete-day receipts produce
exit code 2. Exit code 0 means only `sample_checked`, not deployment approval:
`productionReady` is always false. Missing incremental indexes are reported
separately, and partial comment snapshots remain partial. Account permissions,
source time zones, full-dataset completeness, same-snapshot equivalence and the
backend/AI cutover still need separate verification. Do not add columns or indexes
to the production source automatically to satisfy this check.

The 2026-08-26 MarketInsight export inspected on 2026-10-08 defines these mappings:

| Application field | Source field or existing parser path |
| --- | --- |
| Parent ID, type and time | `futu_comments_feeds.feed_id`, `feed_type`, `posted_at` |
| Discussion product | `stock_id` joined to `futu_comments_stocks.stock_id/ticker` |
| Parent title and body | `feed_title`, `content_text` |
| Parent author | `author_uid`, `author_name`; existing JSON fallback when absent |
| Platform interaction counts | `like_count`, `comment_count`, `image_count` |
| Detail and observation cursors | `detail_updated_at`, `scraped_at` |
| Reply bodies | Existing parser: `raw_json.comment.comment_items` |
| User profile | `futu_comments_users`, linked by author UID |

The export's 14 feed columns match the existing source adapter. It contains the
`(scraped_at, feed_id)` feed index, but not the adapter's
`(detail_updated_at, feed_id)` and `(scraped_at, user_id)` incremental indexes.
DDL confirms field names and types, not JSON payload completeness or the state of
the current production database. `comment_count` is a platform total, not the
number of reply bodies available to AI.

This preflight checks the online three-table synchronization contract. A
feed-only SQLite export is supported by the explicit direct-read mode below,
even when this synchronization preflight reports missing stocks/users tables.
Checking the source alone does not select a different business data path.

## Opt-in SQLite snapshot direct reads

`RADAR_FACT_SOURCE=radar` is the default. Existing backend/worker configuration,
the default `radar.db`, and running pages remain unchanged. Merely setting the
source URL does not enable the new mode. `RADAR_FACT_SOURCE=market_insight`
requires an explicitly prepared, separate SQLite application state file.

The shared connection factory attaches the MarketInsight snapshot read-only and
exposes connection-local `feeds/comments/users` views. These views read source
columns and expand reply JSON using the same Python normalization as collection.
This preserves the existing page, evidence, AI candidate, nested reply context,
claim, annotation publication and synthesis SQL contracts without copying source
text into the application facts. Application `feeds/comments` tables remain empty.
Only stock identity mappings, reply-to-feed locators, deterministic mention/route
indexes, metadata and generated analysis results are persisted in the new state.

The user-provided `market_insight.db` is a feed-only SQLite conversion: it has
`futu_comments_feeds`, dates stored as TEXT, and no stocks or users tables. A
verified `stock_id -> ticker` mapping is therefore mandatory. Use the
`futu_comments_stocks` records in the matching SQL export, or a reviewed JSON
object of stock IDs and tickers. Do not infer the discussion product from a
cashtag appearing in a post. Missing extended user profile fields remain null.

Run preparation in a **separate terminal**, using a new application file outside
the repository. The command reads injected environment variables and does not
load or edit the daily service's `.env` files:

```powershell
$env:MARKET_INSIGHT_DATABASE_URL = 'sqlite:///C:/path/to/market_insight.db'
$env:RADAR_DB_URL = 'sqlite:///C:/path/to/new-radar-source-state.db'
Set-Location worker
.venv\Scripts\python -X utf8 -m jobs.source_state `
  --stock-dump 'C:/path/to/matching-market-insight.sql'
```

Alternatively use `--stock-catalog 'C:/path/to/stock-identities.json'`. Optional
`--since '2026-08-01 00:00:00'` limits indexing by UTC parent timestamp; choose a
bound covering all requested current **and baseline** windows. Pages requiring
earlier facts remain unavailable, not zero. The full active product pool is used;
the stock catalog may also contain unrelated instruments.

`--complete-through YYYY-MM-DD` is an explicit operator assertion of an already
verified complete HKT day. It must not exceed the observed source date. It is
**not** inferred from the last post, copied from the current Radar database, or
set automatically. Without a verified complete day, the source can still be
indexed, queried and used for explicit bounded extraction, but date presets that
require a complete anchor remain unavailable. This is not a connection error.

After preparation, enable the isolated instance in that terminal only:

```powershell
$env:RADAR_FACT_SOURCE = 'market_insight'
$env:DATA_PROVIDER = 'sql'
$env:APP_ENV = 'production'
$env:APP_HOST = '127.0.0.1'
$env:APP_PORT = '8018'  # use another unused port if occupied
Set-Location ..\backend
.venv\Scripts\python -X utf8 app.py
```

An independently started worker must receive the same three database settings.
It writes jobs, annotations, evidence and synthesis only to the new application
state. Its daily source step validates the prepared snapshot instead of copying
facts. Do not start a worker with real model credentials during connection tests;
set `AI_DATA_GOVERNANCE_APPROVED=false`, `AI_DAILY_CRON=off` and
`AI_INTRADAY_CRON=off` for validation. No existing service needs to be stopped or
restarted to test this mode. Source initialization and legacy migrations are
separate; automatic legacy migration is disabled for the direct-read state.

Snapshot mutation is fail-closed: changes to the source database or WAL detected
on checkout or execution reject reads instead of mixing new bodies with old
indexes/AI outputs. Stop the isolated instance before preparation. If a changed
snapshot already has AI results in its state, prepare a new state file; historical
results remain in the previous one. Changing the indexed window likewise requires
a new state file. This release is for stable SQLite snapshots, not live source
change reconciliation. Filesystem change detection is not a cryptographic proof
against a writer deliberately preserving timestamps.

Verified locally against the supplied snapshot: the UTC window starting
2026-08-24 contained 10,971 parent feeds and 6,692 parsed replies in active
products, with five broken JSON payloads retained as unavailable. Source body
copies in the independent state were zero. Automated tests also cover isolated
AI extraction, mock annotation, mock synthesis, evidence reads and five-page
API contracts. No real AI run or current-page cutover was performed.

**Production limitation:** direct-read TEMP views currently support SQLite source
and SQLite state only. MySQL URLs continue to work with the existing read-only
synchronization adapter, but MySQL direct-read federation and live incremental
index reconciliation are not implemented by this snapshot mode. Do not label
this as a verified MySQL production migration. MySQL must not be silently routed
through SQLite or the old Radar facts when direct mode is requested.

## Incremental synchronization

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
   `comments_all` receipt for today or the previous evening is used when the
   optional receipt table exists. Without that table, Radar synchronizes the
   non-empty intersection of its product pool and the source catalog, publishes
   an `observed_partial` anchor from the latest already-ended HKT day containing
   observed facts, and leaves `sourceCompleteThrough` unverified. Without the
   URL the database is the imported dump and the step is skipped.
2. **Prices** when `FMP_API_KEY` is set.
3. **Analysis**: `d7,d1,d2` are enqueued as scheduled requests, in that priority
   order, and executed by the worker for every active product. `d14,d30,mtd`
   are started manually, one range at a time, from the admin page.
   (`AI_OWN_TOP_LIMIT=N` restricts own products to the comment-volume Top N plus
   every peer.)

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
- `feeds.comment_count` remains the platform reply counter for audit and coverage.
  Product metrics count strict-tagged parent feeds once each. `comments` contains
  only the bodies actually available to AI, and replies never create parent
  `feed_mentions`.
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
