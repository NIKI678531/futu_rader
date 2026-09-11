# 舆情雷达 / futu-radar

Local import of the Claude Design project
[fd51d8b0-14cd-4cd8-900b-45e95ac4d314](https://claude.ai/design/p/fd51d8b0-14cd-4cd8-900b-45e95ac4d314?file=official-activity.dc.html).

The repo holds the same screens twice, on purpose:

| Directory | What it is |
| --- | --- |
| `design/` | Byte-exact mirror of the design project. Read-only reference — never hand-edit. |
| `frontend/` | React + Vite port of the five screens, built from that mirror. |
| `backend/` | Flask API. 22 endpoints under `/api/v1`, two providers (`demo` / `sql`). Every metric formula lives in `backend/core/`. |
| `worker/` | Dump import, `raw_json` ETL, and the collection scheduler. Raw rows only — no metric formulas. |
| `radar_db/` | The slim DB's SQLAlchemy `MetaData`, shared by `backend/` and `worker/`. SQLite locally, MySQL 8 in production. |
| `docs/` | Spec and client-confirmation markdown, plus `docs/adr/` for decisions. |

## Running

```sh
cd frontend
npm install
npm run dev      # the React port          → http://localhost:5173
npm run design   # the untouched .dc.html  → http://localhost:5174
```

`npm run design` serves `../design` as static files, so the original design source
renders side by side with the port for comparison. `npm run build` emits `frontend/dist`.

The checkout currently lives on the `P:` DFS share, which needs two Vite settings that
`frontend/vite.env.js` turns on automatically — see the comment there. A cold
`node_modules/.vite` still costs a slow first start, and HMR stays slow: every module is
read over SMB and the watcher has to poll. Working from a local disk is noticeably faster
and needs no special configuration.

**`npm install` does not merely run slowly on `P:` — it fails.** Unpacking creates
symlinks and renames small files concurrently, and SMB's semantics there differ enough
that the install dies partway with `EPERM`/`EBUSY`/`ENOENT`. So on that share the
toolchain runs from a local mirror instead ([ADR-0018](docs/adr/0018-local-mirror-for-npm.md)):

```sh
cd frontend
npm run mirror        # sync P: → %USERPROFILE%\.futu-radar\mirror (source only)
npm run mirror:test   # sync, then run the full suite over there
```

Source is only ever synced one way. The trap worth knowing: edit `src/` on `P:`, go run
the tests in the mirror without syncing, and they pass — on the previous revision.
On a local checkout none of this applies; just `npm install` in `frontend/`.

The API and the collector are Python 3.11, each with its own virtualenv and no shared
package — the worker writes raw rows, the API owns every formula (see `backend/core/`):

```sh
cd backend && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m pytest -q
.venv/Scripts/python app.py            # → http://localhost:8008/api/v1/meta

cd worker  && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m pytest -q
```

No system Python? `uv` does both steps without one:
`uv venv --python 3.11 .venv && uv pip install --python .venv/Scripts/python.exe -r requirements.txt`.
Note that a `uv`-made venv has no `pip` module — install into it with
`VIRTUAL_ENV=<abs path to .venv> uv pip install …`.

On Windows, prefix Python commands with `PYTHONIOENCODING=utf-8` (or run `python -X utf8`);
the console log lines are Chinese and will otherwise come out as mojibake.

The frontend is deliberately not a compose service: building it in a container off the
`P:` share is far slower than `npm run dev`. Ports sit one above ChatInsight's so both
projects can run at once. Rationale for every such choice is in [plan.md](plan.md) §6.

### Verification

```sh
cd frontend && npm test      # guards → six-state → diff
cd backend  && .venv/Scripts/python -m pytest -q
cd worker   && .venv/Scripts/python -m pytest -q
```

`npm test` is three gates, and they check different things: `guards` is a static grep for
five red lines (no demo-data generator in screen code, no `?? 0` fallbacks in the data
layer, …); `six-state` drives a purpose-built missing-data fixture through a browser and
asserts 56 renderings; `diff` compares every text segment of the port against the design
source, verbatim.

All three run under `demo`. They do **not** cover the `sql` provider, whose missing
surface is much larger — whole blocks come back `null`, not just fields. That needs the
real slim DB, so it is opt-in and separate:

```sh
cd backend  && DATA_PROVIDER=sql APP_PORT=8019 .venv/Scripts/python app.py
cd frontend && API=http://127.0.0.1:8019/api/v1 npm run real-data-check
```

It loads all five routes against the real database and fails on any `pageerror` or any
`NaN`/`undefined`/`null`/`[object Object]` reaching the page.

### Which provider is serving

`DATA_PROVIDER` picks the data source; the endpoint contract is identical either way
([ADR-0001](docs/adr/0001-dual-provider.md)). **Always say which one a screenshot came
from** — `demo`'s numbers are design-source fiction.

| | `demo` (default) | `sql` |
| --- | --- | --- |
| Source | fixtures exported from `design/radar-data.js` | the slim DB built from the client's dump |
| Anchor ("today") | `2026-09-01`, frozen | `2026-08-25`, measured at import |
| Job | pixel-for-pixel fidelity, the acceptance baseline | honesty: real values where they exist, 「暂不可用」 where they don't |

```sh
cd backend
DATA_PROVIDER=demo .venv/Scripts/python app.py    # fixtures, no database needed
DATA_PROVIDER=sql  .venv/Scripts/python app.py    # the slim DB (build it first, below)
```

### Building the slim DB from the client's dump

One-time, and only if you need the `sql` provider. The dump is a 10.3 GB `mysqldump`;
it is **not in the repo and must not be** — it carries real nicknames, IP regions and
profile text ([ADR-0008](docs/adr/0008-dump-import-and-slim-db.md)). Copy it off OneDrive
to a local SSD first: on-demand sync will crawl through a 10 GB sequential read.

```sh
cd worker
# 1. stream-parse the dump into the src_* mirror layer
#    (scans all 991,273 feed rows, keeps 504,400 — the 120 products over the last 120 days)
.venv/Scripts/python -m jobs.import_dump --dump "D:/path/to/dump-market_insight-….sql"
# 2. unpack raw_json into the feeds / comments / mentions / users fact tables
.venv/Scripts/python -m jobs.etl
```

The slim DB lands **outside the repo** by default — `%LOCALAPPDATA%\futu-radar\radar.db`
on Windows, ~5.3 GB (most of it the retained `raw_json`, which is what lets the ETL
re-run without re-reading the dump). Two reasons, either one sufficient: the `P:` share writes at
9.4 MB/s against C:'s 3.1 GB/s, and the slim DB carries the same real user data the dump
does. Point `RADAR_DB_URL` somewhere else if you need to; see `radar_db/__init__.py`.

Both steps are re-runnable and reconcile their own row counts — a mismatch between rows
read, rows kept and rows landed is a hard failure, not a warning.

### Docker

```sh
docker compose up -d      # mysql 8 (3307) + backend (8008, DATA_PROVIDER=sql) + worker
```

This is the production shape: MySQL instead of the local SQLite file, same schema, same
SQL ([ADR-0016](docs/adr/0016-sqlite-local-mysql-prod.md)). The compose database starts
empty — the import above targets whatever `RADAR_DB_URL` points at, so run it against the
MySQL URL to fill it. There is no automated test on the MySQL dialect yet; check the
headline numbers by hand the first time.

## Routes

| Route | Design file | Status |
| --- | --- | --- |
| `/official` | `official-activity.dc.html` | ported |
| `/kol` | `kol-activity.dc.html` | ported |
| `/kol/detail?kol=…` | `kol-detail.dc.html` | ported |
| `/product?code=…&range=…` | `product-monitor.dc.html` | ported |
| `/sector` | `sector-overview.dc.html` | ported |

`/` and any unknown path redirect to `/official`.

## How the port maps onto the design source

A `.dc.html` file is an `<x-dc>` template plus a `class Component extends DCLogic`
script. `DCLogic` is a React class component without `render()`, so each screen ports
mechanically:

- `state`, lifecycle, helper methods and `renderVals()` are copied **verbatim** from the
  design source. Keeping them diffable against `design/*.dc.html` is the point — when the
  design changes, re-copy the method rather than re-deriving it.
- `render()` is the only new code: `{{ hole }}` → JSX, `<sc-if>` → `{cond && (…)}`,
  `<sc-for>` → `.map()` with a `key` (dc did not need keys; JSX does).
- `data-props` defaults become `static defaultProps`.

Supporting pieces:

- `frontend/src/lib/dc.js` — `s(css)` parses a design `style="…"` declaration string into a
  React style object; `hover(css)` / `focus(css)` mint a class backed by a real
  `:hover` / `:focus` rule, which is how `style-hover` / `style-focus` survive the move.
- `frontend/src/components/Shell.jsx` — header rows 1 and 2, identical across all five
  screens; each screen passes its own filter bar as `children`.
- `frontend/src/lib/routes.js` — maps design hrefs (`kol-detail.dc.html?kol=…`) to router
  paths; `frontend/src/components/DcLink.jsx` uses it so in-page links stay verbatim.
- `frontend/src/data/radar.js` — imports `design/radar-data.js` for its side effect and
  re-exports `window.RADAR`. The data layer is the mirror's file, unmodified.

`product-monitor` (1793 source lines) and `sector-overview` (1295) are large enough that
their templates are split into section components under `frontend/src/screens/productMonitor/`
and `frontend/src/screens/sectorOverview/`; `renderVals()` stays whole in each `index.jsx`
because the sections share values the design computes in one pass.

### Deliberate deviations

- **No async runtime wait.** The design polls for `window.RADAR` because `radar-data.js`
  loads as a `<script>`; the port imports it synchronously, so the poll is gone.
- **URL rewrites.** `product-monitor`'s `go()` writes `/product?code=…&range=…` through
  `history.replaceState`, which `BrowserRouter` does not observe — the address updates
  without a remount, matching the design's behaviour.
- **Dead code dropped.** Values `renderVals()` computes but no template reads
  (`domains`, `subItems`, `heatBg`, `negCats`, …) were left out; the design's own comments
  mark the modules they belonged to as merged away.
- **Keys.** `id` / `key` fields were added where a list had no stable identifier of its
  own: `riskRows` and `evidence` rows in `product-monitor`, and the treemap tiles, theme
  rows and risk rows in `sector-overview`.

### Known warnings

`npm run build` prints `@import must precede all other statements` from the design
system's `colors_and_type.css`, which puts a Google Fonts `@import` after an `@font-face`.
That import is invalid in the design too — browsers drop it there as well — so the mirror
is left untouched and CJK text renders from the bundled `NotoSans-Regular.ttf`.

`Failed to resolve entry for package "@vitejs/plugin-react"` used to abort roughly one run
in two, `dev` and `build` alike. It is not a broken install: Vite bundles the config with
`preserveSymlinks: false` hardcoded, realpaths each package to the UNC share behind `P:`,
and maps the drive letter back by parsing `net use` — a race it sometimes loses. Both Vite
configs now avoid bare specifiers entirely, so the resolution never happens; see the
comment at the top of `vite.config.js`. If the error ever returns, look for a bare
`import … from '<package>'` that crept back into a config file.

## Re-importing from Claude Design

Fetch through the design MCP's `render_preview` serve URL with **`&raw=1`** appended;
without that flag the endpoint injects preview scaffolding into `.dc.html` and the copy is
no longer byte-exact. Verify each file's size after fetching. Serve URLs carry a
short-lived project-scoped token — keep them out of commits and shared text; link the
`claude.ai/design/…` project URL instead.
