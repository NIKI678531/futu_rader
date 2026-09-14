"""质量与成本报表（ADR-0020 步骤 18）—— 只读，不改任何结论。

    python -m jobs.audit --report [--scope <id>]        # 完成率、dead-letter、needs_review 率、证据定位率……
    python -m jobs.audit --lexicon-recall [--scope <id>] # 合规词表命中但模型未标的样本清单
    python -m jobs.audit --sample 100 --xlsx            # 随机 100 条「原文＋标签＋证据」给人翻看

## 它能说什么、不能说什么

能说：跑了多少、错了多少、模型自己举手（needs_review）多少、引文有多少能在原文里定位到、
各产品的态度分布长什么样、花了多少 token。这些是**可靠性与成本**指标。

不能说：准确率。没有金标就没有分母（ADR-0019）。这份报表里不出现「准确率」三个字，
抽样导出的 Excel 是「只看不批」—— 看了之后要下线某条用 `review.py --reject`，不在这里改。

## 词表召回审计

runbook §20.3 的合规词表（`ai/lexicon/compliance_zh.py`）现在不再决定谁去问模型（每条评论
本来就要去），它退成审计工具：列出「词表命中某类、但模型 `compliance_tags` 为空」的样本，
供人翻看模型有没有漏。词表允许误报，所以清单里多数会是误报 —— 那也是信息：告诉你词表该修哪。
"""

import argparse
import csv
import json
import logging
import os
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import clock  # noqa: E402
from ai.lexicon import compliance_zh  # noqa: E402
from radar_db import default_data_dir, make_engine  # noqa: E402
from radar_db.annotations_read import current_annotations  # noqa: E402
from radar_db.schema import (  # noqa: E402
    analysis_scopes,
    annotation_evidence,
    annotation_jobs,
    annotation_runs,
    annotations,
    comments,
    feeds,
)

log = logging.getLogger("worker.audit")


def _scope_filter(q, scope_id):
    return q.where(annotation_jobs.c.scope_id == scope_id) if scope_id else q


