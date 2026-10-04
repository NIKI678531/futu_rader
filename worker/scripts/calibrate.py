"""放量前的一致性实验（ADR-0020 步骤 11）。**不写 annotations**，结果只落文件。

    cd worker && .venv/Scripts/python -m scripts.calibrate --scope <scope_id> --n 400 --batch 5 --skip-v1
    cd worker && .venv/Scripts/python -m scripts.calibrate --codes 3033,7226 --from 2026-08-01 --to 2026-08-25

三个实验，各自回答一个「放量前必须知道」的问题：

1. **b=1 vs b=5**：同一批样本单条发一遍、5 条一批发一遍，比
   `relevance`／`attitude` 一致率与实测吞吐；质量达标且吞吐至少 3 倍才可启用 b=5。
   `batch=30` 不支持。System One 的硬上限为 1，因此不用于批量回填。
2. **v1 vs 当前 Prompt（可选诊断）**：七维 Prompt 有没有把态度判断带偏。同样本用 v1 Prompt 再跑一遍，
   比 `attitude` 一致率。
3. **规则误杀抽查**：抽 100 条被 `offpool_stock_only` 剔掉的评论，导出给人翻。

阈值：1、2 任一 <90% ⇒ 先改 Prompt 再放量；3 误杀 >5% ⇒ 关掉第 5 条规则。
一致率是**可靠性**不是准确率，只写 `.scratch/llm-90d/`，不进页面（ADR-0019）。

## 花多少钱

生产放量样本约 400（b=1）＋ 80（b=5）≈ 480 次请求；`--skip-v1` 关闭额外 v1 对照。

## 输出

- `.scratch/llm-90d/calibration-<时间>.json`：只有计数与比率，可进 git。
- `<数据目录>/futu-radar/calibration-<时间>-gold/`：本次 b=5 输出对应的盲标表与模型标签表，
  不写 annotations，供 400 条人工金标门禁使用；含用户正文，**不进 git**。
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
import time
from contextlib import ExitStack
from dataclasses import replace
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import and_, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import clock  # noqa: E402
from ai import config, prefilter, schemas, evidence, release_regressions  # noqa: E402
from ai.batching import BatchPolicy, pack_items
from ai.providers.base import RunControl, RunStopped
from ai.lexicon import offpool_stocks, product_aliases  # noqa: E402
from ai.prompts import get as get_prompt  # noqa: E402
from ai.providers import build as build_provider  # noqa: E402
from jobs import annotate, extract  # noqa: E402
from jobs.import_dump import pool_codes  # noqa: E402
from radar_db import default_data_dir, make_engine  # noqa: E402
from radar_db.comment_routes import (  # noqa: E402
    COMMENT_ROUTE_VERSION,
    product_pool_digest,
    report_policy_fields,
    require_ready as require_comment_routes_ready,
    route_exists_predicate,
)
from radar_db.scope_jobs import scope_condition
from radar_db.leases import WorkerLease
from radar_db.schema import analysis_scopes, annotation_jobs, comments, feeds  # noqa: E402
from radar_db.time_windows import utc_naive_to_hkt  # noqa: E402

log = logging.getLogger("worker.calibrate")
OUT_DIR = REPO_ROOT / ".scratch" / "llm-90d"


def effective_batch_size(provider, requested):
    """Respect provider-declared hard limits (System One currently declares 1)."""
    limit = getattr(provider, "max_batch_size", None)
    return min(requested, limit) if limit else requested


def batch_release_gate(*, sample_count, failed_single, failed_batch,
                       relevance_agreement, attitude_agreement,
                       throughput_multiplier, effective_batch_size):
    """Decide whether the five-item configuration is safe and materially faster."""
    inputs = {
        "sample_count": sample_count,
        "failed_single": failed_single,
        "failed_batch": failed_batch,
        "relevance_agreement": relevance_agreement,
        "attitude_agreement": attitude_agreement,
        "throughput_multiplier": throughput_multiplier,
        "effective_batch_size": effective_batch_size,
    }
    passed = (
        sample_count >= 300
        and failed_single == 0 and failed_batch == 0
        and relevance_agreement is not None and relevance_agreement >= 0.90
        and attitude_agreement is not None and attitude_agreement >= 0.90
        and throughput_multiplier is not None and throughput_multiplier >= 3.0
        and effective_batch_size == 5
    )
    return {"passed": passed, "inputs": inputs}


def calibration_policy(cfg, *, requested_batch_size, effective_batch_size,
                       max_input_tokens, max_payload_bytes, max_output_tokens):
    """Canonical report policy consumed by ``jobs.analyze.check_calibration``."""
    return {
        "model": cfg.model,
        "promptVersion": cfg.prompt_version,
        "schemaVersion": cfg.schema_version,
        "taxonomyVersion": cfg.taxonomy_version,
        "batchSize": effective_batch_size,
        "requestedBatchSize": requested_batch_size,
        "maxInputTokens": max_input_tokens,
        "maxPayloadBytes": max_payload_bytes,
        "maxOutputTokens": max_output_tokens,
        # These values are deterministic for the deployed rule/catalog.  The
        # runtime command additionally requires the database readiness marker.
        "commentRouteVersion": COMMENT_ROUTE_VERSION,
        "productPoolDigest": product_pool_digest(),
    }


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


def calibration_sample(rows, n, ownership, seed=42):
    """Reserve slots for every fixed complaint case inside the requested sample."""
    fixed = release_regressions.comment_case_rows()
    if n < len(fixed):
        raise ValueError(
            f"Calibration sample must have at least {len(fixed)} rows for fixed regressions"
        )
    ordinary = stratified_sample(rows, n - len(fixed), ownership, seed=seed)
    return ordinary + fixed


def candidates(engine, args, ownership):
    require_comment_routes_ready(engine)
    if args.scope:
        parent = comments.alias("parent")
        grandparent = comments.alias("grandparent")
        great_grandparent = comments.alias("great_grandparent")
        with engine.connect() as conn:
            conn.execute(
                select(analysis_scopes.c.scope_id).where(analysis_scopes.c.scope_id == args.scope)
            ).scalar_one()
            rows = conn.execute(
                select(
                    comments.c.comment_id,
                    comments.c.content,
                    comments.c.author_uid,
                    comments.c.feed_id,
                    annotation_jobs.c.subject_code.label("code"),
                    feeds.c.posted_at,
                    feeds.c.title,
                    feeds.c.content.label("post_content"),
                    parent.c.content.label("parent_content"),
                    grandparent.c.content.label("grandparent_content"),
                    great_grandparent.c.content.label("great_grandparent_content"),
                )
                .select_from(
                    annotation_jobs
                    .join(
                        comments,
                        and_(
                            annotation_jobs.c.target_type == "comment",
                            annotation_jobs.c.target_id == comments.c.comment_id,
                        ),
                    )
                    .join(feeds, feeds.c.feed_id == comments.c.feed_id)
                    .outerjoin(parent, parent.c.comment_id == comments.c.reply_to_comment_id)
                    .outerjoin(grandparent, grandparent.c.comment_id == parent.c.reply_to_comment_id)
                    .outerjoin(
                        great_grandparent,
                        great_grandparent.c.comment_id == grandparent.c.reply_to_comment_id,
                    )
                )
                .where(
                    scope_condition(args.scope),
                    annotation_jobs.c.task == "comment_product",
                    annotation_jobs.c.status != "superseded",
                    route_exists_predicate(
                        annotation_jobs.c.target_id,
                        annotation_jobs.c.subject_code,
                    ),
                )
                .distinct()
                .order_by(annotation_jobs.c.target_id, annotation_jobs.c.subject_code)
            ).all()
    else:
        codes = [c.strip() for c in args.codes.split(",")]
        since = datetime.fromisoformat(args.from_)
        until = datetime.fromisoformat(args.to) + timedelta(days=1)
        raw = extract._comment_candidates_hkt(
            engine,
            codes=codes,
            since=since,
            until=until,
            comment_routes=True,
        )
        rows, seen = [], set()
        for row in raw:
            key = (row.comment_id, row.code)
            if key not in seen:
                seen.add(key)
                rows.append(row)
    return rows


def label_batch(provider, prompt, schema_version, items, batch_size, provenance=None):
    """`items`：`[(row, payload)]`。返回 `{item_id: 标注对象}`；失败的批整批跳过并计数。"""
    out, failed = {}, 0
    batch_size = effective_batch_size(provider, batch_size)
    policy = BatchPolicy(batch_size, getattr(provider, "max_input_tokens", 8000),
                         getattr(provider, "max_payload_bytes", 12288))
    batches, oversized = pack_items(items,
        key_of=lambda item: (
            item[0].code,
            utc_naive_to_hkt(item[0].posted_at).date()
            if getattr(item[0], "posted_at", None) else None,
        ),
        payload_of=lambda item: item[1], system=prompt.SYSTEM, render=prompt.user_message,
        schema=schemas.batch_json_schema("comment_product", schema_version), policy=policy)
    failed += len(oversized)
    for index, chunk in enumerate(batches):
        payloads = [p for _r, p in chunk]
        try:
            comp = provider.complete_json(prompt.SYSTEM, prompt.user_message(payloads),
                                          schemas.batch_json_schema("comment_product", schema_version),
                                          "comment_product_batch")
            parsed = schemas.parse_batch("comment_product", comp.data,
                                         [p["item_id"] for p in payloads], schema_version)
            for row, payload in chunk:
                item = parsed[payload["item_id"]]
                if provenance is not None:
                    provenance[payload["item_id"]] = comp.model
                quotes = [item.evidence] if item.evidence else []
                if schema_version == "v2" and item.compliance_evidence:
                    quotes.append(item.compliance_evidence)
                required_missing = (item.relevance == "relevant" and not item.evidence)
                if schema_version == "v2" and item.compliance_tags and not item.compliance_evidence:
                    required_missing = True
                if required_missing or any(not evidence.locate(quote, row.content).found for quote in quotes):
                    failed += 1
                else:
                    out[payload["item_id"]] = item
        except RunStopped:
            failed += sum(len(batch) for batch in batches[index:])
            break
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
    with ExitStack() as resources:
        return _main(argv, resources)


def _main(argv, resources):
    ap = argparse.ArgumentParser(description="放量前一致性实验（不写 annotations）")
    ap.add_argument("--scope")
    ap.add_argument("--codes")
    ap.add_argument("--from", dest="from_")
    ap.add_argument("--to")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--batch", type=int, choices=(5,), default=5,
                    help="批量候选固定为 5；batch=30 已禁用")
    ap.add_argument("--max-http-requests", type=int, required=True)
    ap.add_argument("--max-input-tokens", type=int, default=8000)
    ap.add_argument("--max-payload-bytes", type=int, default=12288)
    ap.add_argument("--max-output-tokens", type=int, default=8192)
    ap.add_argument("--skip-v1", action="store_true")
    ap.add_argument("--skip-single", action="store_true", help="跳过 b=1（最贵的一项）")
    args = ap.parse_args(argv)
    if min(args.n, args.max_http_requests, args.batch, args.max_input_tokens, args.max_payload_bytes, args.max_output_tokens) < 1:
        ap.error("Limits must be positive")
    if not args.scope and not (args.codes and args.from_ and args.to):
        ap.error("给 --scope，或 --codes/--from/--to")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    engine = make_engine()
    cfg = replace(config.load(), max_input_tokens=args.max_input_tokens, max_payload_bytes=args.max_payload_bytes,
                  max_output_tokens=args.max_output_tokens, grouped_batches=True)
    if not cfg.prompt_version.startswith("comment-product-v") or cfg.schema_version != "v2":
        ap.error("Grouped calibration requires a comment-product prompt and schema v2")
    control = RunControl(args.max_http_requests)
    resources.enter_context(WorkerLease(engine, "own-analysis"))
    resources.enter_context(control.interruptible())
    provider = build_provider(cfg, control=control)
    provider.max_input_tokens = args.max_input_tokens
    provider.max_payload_bytes = args.max_payload_bytes
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

    try:
        sample = calibration_sample(kept, args.n, ownership)
    except ValueError as exc:
        ap.error(str(exc))
    items = []
    for r in sample:
        job = {"target_id": r.comment_id, "subject_code": r.code}
        parents = [
            value for value in (
                getattr(r, "parent_content", None),
                getattr(r, "grandparent_content", None),
                getattr(r, "great_grandparent_content", None),
            ) if value
        ]
        src = {"text": r.content, "title": r.title,
               "parent": parents[0] if parents else None, "parents": parents,
               "post_content": getattr(r, "post_content", None)}
        items.append((r, annotate._build_payload("comment_product", job, src)))
    log.info("样本 %d 条（候选 %d，规则放行 %d，仅个股剔除 %d）", len(items), len(rows), len(kept), len(dropped_offpool))

    current = get_prompt("comment_product", cfg.prompt_version, schema_version=cfg.schema_version)
    effective_batch = effective_batch_size(provider, args.batch)
    report = {"stamp": stamp, "n": len(items), "candidates": len(rows), "kept_after_rules": len(kept),
              "offpool_dropped": len(dropped_offpool),
              "strata": dict(Counter(f"{_script(r.content)}|{ownership.get(r.code)}" for r, _ in items))}

    # 实验 1：b=5 与 b=1；同时量实际吞吐，不用请求数猜测提速。
    batch_started = time.perf_counter()
    batch_models = {}
    batched, failed_batch = label_batch(
        provider, current, cfg.schema_version, items, args.batch, batch_models,
    )
    batch_seconds = max(time.perf_counter() - batch_started, 1e-9)
    report["current_b5"] = {
        "requested_batch_size": args.batch,
        "effective_batch_size": effective_batch,
        "labeled": len(batched), "failed": failed_batch,
        "elapsed_seconds": round(batch_seconds, 4),
        "items_per_second": round(len(batched) / batch_seconds, 4),
        "attitude_dist": dict(Counter(a.attitude for a in batched.values())),
        "relevance_dist": dict(Counter(a.relevance for a in batched.values())),
        "needs_review_rate": round(sum(a.needs_review for a in batched.values()) / max(len(batched), 1), 4),
        "compliance_hit_rate": round(sum(bool(a.compliance_tags) for a in batched.values()) / max(len(batched), 1), 4),
    }
    disagreements = []
    if not args.skip_single:
        single_started = time.perf_counter()
        b1, f1 = label_batch(provider, current, cfg.schema_version, items, 1)
        single_seconds = max(time.perf_counter() - single_started, 1e-9)
        rel, n_rel = agreement(batched, b1, "relevance")
        att, n_att = agreement(batched, b1, "attitude")
        single_throughput = len(b1) / single_seconds
        batch_throughput = len(batched) / batch_seconds
        multiplier = round(batch_throughput / single_throughput, 4) if single_throughput else None
        report["b1_vs_b5"] = {
            "labeled_b1": len(b1), "failed_b1": f1,
            "single_elapsed_seconds": round(single_seconds, 4),
            "single_items_per_second": round(single_throughput, 4),
            "batch_items_per_second": round(batch_throughput, 4),
            "throughput_multiplier": multiplier,
            "relevance_agreement": rel, "attitude_agreement": att, "n": n_att,
        }
        for k in batched:
            if k in b1 and (batched[k].attitude != b1[k].attitude or batched[k].relevance != b1[k].relevance):
                disagreements.append(("b1_vs_b5", k, batched[k].relevance, batched[k].attitude, b1[k].relevance, b1[k].attitude))

    # 实验 2：v1 vs v2
    if not args.skip_v1:
        v1 = get_prompt("comment_product", "comment-product-v1")
        v1_items = [(r, {k: v for k, v in p.items() if k != "post_context"}) for r, p in items]
        bv1, fv1 = label_batch(provider, v1, "v1", v1_items, args.batch)
        att, n_att = agreement(batched, bv1, "attitude")
        rel, _ = agreement(batched, bv1, "relevance")
        report["v1_vs_current"] = {"labeled_v1": len(bv1), "failed_v1": fv1,
                              "attitude_agreement": att, "relevance_agreement": rel, "n": n_att}
        for k in batched:
            if k in bv1 and batched[k].attitude != bv1[k].attitude:
                disagreements.append(("v1_vs_current", k, batched[k].relevance, batched[k].attitude, bv1[k].relevance, bv1[k].attitude))

    verdict = []
    for key in ("b1_vs_b5", "v1_vs_current"):
        if key in report and report[key]["attitude_agreement"] is not None and report[key]["attitude_agreement"] < 0.9:
            verdict.append(f"{key} 态度一致率 {report[key]['attitude_agreement']:.1%} < 90%：先改 Prompt 再放量")
    comparison = report.get("b1_vs_b5", {})
    gate = batch_release_gate(
        sample_count=len(items), failed_single=comparison.get("failed_b1", 1),
        failed_batch=failed_batch, relevance_agreement=comparison.get("relevance_agreement"),
        attitude_agreement=comparison.get("attitude_agreement"),
        throughput_multiplier=comparison.get("throughput_multiplier"),
        effective_batch_size=effective_batch,
    )
    passed = gate["passed"] and comparison.get("n") == len(items) and not control.stop_event.is_set()
    report["batchGatePassed"] = passed
    report["batchGate"] = gate
    report["policy"] = calibration_policy(
        cfg,
        requested_batch_size=args.batch,
        effective_batch_size=effective_batch,
        max_input_tokens=args.max_input_tokens,
        max_payload_bytes=args.max_payload_bytes,
        max_output_tokens=args.max_output_tokens,
    )
    # Read the activated values rather than only trusting constants in this
    # process; this makes the artifact unusable after a catalog/rule rollover.
    report["policy"].update(report_policy_fields(engine))
    report["http"] = control.snapshot()
    report["verdict"] = verdict + (["批量一致性门槛通过，不代表准确率"] if passed else
                                   ["未通过放量门槛：需至少300条完整对照，相关性/态度一致率均达90%，且 b=5 吞吐至少为 b=1 的3倍"])

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"calibration-{stamp}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    # 含原文的两份清单写在仓库树外。
    data_dir = default_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    from scripts import gold_sample

    gold_units = []
    for row, _payload in items:
        item_id = f"comment:{row.comment_id}|product:{row.code}"
        label = batched.get(item_id)
        if label is None:
            continue
        parents = [value for value in (
            getattr(row, "parent_content", None),
            getattr(row, "grandparent_content", None),
            getattr(row, "great_grandparent_content", None),
        ) if value]
        gold_units.append({
            "id": f"G{len(gold_units) + 1:04d}",
            "sample_source": "llm",
            "comment_id": row.comment_id,
            "code": row.code,
            "ownership": ownership.get(row.code, "peer"),
            "text": row.content,
            "title": row.title,
            "parent": gold_sample.format_parent_comments(parents),
            "parents": parents,
            "student_relevance": None,
            "student_attitude": None,
            "student_relevance_p": None,
            "student_attitude_p": None,
            "llm_relevance": label.relevance,
            "llm_attitude": label.attitude if label.relevance == "relevant" else None,
            "llm_policy": {
                "model": batch_models.get(item_id) or cfg.model,
                "requestedModel": cfg.model,
                "promptVersion": cfg.prompt_version,
                "schemaVersion": cfg.schema_version,
                "taxonomyVersion": cfg.taxonomy_version,
                **report_policy_fields(engine),
            },
            "language": _script(row.content),
            "llm_only": True,
            "regression_case_id": getattr(row, "regression_case_id", None),
            "stratum": f"{_script(row.content)}|{ownership.get(row.code, 'peer')}",
        })
    gold_dir = data_dir / f"calibration-{stamp}-gold"
    gold_path, labels_path = gold_sample.write_workbooks(
        gold_units, gold_dir, source="llm", llm_only=True,
    )
    report["goldSample"] = {
        "rows": len(gold_units),
        "directory": gold_dir.name,
        "gold": gold_path.name,
        "modelLabels": labels_path.name,
    }
    # Rewrite after adding the non-sensitive workbook manifest. The spreadsheets
    # themselves remain in the external data directory because they contain text.
    (OUT_DIR / f"calibration-{stamp}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8",
    )
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
    print(f"\n金标工作簿、不一致样本与仅个股抽查 CSV 在：{data_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
