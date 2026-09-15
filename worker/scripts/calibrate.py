"""放量前的一致性实验（ADR-0020 步骤 11）。**不写 annotations**，结果只落文件。

    cd worker && .venv/Scripts/python -m scripts.calibrate --scope <scope_id> --n 300
    cd worker && .venv/Scripts/python -m scripts.calibrate --codes 3033,7226 --from 2026-08-01 --to 2026-08-25

三个实验，各自回答一个「放量前必须知道」的问题：

1. **b=1 vs b=30**：这只推理模型在 30 条/批下是否退化。文献（arXiv 2604.03684）里
   OpenAI 的推理模型在大批下会崩，Luna 没被测过。同一批样本单条发一遍、30 条一批发一遍，
   比 `relevance` 与 `attitude` 的一致率。
2. **v1 vs v2**：七维 Prompt 有没有把态度判断带偏。同样本用 v1 Prompt（b=30）再跑一遍，
   比 `attitude` 一致率。
3. **规则误杀抽查**：抽 100 条被 `offpool_stock_only` 剔掉的评论，导出给人翻。

阈值：1、2 任一 <90% ⇒ 先改 Prompt 再放量；3 误杀 >5% ⇒ 关掉第 5 条规则。
一致率是**可靠性**不是准确率，只写 `.scratch/llm-90d/`，不进页面（ADR-0019）。

## 花多少钱

约 300（b=1）＋ 10（b=30）＋ 10（v1）≈ 320 次请求。`--n` 可调小。

## 输出

- `.scratch/llm-90d/calibration-<时间>.json`：只有计数与比率，可进 git。
- `<数据目录>/futu-radar/calibration-<时间>-disagreements.csv`：不一致样本的正文与两边标签，
  含用户正文，**不进 git**（写在仓库树外）。
"""

import argparse
import csv
import json
import logging
import os
import random
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import clock  # noqa: E402
from ai import config, prefilter, schemas  # noqa: E402
from ai.lexicon import offpool_stocks, product_aliases  # noqa: E402
from ai.prompts import get as get_prompt  # noqa: E402
from ai.providers import build as build_provider  # noqa: E402
from jobs import annotate  # noqa: E402
from jobs.import_dump import pool_codes  # noqa: E402
from radar_db import default_data_dir, make_engine  # noqa: E402
from radar_db.scope_jobs import scope_condition
from radar_db.schema import analysis_scopes, annotation_jobs  # noqa: E402

log = logging.getLogger("worker.calibrate")
OUT_DIR = REPO_ROOT / ".scratch" / "llm-90d"


def _script(text):
    if any("\u3040" <= ch <= "\u30ff" for ch in text):
        return "ja"
    trad = sum(ch in "這隻個們說為對於會來時間過還發現經開關無麼與" for ch in text)
    simp = sum(ch in "这只个们说为对于会来时间过还发现经开关无么与" for ch in text)
    canto = any(w in text for w in ("呢隻", "唔", "咁", "嘅", "係", "冇", "啲", "佬", "喺"))
    if canto:
        return "yue"
    return "zh-Hant" if trad > simp else "zh-Hans"


def stratified_sample(rows, n, ownership, seed=42):
    """按 简繁粤 × 长短 × own/peer × 有无父评论 分层，各层尽量均匀。"""
    rnd = random.Random(seed)
    buckets = {}
    for r in rows:
        key = (_script(r.content), "long" if len(r.content) >= 20 else "short",
               ownership.get(r.code, "peer"), bool(r.parent_content))
        buckets.setdefault(key, []).append(r)
    for b in buckets.values():
        rnd.shuffle(b)
    out, i = [], 0
    keys = sorted(buckets)
    while len(out) < n and any(buckets.values()):
        k = keys[i % len(keys)]
        if buckets[k]:
            out.append(buckets[k].pop())
        i += 1
    return out