def report(engine, scope_id=None):
    out = {"scope_id": scope_id, "generated_at": clock.now().strftime("%Y-%m-%d %H:%M")}
    with engine.connect() as conn:
        # 队列
        q = _scope_filter(select(annotation_jobs.c.task, annotation_jobs.c.status, func.count())
                          .group_by(annotation_jobs.c.task, annotation_jobs.c.status), scope_id)
        queue = defaultdict(dict)
        for task, status, n in conn.execute(q):
            queue[task][status] = n
        out["queue"] = {t: {**st, "completion": round(st.get("done", 0) / max(sum(st.values()), 1), 4)}
                        for t, st in queue.items()}

        # dead-letter 原因 Top
        q = _scope_filter(select(annotation_jobs.c.last_error, func.count())
                          .where(annotation_jobs.c.status == "dead")
                          .group_by(annotation_jobs.c.last_error)
                          .order_by(func.count().desc()).limit(5), scope_id)
        out["dead_letter_top"] = [{"error": (e or "")[:120], "n": n} for e, n in conn.execute(q)]

        # run 用量
        runs = conn.execute(select(annotation_runs).order_by(annotation_runs.c.started_at.desc()).limit(50)).mappings().all()
        usage = defaultdict(lambda: {"runs": 0, "tok_in": 0, "tok_out": 0, "tok_reason": 0, "unknown_usage_runs": 0,
                                     "input": 0, "success": 0, "error": 0})
        for r in runs:
            u = usage[r["task"]]
            u["runs"] += 1
            u["input"] += r["input_count"] or 0
            u["success"] += r["success_count"] or 0
            u["error"] += r["error_count"] or 0
            if r["token_input"] is None and r["provider"] != "rule":
                u["unknown_usage_runs"] += 1
            else:
                u["tok_in"] += r["token_input"] or 0
                u["tok_out"] += r["token_output"] or 0
                u["tok_reason"] += r["token_reasoning"] or 0
        out["usage_last_50_runs"] = dict(usage)
        out["estimated_cost"] = None  # 网关没有给价格。写 None，不写 0。

        # 标注质量信号
        total = conn.execute(select(func.count()).select_from(annotations)).scalar_one()
        by_state = dict(conn.execute(select(annotations.c.review_state, func.count()).group_by(annotations.c.review_state)).all())
        by_kind = dict(conn.execute(select(annotations.c.kind, func.count()).group_by(annotations.c.kind)).all())
        rule_rows = conn.execute(
            select(func.count()).select_from(annotations.join(annotation_runs, annotation_runs.c.run_id == annotations.c.run_id))
            .where(annotation_runs.c.provider == "rule")
        ).scalar_one()
        out["annotations"] = {
            "rows": total, "by_review_state": by_state, "by_kind": by_kind,
            "needs_review_rate": round(by_state.get("needs_review", 0) / max(total, 1), 4),
            "rule_rows": rule_rows,
        }
        # 规则剔除分布
        tq = conn.execute(select(annotations.c.value_json).where(annotations.c.kind == "text_quality")).all()
        out["prefilter_dropped_by_rule"] = dict(Counter(json.loads(v[0]).get("rule") for v in tq))

        # 证据定位率：相关（relevant）的 relevance 行里带证据的比例
        rel_ids = [r[0] for r in conn.execute(
            select(annotations.c.annotation_id).where(annotations.c.kind == "relevance",
                                                      annotations.c.value_json == '"relevant"'))]
        with_ev = 0
        for i in range(0, len(rel_ids), 900):
            chunk = rel_ids[i:i + 900]
            with_ev += conn.execute(
                select(func.count(func.distinct(annotation_evidence.c.annotation_id)))
                .where(annotation_evidence.c.annotation_id.in_(chunk))
            ).scalar_one()
        out["evidence_located_rate"] = round(with_ev / len(rel_ids), 4) if rel_ids else None

    # 各产品态度分布（现行结论）
    att = current_annotations(engine, "attitude", "comment")
    rel = current_annotations(engine, "relevance", "comment")
    dist = defaultdict(Counter)
    for unit, a in att.items():
        if (rel.get(unit) or {}).get("value") == "relevant":
            dist[unit[1]][a["value"]] += 1
    irr = Counter(unit[1] for unit, r in rel.items() if r["value"] == "irrelevant")
    ctx = Counter(unit[1] for unit, r in rel.items() if r["value"] == "needs_context")
    out["attitude_by_product"] = {
        code: {**c, "irrelevant": irr.get(code, 0), "needs_context": ctx.get(code, 0)}
        for code, c in sorted(dist.items(), key=lambda kv: -sum(kv[1].values()))[:40]
    }
    comp = current_annotations(engine, "compliance", "comment")
    tags = Counter(t for c in comp.values() for t in ((c["value"] or {}).get("tags") or []))
    out["compliance"] = {"scanned_units": len(comp), "hits_by_tag": dict(tags)}
    return out


def lexicon_recall(engine, scope_id=None, limit=200):
    """词表命中但模型未标任何合规类的评论。返回 `[{comment_id, code, tags_by_lexicon, text}]`。"""
    comp = current_annotations(engine, "compliance", "comment")
    misses = [(unit, c) for unit, c in comp.items() if not ((c["value"] or {}).get("tags") or [])]
    ids = [unit[0] for unit, _ in misses]
    texts = {}
    with engine.connect() as conn:
        for i in range(0, len(ids), 900):
            for cid, content in conn.execute(select(comments.c.comment_id, comments.c.content)
                                             .where(comments.c.comment_id.in_(ids[i:i + 900]))):
                texts[cid] = content or ""
    out = []
    for (cid, code), _c in misses:
        hits = compliance_zh.hits(texts.get(cid, ""))
        if hits:
            out.append({"comment_id": cid, "code": code, "lexicon_hits": hits, "text": texts.get(cid, "")})
            if len(out) >= limit:
                break
    return out


