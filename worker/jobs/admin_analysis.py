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

from sqlalchemy import and_, or_, select, update

from ai import config
from ai.providers import build as build_provider, generation_supported
from ai.providers.base import RunControl
from collection.budget import DailyBudget
from collection.models import HARD_DAILY_MAX
from jobs import analyze, daily, full_own, refresh, synthesize
from core.top_products import analysis_products, parse_own_limit
from radar_db import make_engine
from radar_db.analysis_requests import (
    RANGE_PRIORITY,
    claim_next,
    finish,
    promote_followups,
    renew,
    requeue,
)
from radar_db.schema import annotation_jobs, feeds, meta_kv

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
    synth_cfg = config.synthesis(cfg)
    return {
        "ready": True,
        "error": None,
        "provider": cfg.provider,
        "model": cfg.model,
        "generationProvider": synth_cfg.provider,
        "generationModel": synth_cfg.model,
        "generationError": None if generation_supported(cfg) else GENERATION_MISSING,
        "promptVersion": cfg.prompt_version,
        "schemaVersion": cfg.schema_version,
        "taxonomyVersion": cfg.taxonomy_version,
        "ownTopLimit": limit,
        "dataMode": "production" if refresh.source_configured() else "dump",
        "dailyCron": daily.daily_cron(),
        "intradayCron": daily.intraday_cron(),
    }


GENERATION_MISSING = (
    "Layer B 需要能生成文字的模型：Layer A 用 TypeSafe 时请配置 "
    "AI_SYNTH_BASE_URL、AI_SYNTH_API_KEY、AI_SYNTH_MODEL"
)
MISSING_LIST_LIMIT = 60
REVIVED_TASKS = ("comment_product", "kol_comment_opinion", "post_annotation")


def revive_failed_jobs(engine, codes) -> int:
    """Give failed/dead Layer-A jobs of these products one more round (attempts reset)."""

    codes = list(codes)
    with engine.begin() as conn:
        feed_ids = select(feeds.c.feed_id).where(feeds.c.code.in_(codes))
        job_ids = [job_id for (job_id,) in conn.execute(
            select(annotation_jobs.c.job_id).where(
                annotation_jobs.c.task.in_(REVIVED_TASKS),
                annotation_jobs.c.status.in_(("failed", "dead")),
                or_(
                    annotation_jobs.c.subject_code.in_(codes),
                    and_(
                        annotation_jobs.c.task == "post_annotation",
                        annotation_jobs.c.target_type == "feed",
                        annotation_jobs.c.target_id.in_(feed_ids),
                    ),
                ),
            )
        )]
        if not job_ids:
            return 0
        return int(conn.execute(
            update(annotation_jobs)
            .where(annotation_jobs.c.job_id.in_(job_ids))
            .values(status="pending", attempts=0, lease_until=None, last_error=None)
        ).rowcount or 0)


def missing_layer_b(engine, codes, range_key, anchor) -> list[dict]:
    """``[{code, kinds}]`` for every product whose Layer B is not published for ``anchor``."""

    return synthesize.missing_generations(engine, codes, range_key, anchor)


def run_analysis(range_key: str, *, engine=None) -> dict:
    """Run one range's selected products through both AI layers until nothing is left."""

    if range_key not in RANGE_PRIORITY:
        raise ValueError("range 只允许 d0、d1、d2、d7、d14、d30 或 mtd")
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
            "baseline_fallback": True,
        },
    )()
    plan = analyze.make_plan(engine, args)
    revived = revive_failed_jobs(engine, plan["codes"])
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
    missing = missing_layer_b(engine, plan["codes"], range_key, plan["anchor"])
    status = progress.get("status", "partial")
    reasons = []
    can_generate = generation_supported(cfg)
    if not can_generate:
        reasons.append(GENERATION_MISSING)
    if plan.get("baselineUnavailable"):
        reasons.append("基准期早于库内数据起点：只分析当前区间，环比显示为数据暂不可用")
    blocked = sorted(
        code for code, row in progress.get("products", {}).items() if row.get("blocked")
    )
    # Without a generation model every product stops at Layer B; that is the reason above.
    if blocked and can_generate:
        reasons.append(f"{len(blocked)} 只产品仍有失败或被占用的标注任务")
    if status == "complete" and missing:
        status = "incomplete"
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
        "revivedJobs": revived,
        "missingPairs": len(missing),
        "missing": missing[:MISSING_LIST_LIMIT],
        "blockedCodes": blocked[:MISSING_LIST_LIMIT],
        "reasons": reasons,
        "baselineUnavailable": bool(plan.get("baselineUnavailable")),
        "budgetDate": budget_date.isoformat(),
        "status": status,
        "ownTopLimit": limit,
        "selectedCodes": selected_codes,
    }


def process_next(*, engine=None, runner=None, owner: str | None = None) -> dict | None:
    """Process at most one queued request; safe to call on every scheduler tick."""

    engine = engine or make_engine()
    owner = owner or uuid.uuid4().hex
    promote_followups(engine)
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