def candidates(engine, args, ownership):
    if args.scope:
        with engine.connect() as conn:
            sc = conn.execute(select(analysis_scopes).where(analysis_scopes.c.scope_id == args.scope)).mappings().one()
            ids = [r[0] for r in conn.execute(
                select(annotation_jobs.c.target_id).where(scope_condition(args.scope),
                                                          annotation_jobs.c.task == "comment_product"))]
        codes = json.loads(sc["codes_json"])
        since, until = sc["date_from"], sc["date_to"] + timedelta(days=1)
        rows = [r for r in annotate._comment_candidates(engine, codes=codes, since=since, until=until)
                if r.comment_id in set(ids)]
    else:
        codes = [c.strip() for c in args.codes.split(",")]
        since = datetime.strptime(args.from_, "%Y-%m-%d")
        until = datetime.strptime(args.to, "%Y-%m-%d") + timedelta(days=1)
        rows = list(annotate._comment_candidates(engine, codes=codes, since=since, until=until))
    return rows


def label_batch(provider, prompt, schema_version, items, batch_size):
    """`items`：`[(row, payload)]`。返回 `{item_id: 标注对象}`；失败的批整批跳过并计数。"""
    out, failed = {}, 0
    for i in range(0, len(items), batch_size):
        chunk = items[i:i + batch_size]
        payloads = [p for _r, p in chunk]
        try:
            comp = provider.complete_json(prompt.SYSTEM, prompt.user_message(payloads),
                                          schemas.batch_json_schema("comment_product", schema_version),
                                          "comment_product_batch")
            out.update(schemas.parse_batch("comment_product", comp.data,
                                           [p["item_id"] for p in payloads], schema_version))
        except Exception as exc:  # noqa: BLE001  实验脚本：记下来继续
            failed += len(chunk)
            log.warning("一批失败（%d 条）：%s", len(chunk), str(exc)[:200])
    return out, failed


def agreement(a, b, field):
    both = [k for k in a if k in b]
    if not both:
        return None, 0
    same = sum(getattr(a[k], field) == getattr(b[k], field) for k in both)
    return round(same / len(both), 4), len(both)


