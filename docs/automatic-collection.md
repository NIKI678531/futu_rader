# Automatic Futu collection

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

Daily AI uses a persistent Hong Kong calendar-day budget:

```powershell
.venv\Scripts\python -X utf8 -m jobs.refresh analyze `
  --mode daily `
  --budget-date 2026-09-25 `
  --max-http-attempts 500 `
  --batch-size 5 `
  --concurrency 2 `
  --calibration-report C:\secure\calibration.json
```

Use `--mode weekly` on Monday to include `d7,d14,d30,mtd`. Every provider HTTP
attempt, including retries, reserves one row-level database allowance before the
request. `--budget-date` identifies the scheduled analysis day; the allowance itself
is always charged to the actual Hong Kong day of each HTTP attempt, including when a
long run crosses midnight. Re-running the task cannot reset either day's limit.
In Kubernetes, put the complete validated report JSON in the
`AI_CALIBRATION_REPORT` Secret value. Local runs may instead use the file-path CLI
option shown above. A missing, invalid, or policy-mismatched report stops before the
first paid request.

The same runtime Secret must set `AI_DATA_GOVERNANCE_APPROVED=true` only after the
operator has confirmed the processing region, log retention, training-use and
deletion-policy questions recorded in ADR-0017/0019. Missing, false or malformed
approval fails closed before calibration and before the first provider call; it is a
data-export authorization, not a human review gate for generated conclusions.

## Initial SQLite to MySQL migration

Stop the current backend/worker writers first, then make and retain a byte-for-byte
backup of the SQLite file (for example with PowerShell `Copy-Item -LiteralPath ...`).
Record its size and SHA-256 before continuing; do not migrate from a live file that
can still be written.

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
rollback backup until source reconciliation and API smoke tests pass.

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

## Data guarantees

- Source cursors are `(source timestamp, stable id)` keysets with a fixed
  high-watermark per run.
- A source page and its target checkpoint commit in one transaction.
- Replaying a source or Dataset event is safe and does not duplicate facts.
- Missing comments in a partial snapshot never delete previously observed rows.
- `feeds.comment_count` remains the platform count; `comments` contains only the
  bodies actually available to AI.
- Counter observations are append-only. Before a post is 24 hours old the latest
  value is provisional; afterwards the first observation is frozen. A historical
  correction requires an explicit `sync --mode repair` run.
- Only a successful `comments_all` source run may advance the complete HKT date.
- Fact publication never waits for or rolls back because of AI work.

Operational state is available through `python -m jobs.refresh status` and the
additive `/api/v1/meta.dataCollection` response.