def sample_rows(engine, n=100, seed=7):
    """随机 n 个判定单元：原文＋全部现行标签＋证据引文。给人翻，不批。"""
    rel = current_annotations(engine, "relevance", "comment")
    att = current_annotations(engine, "attitude", "comment")
    asp = current_annotations(engine, "aspect", "comment")
    mkt = current_annotations(engine, "market_direction", "comment")
    comp = current_annotations(engine, "compliance", "comment")
    units = sorted(rel)
    random.Random(seed).shuffle(units)
    picked = units[:n]
    ids = [u[0] for u in picked]
    texts, quotes = {}, defaultdict(list)
    with engine.connect() as conn:
        for i in range(0, len(ids), 900):
            for cid, content, code in conn.execute(
                select(comments.c.comment_id, comments.c.content, feeds.c.code)
                .select_from(comments.join(feeds, feeds.c.feed_id == comments.c.feed_id))
                .where(comments.c.comment_id.in_(ids[i:i + 900]))
            ):
                texts[cid] = content or ""
        ann_ids = [rel[u]["annotation_id"] for u in picked]
        for i in range(0, len(ann_ids), 900):
            for aid, q in conn.execute(select(annotation_evidence.c.annotation_id, annotation_evidence.c.quote_text)
                                       .where(annotation_evidence.c.annotation_id.in_(ann_ids[i:i + 900]))):
                quotes[aid].append(q)
    rows = []
    for u in picked:
        r = rel[u]
        rows.append({
            "comment_id": u[0], "code": u[1], "text": texts.get(u[0], ""),
            "relevance": r["value"], "attitude": (att.get(u) or {}).get("value"),
            "aspects": ",".join((asp.get(u) or {}).get("value") or []),
            "market_direction": (mkt.get(u) or {}).get("value"),
            "compliance_tags": ",".join(((comp.get(u) or {}).get("value") or {}).get("tags") or []),
            "evidence": " | ".join(quotes.get(r["annotation_id"], [])),
            "review_state": r["review_state"],
        })
    return rows


def _write_table(rows, path, xlsx):
    if not rows:
        return None
    if xlsx:
        try:
            from openpyxl import Workbook
        except ImportError:
            log.warning("没有 openpyxl，改写 CSV（pip install openpyxl 可出 Excel）")
            xlsx = False
    if xlsx:
        wb = Workbook()
        ws = wb.active
        ws.append(list(rows[0].keys()))
        for r in rows:
            ws.append([json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in r.values()])
        p = path.with_suffix(".xlsx")
        wb.save(p)
        return p
    p = path.with_suffix(".csv")
    with open(p, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v) for k, v in r.items()})
    return p


def main(argv=None):
    ap = argparse.ArgumentParser(description="标注质量与成本报表（只读）")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--lexicon-recall", action="store_true")
    ap.add_argument("--sample", type=int, default=0, help="随机导出 N 个判定单元给人翻看")
    ap.add_argument("--xlsx", action="store_true", help="导出 Excel（需 openpyxl），默认 CSV")
    ap.add_argument("--scope")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    engine = make_engine()
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    stamp = clock.now().strftime("%Y%m%dT%H%M%S")

    if args.report or not (args.lexicon_recall or args.sample):
        rep = report(engine, args.scope)
        print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
        # 报表只有计数，可进 .scratch/
        out_dir = REPO_ROOT / ".scratch" / "llm-90d"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"audit-{stamp}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str),
                                                     encoding="utf-8")
    if args.lexicon_recall:
        rows = lexicon_recall(engine, args.scope)
        p = _write_table(rows, data_dir / f"lexicon-recall-{stamp}", args.xlsx)
        print(f"词表命中但模型未标：{len(rows)} 条 → {p}（含原文，仓库树外）")
    if args.sample:
        rows = sample_rows(engine, args.sample)
        p = _write_table(rows, data_dir / f"sample-{stamp}", args.xlsx)
        print(f"抽样 {len(rows)} 条 → {p}（含原文，仓库树外；只看不批）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
