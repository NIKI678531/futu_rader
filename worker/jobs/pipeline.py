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

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai import config  # noqa: E402
from ai.providers import build as build_provider  # noqa: E402
from jobs import annotate, audit, synthesize  # noqa: E402
from radar_db import make_engine  # noqa: E402
from radar_db.schema import analysis_scopes, annotation_jobs  # noqa: E402
from radar_db.scope_jobs import scope_condition

log = logging.getLogger("worker.pipeline")

TASK_ORDER = ("comment_product", "kol_comment_opinion", "post_annotation")


def run(engine, cfg, scope_id, *, provider=None, dry_run=False, budget_requests=None, max_items=None,
        skip_synth=False, ranges=None):
    with engine.connect() as conn:
        scope = conn.execute(select(analysis_scopes).where(analysis_scopes.c.scope_id == scope_id)).mappings().first()
    if scope is None:
        raise SystemExit(f"没有这个 scope：{scope_id}。先跑 python -m jobs.extract")
    codes = json.loads(scope["codes_json"])
    summary = {"scope_id": scope_id, "codes": len(codes), "steps": []}

    provider = provider or (None if dry_run else build_provider(cfg))
    for task in TASK_ORDER:
        pending = annotate.pending_count(engine, task, scope_id)
        if pending == 0:
            summary["steps"].append({"task": task, "skipped": "队列为空"})
            continue
        if dry_run:
            summary["steps"].append({"task": task, "estimate": annotate.estimate(engine, cfg, task, scope_id)})
            continue
        stats = annotate.run(engine, cfg, task=task, max_items=max_items or pending, provider=provider,
                             scope_id=scope_id, budget_requests=budget_requests)
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
    if not skip_synth and (dry_run or summary["complete"]):
        st = synthesize.run(engine, cfg, codes=codes, ranges=ranges or synthesize.DEFAULT_RANGES,
                            provider=provider, dry_run=dry_run)
        summary["steps"].append({"task": "synthesize", **{k: st[k] for k in ("run_id", "calls", "written",
                                                                                "skipped_same", "low_sample",
                                                                                "errors", "tok_in", "tok_out")}})
        if not dry_run and st["errors"]:
            summary["complete"] = False
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
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    engine = make_engine()
    cfg = config.load(_allow_missing_key=args.dry_run)
    out = run(engine, cfg, args.scope, dry_run=args.dry_run, budget_requests=args.budget_requests,
              max_items=args.max_items, skip_synth=args.skip_synth,
              ranges=[r.strip() for r in args.ranges.split(",")] if args.ranges else None)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0 if args.dry_run or out.get("complete", False) else 2


if __name__ == "__main__":
    raise SystemExit(main())
