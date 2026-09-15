"""一次过跑完一个抽取范围（ADR-0020 步骤 12）。

    python -m jobs.pipeline --scope <scope_id>                 # 评论 → KOL 评论 → 帖子 → Layer B → 报表
    python -m jobs.pipeline --scope <scope_id> --resume        # 中断后续跑：已 done 的任务不重发
    python -m jobs.pipeline --scope <scope_id> --dry-run       # 只打印每一步会做什么与用量估算
    python -m jobs.pipeline --scope <scope_id> --budget-requests 500

## 顺序为什么是这样

1. `comment_product`（评论 × 产品，own 优先 —— 排队时已按 priority 排好）：页面上最多的
   AI 字段都来自它。
2. `kol_comment_opinion`：量很小，KOL 详情页要它。
3. `post_annotation`：KOL 与官号的帖子。
4. Layer B（`synthesize`）：依赖 1 的结论，所以放在后面。
5. `audit --report`：跑完看一眼。

每一步都是幂等的：任务表的唯一键与生成物的指纹保证重复运行不重复付费。`--resume` 与不带
其实是一样的行为 —— 它存在只是为了让人知道「可以直接再跑一次」。

## 评论任务只领 `stage='llm'`（ADR-0021）

`extract` 排进来的评论任务默认 `stage='student'`，先由 `jobs.classify` 用学生模型判一遍，判不准的
才改成 `llm`。本模块对 `comment_product` 只领 `llm` 段 —— 学生还没跑到的任务不该被 Luna 抢走。
帖子与 KOL 评论任务只有 `llm` 一段，不分段领。

## Layer B 逐区间触发（ADR-0021）

原来要等 scope 里**全部**任务 done 才 synthesize：61 只产品 × 60 天的队列里，`d1`（昨天）的评论
早就判完了，页面上「昨日」那块却要等到 60 天前的评论也判完才亮。现在按 `core.calendar.PRESETS`
六档逐区间判：该区间的当前窗＋基准窗里**没有** pending/claimed 的评论任务（不分段 —— 学生段
没跑完也算没就绪），这一对 `(code, range)` 就进 Layer B。`--skip-synth` 关掉它。

## 钱的开关

`--budget-requests N` 是每个任务这一轮最多领取多少批（≈ 请求数）。价格未知时它是唯一能对着
账单核的数字。到了上限就停，下次再跑接着领。
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
# `core.calendar` 是 backend 的叶子模块（区间预设的唯一实现处，铁律 1）。`jobs.synthesize` 也会把
# backend 放进 sys.path，但不能指望它先被 import —— `python -m jobs.pipeline` 单独起的时候这里是
# 第一个碰 `core` 的地方。
if (REPO_ROOT / "backend" / "core").is_dir() and str(REPO_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "backend"))

from ai import config  # noqa: E402
from ai.providers import build as build_provider  # noqa: E402
from core.calendar import PRESETS, build as build_range  # noqa: E402
from jobs import annotate, audit, synthesize  # noqa: E402
from radar_db import make_engine  # noqa: E402
from radar_db.schema import analysis_scopes, annotation_jobs, comments, feeds  # noqa: E402
from radar_db.scope_jobs import scope_condition  # noqa: E402

log = logging.getLogger("worker.pipeline")

TASK_ORDER = ("comment_product", "kol_comment_opinion", "post_annotation")


def pending_days(engine, scope_id):
    """scope 里还没判完（pending/claimed，任一 stage）的评论任务，按产品归集帖子日期集合。"""
    out = defaultdict(set)
    with engine.connect() as conn:
        rows = conn.execute(
            select(annotation_jobs.c.subject_code, feeds.c.posted_at)
            # 外连接：评论或帖子行丢了的待办任务也要出现（日期为 NULL ⇒ 挡住全部区间），
            # 内连接会让它凭空消失，区间就会在还有待办时被判成「就绪」。
            .select_from(annotation_jobs
                         .outerjoin(comments, comments.c.comment_id == annotation_jobs.c.target_id)
                         .outerjoin(feeds, feeds.c.feed_id == comments.c.feed_id))
            .where(scope_condition(scope_id), annotation_jobs.c.task == "comment_product",
                   annotation_jobs.c.target_type == "comment",
                   annotation_jobs.c.status.in_(("pending", "claimed")))
            .distinct()
        )
        for code, ts in rows:
            out[code].add(ts.date() if ts is not None else None)
    return out


def ready_pairs(engine, scope_id, codes, ranges, anchor, *, days=None):
    """哪些 `(code, range)` 的当前窗＋基准窗里已经没有待判评论。

    日期取不到（`posted_at` 为 NULL）的待办任务视为**挡住全部区间**：不知道它属于哪一天，
    就不能说任何一个区间已经齐了。
    """
    days = pending_days(engine, scope_id) if days is None else days
    out = []
    for code in codes:
        blocked = days.get(code, set())
        if None in blocked:
            continue
        for rk in ranges:
            rng = build_range(rk, anchor)
            lo, hi = date.fromisoformat(rng["benchFrom"]), date.fromisoformat(rng["to"])
            if any(lo <= d <= hi for d in blocked):
                continue
            out.append((code, rk))
    return out


def run(engine, cfg, scope_id, *, provider=None, dry_run=False, budget_requests=None, max_items=None,
        skip_synth=False, ranges=None, synth_workers=None, stage=annotate.STAGE_LLM):
    """跑一个 scope：三类标注任务 → 就绪的 (code, range) 进 Layer B → 审计。

    `stage`：评论任务只领这一段，默认 `llm`（学生段归 `jobs.classify`）。传 `None` 不分段 ——
    只给没有学生模型也不想走 classify 放行路径的离线场景用。
    """
    with engine.connect() as conn:
        scope = conn.execute(select(analysis_scopes).where(analysis_scopes.c.scope_id == scope_id)).mappings().first()
    if scope is None:
        raise SystemExit(f"没有这个 scope：{scope_id}。先跑 python -m jobs.extract")
    codes = json.loads(scope["codes_json"])
    summary = {"scope_id": scope_id, "codes": len(codes), "steps": []}

    provider = provider or (None if dry_run else build_provider(cfg))
    for task in TASK_ORDER:
        # 评论任务只领 Luna 段；学生段由 jobs.classify 消费。帖子与 KOL 评论不分段。
        task_stage = stage if task == "comment_product" else None
        pending = annotate.pending_count(engine, task, scope_id, stage=task_stage)
        if pending == 0:
            summary["steps"].append({"task": task, "skipped": "队列为空"})
            continue
        if dry_run:
            summary["steps"].append({"task": task, "estimate": annotate.estimate(engine, cfg, task, scope_id)})
            continue
        stats = annotate.run(engine, cfg, task=task, max_items=max_items or pending, provider=provider,
                             scope_id=scope_id, budget_requests=budget_requests, stage=task_stage)
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
                    annotation_jobs.c.status != "done",
                ).limit(1)
            ).first()
        summary["complete"] = unfinished is None
    if not skip_synth:
        ranges = list(ranges or synthesize.DEFAULT_RANGES)
        anchor = synthesize.read_anchor(engine)
        pairs = [] if anchor is None else ready_pairs(engine, scope_id, codes, ranges, anchor)
        summary["synth_pairs"] = len(pairs)
        summary["synth_pairs_total"] = len(codes) * len(ranges)
        if pairs:
            st = synthesize.run(engine, cfg, pairs=pairs, provider=provider, dry_run=dry_run,
                                scope_id=scope_id, **({"workers": synth_workers} if synth_workers else {}))
            summary["steps"].append({"task": "synthesize", **{k: st[k] for k in ("run_id", "calls", "written",
                                                                                    "skipped_same", "low_sample",
                                                                                    "errors", "tok_in", "tok_out",
                                                                                    "pairs", "pairs_clean")}})
            if not dry_run and st["errors"]:
                summary["complete"] = False
        elif anchor is None:
            summary["steps"].append({"task": "synthesize", "skipped": "meta_kv 里没有 anchor"})
        else:
            summary["steps"].append({"task": "synthesize", "skipped": "没有就绪的 (code, range)"})
    if not dry_run:
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
    ap.add_argument("--max-items", type=int)
    ap.add_argument("--skip-synth", action="store_true")
    ap.add_argument("--ranges", help="Layer B 只做这些区间，如 d7,d30")
    ap.add_argument("--synth-workers", type=int, help="Layer B (code, range) 并行线程数，默认 8")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    engine = make_engine()
    cfg = config.load(_allow_missing_key=args.dry_run)
    out = run(engine, cfg, args.scope, dry_run=args.dry_run, budget_requests=args.budget_requests,
              max_items=args.max_items, skip_synth=args.skip_synth,
              ranges=[r.strip() for r in args.ranges.split(",")] if args.ranges else None,
              synth_workers=args.synth_workers)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0 if args.dry_run or out.get("complete", False) else 2


if __name__ == "__main__":
    raise SystemExit(main())
