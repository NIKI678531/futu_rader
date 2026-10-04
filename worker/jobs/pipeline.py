"""一次过跑完一个抽取范围（ADR-0020 步骤 12）。

    python -m jobs.pipeline --scope <scope_id>                 # 评论 → KOL 评论 → 帖子 → Layer B → 报表
    python -m jobs.pipeline --scope <scope_id> --resume        # 中断后续跑：已 done 的任务不重发
    python -m jobs.pipeline --scope <scope_id> --dry-run       # 只打印每一步会做什么与用量估算
    python -m jobs.pipeline --scope <scope_id> --budget-requests 500 --max-http-requests 500 \
      --calibration-report <calibration.json> --quality-report <gold-eval.json>

## 顺序为什么是这样

1. `comment_product`（评论 × 产品，own 优先 —— 排队时已按 priority 排好）：页面上最多的
   AI 字段都来自它。
2. `kol_comment_opinion`：量很小，KOL 详情页要它。
3. `post_annotation`：KOL 与官号的帖子。
4. Layer B（`synthesize`）：依赖 1 的结论，所以放在后面。
5. `audit --report`：跑完看一眼。

每一步都是幂等的：任务表的唯一键与生成物的指纹保证重复运行不重复付费。`--resume` 与不带
其实是一样的行为 —— 它存在只是为了让人知道「可以直接再跑一次」。

## 钱的开关

`--budget-requests N` 是每个任务这一轮最多领取多少批（≈ 请求数）。价格未知时它是唯一能对着
账单核的数字。到了上限就停，下次再跑接着领。所有非 dry-run 调用都要同时提供通过的批量校准
报告与 v3 人工金标报告；低层入口不能绕过发布门禁。
"""

import argparse
import json
import logging
import os
import sys
from contextlib import nullcontext
from collections import defaultdict
from datetime import date
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai import config  # noqa: E402
from ai.providers import build as build_provider  # noqa: E402
from jobs import annotate, audit, synthesize  # noqa: E402
from jobs.backfill_comment_filter import prepare_comment_task_execution  # noqa: E402
from jobs.scope_policy import require_current_exact_scope  # noqa: E402
from radar_db import make_engine  # noqa: E402
from radar_db.comment_filter import load_comment_filter_config  # noqa: E402
from radar_db.comment_routes import (  # noqa: E402
    readiness_on_connection as comment_routes_ready_on_connection,
    supersede_ineligible_jobs as supersede_ineligible_route_jobs,
)
from radar_db.schema import analysis_scopes, annotation_jobs, comments, feeds  # noqa: E402
from radar_db.scope_jobs import scope_condition
from radar_db.time_windows import utc_naive_to_hkt  # noqa: E402

log = logging.getLogger("worker.pipeline")

TASK_ORDER = ("comment_product", "kol_comment_opinion", "post_annotation")


def pending_days(engine, scope_id):
    days = defaultdict(set)
    with engine.connect() as conn:
        rows = conn.execute(
            select(annotation_jobs.c.subject_code, feeds.c.posted_at)
            .select_from(annotation_jobs
                         .outerjoin(comments, comments.c.comment_id == annotation_jobs.c.target_id)
                         .outerjoin(feeds, feeds.c.feed_id == comments.c.feed_id))
            .where(scope_condition(scope_id), annotation_jobs.c.task == "comment_product",
                   annotation_jobs.c.target_type == "comment",
                   annotation_jobs.c.status.notin_(("done", "superseded")))
            .distinct()
        )
        for code, posted_at in rows:
            days[code].add(utc_naive_to_hkt(posted_at).date() if posted_at is not None else None)
    return days


def ready_pairs(engine, scope_id, codes, ranges, anchor, *, days=None):
    days = pending_days(engine, scope_id) if days is None else days
    pairs = []
    for code in codes:
        blocked = days.get(code, set())
        if None in blocked:
            continue
        for range_key in ranges:
            window = synthesize.build_range(range_key, anchor)
            first, last = date.fromisoformat(window["benchFrom"]), date.fromisoformat(window["to"])
            if not any(first <= day <= last for day in blocked):
                pairs.append((code, range_key))
    return pairs


