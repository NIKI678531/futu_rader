# Financial Market Monitor

> An AI-assisted market intelligence and sentiment observability platform for securities and asset management.

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=111)
![Flask](https://img.shields.io/badge/Flask-3.0-000000?logo=flask&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-8.0-4479A1?logo=mysql&logoColor=white)

Financial Market Monitor, also known as Futu Radar, transforms fragmented market conversations, institutional content, and investor opinions into structured and traceable market intelligence. It helps research, product, and marketing teams monitor attention, sentiment, and narrative shifts across ETFs and related assets.

The platform connects data collection, semantic analysis, metric computation, and interactive visualization in one workflow. Deterministic rules handle instrument identification and financial metrics, while AI extracts meaning from unstructured text. The resulting signals are delivered through a unified API and a market-focused dashboard.

## Why It Matters

- **Finance-native context** — Data is organized around tickers, product relationships, issuers, and market themes to reduce ambiguity in financial language.
- **Signals over noise** — Cleaning, deduplication, relevance screening, and sample-quality controls turn large volumes of discussion into observable market signals.
- **Facts and opinions stay separate** — Deterministic logic computes quantitative metrics; AI interprets sentiment, topics, and narratives without rewriting source facts.
- **Traceable insights** — Aggregated findings can be traced back to their evidence, analysis version, time range, and processing state.
- **Built for continuous operation** — Incremental processing, scheduled jobs, failure recovery, and multi-environment deployment support long-running research workflows.

## Architecture

```mermaid
flowchart LR
    A[Market Content and Public Information] --> B[Collection and Normalization]
    B --> C[(Unified Financial Data Layer)]
    C --> D[Rules and AI Analysis]
    D --> E[Metrics and Insights API]
    E --> F[Market Intelligence Dashboard]
    C -. Source Evidence .-> F
```

Facts, model judgments, and presentation logic are managed as separate layers. The backend owns financial metric definitions, data workers handle synchronization and analysis, and the frontend consumes standardized results. This keeps calculations consistent across every view.

## Technology Stack

| Layer | Technologies |
| --- | --- |
| Web application | React, Vite, React Router |
| API services | Python, Flask, Gunicorn |
| Data processing | SQLAlchemy, Pandas, scheduled jobs, incremental ETL |
| Intelligent analysis | Structured prompts, typed outputs, rules and LLM orchestration |
| Data storage | MySQL 8, SQLite, Alembic |
| Quality and delivery | Pytest, Node Test Runner, Playwright, Docker |

## Quick Start

Requirements: Node.js 18+ and Python 3.11. Full data mode uses MySQL 8; SQLite is supported for local development.

```bash
git clone https://github.com/NIKI678531/futu_rader.git
cd futu_rader
```

Start the API:

```bash
cd backend
python -m venv .venv
# Activate the virtual environment for your shell, then:
python -m pip install -r requirements.txt
python app.py
```

Start the worker in a second terminal. It applies pending database migrations,
runs the daily 08:00 refresh (sync when `MARKET_INSIGHT_DATABASE_URL` is set,
prices, then AI for every page range) and executes the requests queued from
`/admin`; without this process, runs deliberately remain queued:

```bash
cd worker
python -m venv .venv
# Activate the virtual environment for your shell, then:
python -m pip install -r requirements.txt
python scheduler.py
```

`worker/.env` needs the Layer A model (`AI_PRIMARY_*`) and, when Layer A is the
classification-only TypeSafe provider, a text-generation model for page
summaries (`AI_SYNTH_*`); see `worker/.env.example` and
[ADR-0031](docs/adr/0031-daily-full-refresh-single-queue.md).

Start the web application:

```bash
cd frontend
npm install
npm run dev
```

On Windows mapped network drives, `npm run dev` automatically syncs the source to the local
mirror described in ADR-0018 and runs Vite there; do not start `vite` directly from the share.
Open `http://localhost:5173` in a browser. The API runs on `http://localhost:8008` by default. Configuration examples are available in `backend/.env.example` and `worker/.env.example`.

The production frontend is a Webpack 5 Module Federation remote. `npm run build`
emits `dist/remoteEntry.js`; `npm run dev:remote` serves the development container
on port `3026`, and `npm run remote-smoke` checks the built container in a browser.
Register it in the Investment Research Database host with these values:

| Field | Value |
| --- | --- |
| `app_name` / `remote_scope` | `opinionRadar` |
| `remote_module` | `./App` |
| `route` | `/opinion-radar` |
| `remote_url` | `/remote/opinion-radar/remoteEntry.js` |

The exposed component reuses the host router. The standalone Vite entry supplies
its own router for ordinary local development.

To start the containerized infrastructure:

```bash
docker compose up -d
```

## Production Deployment

PRD EKS manifests, environment mapping, image builds, verification and rollback:
[k8s/README.md](k8s/README.md). Use `docker-compose.prd.yml` for production image
builds. The existing `docker-compose.yml` remains a local development setup.
Ingress host and ACM certificate must be filled before applying Ingress.

## Project Structure

```text
frontend/   Market intelligence dashboard and user experience
backend/    Financial metrics, query services, and unified API
worker/     Data synchronization, ETL, AI analysis, and scheduling
radar_db/   Data models, migrations, and shared access layer
deploy/     Airflow and Kubernetes deployment resources
docs/       Metric definitions, architecture decisions, and operations guides
```

## Quality Principles

The platform treats “no data” and “a measured value of zero” as different states. Source coverage, analysis progress, and sample quality are part of every result. Core financial metrics have a single calculation source, while AI outputs use structured constraints and versioned analysis so that each conclusion can be explained by its data, rules, and execution context.

## Use Cases

Financial Market Monitor supports ETF product research, market sentiment observation, brand and competitor monitoring, investor-relations analysis, and trend discovery for financial content teams. It provides market observation and research support; it does not constitute investment advice, a trading signal, or a promise of returns.
