"""Claim and execute database-backed analysis requests from the admin page."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from dataclasses import replace
from datetime import timezone
from zoneinfo import ZoneInfo

from ai import config
from ai.providers import build as build_provider
from ai.providers.base import RunControl
from collection.budget import DailyBudget
from collection.models import HARD_DAILY_MAX
from jobs import analyze, full_own, refresh
from core.top_products import analysis_products, parse_own_limit
from radar_db import make_engine
from radar_db.analysis_requests import RANGE_PRIORITY, claim_next, finish, renew, requeue

import clock


LEASE_SECONDS = 300


class RequestLease:
    def __init__(self, engine, request_id: str, owner: str, seconds: int = LEASE_SECONDS):
        self.engine = engine
        self.request_id = request_id
        self.owner = owner
        self.seconds = seconds
        self.stopped = threading.Event()
        self.lost = False

    def __enter__(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def _run(self):
        while not self.stopped.wait(self.seconds / 3):
            try:
                if not renew(
                    self.engine, self.request_id, self.owner, seconds=self.seconds
                ):
                    self.lost = True
                    return
            except Exception:
                # The durable global analysis lease still prevents duplicate AI
                # execution. A later queue poll can recover this row if needed.
                continue

    def __exit__(self, *_args):
        self.stopped.set()
        self.thread.join()


def _policy_hash(cfg) -> str:
    payload = json.dumps(
        {
            "model": cfg.model,
            "prompt": cfg.prompt_version,
            "taxonomy": cfg.taxonomy_version,
            "schema": cfg.schema_version,
            "batch": cfg.micro_batch_size,
            "concurrency": cfg.concurrency,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def validated_runtime_config():
    """Load the worker policy; only missing credentials or an explicit off switch stop it."""

    if not refresh._data_governance_approved():
        raise ValueError("AI_DATA_GOVERNANCE_APPROVED=false：AI 分析已被手动关闭")
    cfg = config.load()
    cfg = replace(cfg, grouped_batches=True, fill_missing_only=False)
    config.synthesis(cfg)
    return cfg


def own_top_limit() -> int | None:
    return parse_own_limit(os.getenv("AI_OWN_TOP_LIMIT"))


def runtime_readiness() -> dict:
    """Return a safe status payload for the worker heartbeat."""

    try:
        cfg = validated_runtime_config()
        limit = own_top_limit()
    except (OSError, RuntimeError, ValueError) as exc:
        return {
            "ready": False,
            "error": " ".join(str(exc).split())[:500],
        }
    return {
        "ready": True,
        "error": None,
        "model": cfg.model,
        "promptVersion": cfg.prompt_version,
        "schemaVersion": cfg.schema_version,
        "taxonomyVersion": cfg.taxonomy_version,
        "ownTopLimit": limit,
    }


def run_analysis(range_key: str, *, engine=None) -> dict:
    """Run one range's selected products through both AI layers."""

    if range_key not in RANGE_PRIORITY:
        raise ValueError("range 只允许 d1、d2、d7、d14、d30 或 mtd")
    engine = engine or make_engine()
    cfg = validated_runtime_config()
    limit = own_top_limit()
    selected = analysis_products(engine, range_key, own_limit=limit)
    selected_codes = [row["code"] for row in selected]
    if not selected_codes:
        return {
            "anchor": None,
            "range": range_key,
            "productsTotal": 0,
            "productsCompleted": 0,
            "aiRequests": 0,
            "annotationsWritten": 0,
            "synthesisWritten": 0,
            "reused": 0,
            "errors": 0,
            "budgetDate": clock.now(ZoneInfo("Asia/Hong_Kong")).date().isoformat(),
            "status": "complete",
            "ownTopLimit": limit,
            "selectedCodes": [],
        }
    args = type(
        "AdminAnalysisArgs",
        (),
        {
            "codes": ",".join(selected_codes),
            "sector": None,
            "struct": None,
            "ownership": "all",
            "anchor": None,
            "anchor_mode": "latest-complete",
            "ranges": range_key,
            "current_window_only": False,
        },
    )()
    plan = analyze.make_plan(engine, args)
    max_attempts = int(os.getenv("AI_MANUAL_MAX_HTTP_REQUESTS", str(HARD_DAILY_MAX)))
    if not 1 <= max_attempts <= HARD_DAILY_MAX:
        raise ValueError(f"AI_MANUAL_MAX_HTTP_REQUESTS 必须在 1 到 {HARD_DAILY_MAX} 之间")
    budget_date = clock.now(ZoneInfo("Asia/Hong_Kong")).date()
    budget = DailyBudget(
        engine,
        _policy_hash(cfg),
        max_attempts,
        now=lambda: clock.now(timezone.utc),
    )
    control = RunControl(max_attempts)
    control.reserve_hook = budget.reserve
    provider = build_provider(cfg, control=control)
    progress = full_own.run_manual(engine, cfg, plan, provider)
    completed = sum(
        bool(row.get("complete")) for row in progress.get("products", {}).values()
    )
    batch = progress.get("batchRun") or control.snapshot()
    work = progress.get("work") or {}
    return {
        "anchor": plan["anchor"],
        "range": range_key,
        "productsTotal": len(plan["codes"]),
        "productsCompleted": completed,
        "aiRequests": batch.get("httpAttempts", control.attempts),
        "annotationsWritten": work.get("annotationsWritten", 0),
        "synthesisWritten": work.get("synthesisWritten", 0),
        "reused": work.get("annotationsReused", 0) + work.get("synthesisReused", 0),
        "errors": work.get("errors", 0),
        "budgetDate": budget_date.isoformat(),
        "status": progress.get("status", "partial"),
        "ownTopLimit": limit,
        "selectedCodes": selected_codes,
    }


def process_next(*, engine=None, runner=None, owner: str | None = None) -> dict | None:
    """Process at most one queued request; safe to call on every scheduler tick."""

    engine = engine or make_engine()
    owner = owner or uuid.uuid4().hex
    request = claim_next(
        engine, owner=owner, lease_seconds=LEASE_SECONDS
    )
    if request is None:
        return None
    runner = runner or (lambda range_key: run_analysis(range_key, engine=engine))

    def fail(exc):
        error = " ".join(str(exc).split())[:500]
        summary = {
            "range": request["range"],
            "productsTotal": None,
            "productsCompleted": None,
            "aiRequests": None,
            "annotationsWritten": None,
            "synthesisWritten": None,
            "reused": None,
            "errors": 1,
            "status": "failed",
        }
        finish(
            engine,
            request["id"],
            owner,
            "failed",
            summary=summary,
            error=error,
        )
        return {**request, "status": "failed", "summary": summary, "error": error}

    try:
        with RequestLease(engine, request["id"], owner):
            summary = runner(request["range"])
        terminal = "succeeded" if summary.get("status") == "complete" else "partial"
        finish(engine, request["id"], owner, terminal, summary=summary)
        return {**request, "status": terminal, "summary": summary}
    except RuntimeError as exc:
        if "already holds the lease" in str(exc):
            requeue(engine, request["id"], owner, error="已有 AI 分析正在运行，等待后续重试")
            return {**request, "status": "queued"}
        return fail(exc)
    except Exception as exc:
        return fail(exc)