def main(argv=None):
    ap = argparse.ArgumentParser(description="放量前一致性实验（不写 annotations）")
    ap.add_argument("--scope")
    ap.add_argument("--codes")
    ap.add_argument("--from", dest="from_")
    ap.add_argument("--to")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--batch", type=int, default=30)
    ap.add_argument("--skip-v1", action="store_true")
    ap.add_argument("--skip-single", action="store_true", help="跳过 b=1（最贵的一项）")
    args = ap.parse_args(argv)
    if not args.scope and not (args.codes and args.from_ and args.to):
        ap.error("给 --scope，或 --codes/--from/--to")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    engine = make_engine()
    cfg = config.load()
    provider = build_provider(cfg)
    ownership = pool_codes()
    stamp = clock.now().strftime("%Y%m%dT%H%M%S")

    rows = candidates(engine, args, ownership)
    plex = product_aliases.ProductLexicon()
    slex = offpool_stocks.StockLexicon(offpool_stocks.load_from_db(engine, set(ownership)))
    pf = prefilter.Prefilter(plex, slex)
    kept, dropped_offpool = [], []
    for r in rows:
        d = pf.classify(r.content, r.code, comment_id=r.comment_id, author_uid=r.author_uid, feed_id=r.feed_id)
        if d.rule == "offpool_stock_only":
            dropped_offpool.append(r)
        elif not d.dropped:
            kept.append(r)

    sample = stratified_sample(kept, args.n, ownership)
    items = []
    for r in sample:
        job = {"target_id": r.comment_id, "subject_code": r.code}
        src = {"text": r.content, "title": r.title, "parent": r.parent_content,
               "post_content": getattr(r, "post_content", None)}
        items.append((r, annotate._build_payload("comment_product", job, src)))
    log.info("样本 %d 条（候选 %d，规则放行 %d，仅个股剔除 %d）", len(items), len(rows), len(kept), len(dropped_offpool))

    v2 = get_prompt("comment_product", "comment-product-v2")
    report = {"stamp": stamp, "n": len(items), "candidates": len(rows), "kept_after_rules": len(kept),
              "offpool_dropped": len(dropped_offpool),
              "strata": dict(Counter(f"{_script(r.content)}|{ownership.get(r.code)}" for r, _ in items))}

    # 实验 1：b=30 基线 与 b=1
    b30, f30 = label_batch(provider, v2, "v2", items, args.batch)
    report["v2_b30"] = {"labeled": len(b30), "failed": f30,
                        "attitude_dist": dict(Counter(a.attitude for a in b30.values())),
                        "relevance_dist": dict(Counter(a.relevance for a in b30.values())),
                        "needs_review_rate": round(sum(a.needs_review for a in b30.values()) / max(len(b30), 1), 4),
                        "compliance_hit_rate": round(sum(bool(a.compliance_tags) for a in b30.values()) / max(len(b30), 1), 4)}
    disagreements = []
    if not args.skip_single:
        b1, f1 = label_batch(provider, v2, "v2", items, 1)
        rel, n_rel = agreement(b30, b1, "relevance")
        att, n_att = agreement(b30, b1, "attitude")
        report["b1_vs_b30"] = {"labeled_b1": len(b1), "failed_b1": f1,
                               "relevance_agreement": rel, "attitude_agreement": att, "n": n_att}
        for k in b30:
            if k in b1 and (b30[k].attitude != b1[k].attitude or b30[k].relevance != b1[k].relevance):
                disagreements.append(("b1_vs_b30", k, b30[k].relevance, b30[k].attitude, b1[k].relevance, b1[k].attitude))

    # 实验 2：v1 vs v2
    if not args.skip_v1:
        v1 = get_prompt("comment_product", "comment-product-v1")
        v1_items = [(r, {k: v for k, v in p.items() if k != "post_context"}) for r, p in items]
        bv1, fv1 = label_batch(provider, v1, "v1", v1_items, args.batch)
        att, n_att = agreement(b30, bv1, "attitude")
        rel, _ = agreement(b30, bv1, "relevance")
        report["v1_vs_v2"] = {"labeled_v1": len(bv1), "failed_v1": fv1,
                              "attitude_agreement": att, "relevance_agreement": rel, "n": n_att}
        for k in b30:
            if k in bv1 and b30[k].attitude != bv1[k].attitude:
                disagreements.append(("v1_vs_v2", k, b30[k].relevance, b30[k].attitude, bv1[k].relevance, bv1[k].attitude))

    verdict = []
    for key in ("b1_vs_b30", "v1_vs_v2"):
        if key in report and report[key]["attitude_agreement"] is not None and report[key]["attitude_agreement"] < 0.9:
            verdict.append(f"{key} 态度一致率 {report[key]['attitude_agreement']:.1%} < 90%：先改 Prompt 再放量")
    report["verdict"] = verdict or ["一致率达标；仅个股误杀率待人工翻 CSV 后填写"]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"calibration-{stamp}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    # 含原文的两份清单写在仓库树外。
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    by_id = {f"comment:{r.comment_id}|product:{r.code}": r for r, _ in items}
    with open(data_dir / f"calibration-{stamp}-disagreements.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["experiment", "item_id", "code", "text", "rel_a", "att_a", "rel_b", "att_b"])
        for exp, k, ra, aa, rb, ab in disagreements:
            r = by_id[k]
            w.writerow([exp, k, r.code, r.content, ra, aa, rb, ab])
    rnd = random.Random(7)
    rnd.shuffle(dropped_offpool)
    with open(data_dir / f"calibration-{stamp}-offpool-sample.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["comment_id", "code", "text", "误杀？(人工填 Y/N)"])
        for r in dropped_offpool[:100]:
            w.writerow([r.comment_id, r.code, r.content, ""])

    print(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"\n不一致样本与仅个股抽查 CSV 在：{data_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