def run(engine, cfg, scope_id, *, provider=None, dry_run=False, budget_requests=None, max_items=None,
        skip_synth=False, ranges=None, anchor_override=None, audit_report=True,
        synth_workers=None, stage=None):
    """Run one scope; ``stage`` limits only comment-product annotation work."""
    with engine.connect() as conn:
        scope = conn.execute(select(analysis_scopes).where(analysis_scopes.c.scope_id == scope_id)).mappings().first()
    if scope is None:
        raise SystemExit(f"没有这个 scope：{scope_id}。先跑 python -m jobs.extract")
    if not dry_run:
        require_current_exact_scope(scope)
        with engine.connect() as conn:
            route_active = comment_routes_ready_on_connection(conn)
        if route_active:
            parent_filter_config = None
            with engine.begin() as conn:
                supersede_ineligible_route_jobs(conn)
        else:
            parent_filter_config, _retired = prepare_comment_task_execution(
                engine,
                load_comment_filter_config(),
            )
    else:
        parent_filter_config = None
    codes = json.loads(scope["codes_json"])
    summary = {"scope_id": scope_id, "codes": len(codes), "steps": []}

    provider = provider or (None if dry_run else build_provider(cfg))
    for task in TASK_ORDER:
        task_stage = stage if task == "comment_product" else None
        pending = annotate.pending_count(engine, task, scope_id, stage=task_stage)
        if pending == 0:
            summary["steps"].append({"task": task, "skipped": "队列为空"})
            continue
        if dry_run:
            summary["steps"].append({
                "task": task,
                "estimate": annotate.estimate(engine, cfg, task, scope_id, task_stage),
            })
            continue
        stats = annotate.run(engine, cfg, task=task, max_items=max_items or pending, provider=provider,
                             scope_id=scope_id, budget_requests=budget_requests, stage=task_stage,
                             parent_filter_config=(
                                 parent_filter_config if task in annotate.COMMENT_TASKS else None
                             ))
        summary["steps"].append({"task": task, **{k: stats[k] for k in ("run_id", "input", "success", "error",
                                                                          "requests", "tok_in", "tok_out", "aborted")}})
        if stats["aborted"]:
            log.error("%s 中止：%s —— 后续步骤不跑，修好配置后 --resume", task, stats["aborted"])
            summary["aborted"] = stats["aborted"]
            return summary

    if not dry_run:
        with engine.connect() as conn:
            unfinished = conn.execute(
                select(annotation_jobs.c.job_id).where(
                    scope_condition(scope_id),
                    annotation_jobs.c.status.notin_(("done", "superseded")),
                ).limit(1)
            ).first()
        summary["complete"] = unfinished is None
    supports_generation = provider is None or getattr(provider, "supports_generation", True)
    if not skip_synth and not supports_generation:
        summary["steps"].append({
            "task": "synthesize",
            "skipped": "当前 provider 只支持分类；保留已有 Layer-B 生成结果",
        })
    elif not skip_synth:
        ranges = list(ranges or synthesize.DEFAULT_RANGES)
        anchor = anchor_override or synthesize.read_anchor(engine)
        pairs = ready_pairs(engine, scope_id, codes, ranges, anchor) if anchor else []
        summary["synth_pairs"] = len(pairs)
        summary["synth_pairs_total"] = len(codes) * len(ranges)
        if pairs:
            kwargs = {"workers": synth_workers} if synth_workers else {}
            st = synthesize.run(
                engine,
                cfg,
                pairs=pairs,
                provider=provider,
                dry_run=dry_run,
                scope_id=scope_id,
                anchor_override=anchor_override,
                **kwargs,
            )
            summary["steps"].append({
                "task": "synthesize",
                **{
                    key: st[key]
                    for key in (
                        "run_id", "calls", "written", "skipped_same", "low_sample", "errors",
                        "tok_in", "tok_out", "pairs", "pairs_clean",
                    )
                },
            })
            if not dry_run and st["errors"]:
                summary["complete"] = False
        elif anchor is None:
            summary["steps"].append({"task": "synthesize", "skipped": "meta_kv 里没有 anchor"})
        else:
            summary["steps"].append({"task": "synthesize", "skipped": "没有就绪的 (code, range)"})
    if not dry_run and audit_report:
        rep = audit.report(engine, scope_id)
        summary["audit"] = {"queue": rep["queue"], "needs_review_rate": rep["annotations"]["needs_review_rate"],
                            "evidence_located_rate": rep["evidence_located_rate"],
                            "prefilter_dropped_by_rule": rep["prefilter_dropped_by_rule"]}
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description="一次过跑完一个抽取范围")
    ap.add_argument("--scope", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--resume", action="store_true", help="语义同不带：幂等续跑")
    ap.add_argument("--budget-requests", type=int)
    ap.add_argument("--max-http-requests", type=int)
    ap.add_argument("--max-items", type=int)
    ap.add_argument("--calibration-report", type=Path,
                    help="scripts.calibrate 产生且通过的批量校准报告")
    ap.add_argument("--quality-report", type=Path,
                    help="scripts.evaluate_gold 产生且通过的 v3 人工金标报告")
    ap.add_argument("--skip-synth", action="store_true")
    ap.add_argument("--ranges", help="Layer B 只做这些区间，如 d7,d30")
    ap.add_argument("--synth-workers", type=int, help="Layer B (code, range) 并行线程数，默认 8")
    args = ap.parse_args(argv)
    if not args.dry_run and (args.max_http_requests is None or args.max_http_requests < 1):
        ap.error("AI execution requires --max-http-requests")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    engine = make_engine()
    cfg = config.load(_allow_missing_key=args.dry_run)
    if not args.dry_run:
        # Import lazily: analyze imports full_own -> pipeline at module load time.
        from jobs import analyze as analysis_job
        try:
            analysis_job.check_calibration(
                cfg, args.calibration_report, require_singleton=True,
            )
            analysis_job.check_quality(cfg, args.quality_report)
        except (ValueError, RuntimeError) as exc:
            ap.error(str(exc))
    from ai.providers.base import RunControl, RunStopped
    from radar_db.leases import WorkerLease
    control = None if args.dry_run else RunControl(args.max_http_requests)
    with (WorkerLease(engine, "own-analysis") if control else nullcontext()), (
        control.interruptible() if control else nullcontext()
    ):
        try:
            out = run(engine, cfg, args.scope, dry_run=args.dry_run, budget_requests=args.budget_requests,
                      max_items=args.max_items, skip_synth=args.skip_synth,
                      provider=build_provider(cfg, control=control) if control else None,
                      ranges=[r.strip() for r in args.ranges.split(",")] if args.ranges else None,
                      synth_workers=args.synth_workers)
        except RunStopped:
            out = {"complete": False, "status": control.reason}
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0 if args.dry_run or out.get("complete", False) else 2


if __name__ == "__main__":
    raise SystemExit(main())
