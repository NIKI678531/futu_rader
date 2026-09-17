import argparse
import json
import logging
import signal
import sys
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT, ROOT / "worker", ROOT / "backend"):
    sys.path.insert(0, str(folder))

from sqlalchemy import func, select

from ai import config
from ai.batching import BatchPolicy, pack_items, measure
from ai.providers import build as build_provider
from ai.providers.base import RunControl
from core.calendar import PRESETS, build
from jobs import annotate, extract, full_own
from radar_db import make_engine
from radar_db.schema import analysis_scopes, annotation_jobs, comments, feeds, meta_kv


def parser():
    cli = argparse.ArgumentParser(description="Manual SQL extraction and bounded AI analysis")
    cli.add_argument("command", choices=("plan", "run", "status", "resume"))
    cli.add_argument("--ranges", default="d1,d2")
    cli.add_argument("--ownership", choices=("all", "own", "peer"), default="all")
    cli.add_argument("--sector")
    cli.add_argument("--struct")
    cli.add_argument("--codes")
    cli.add_argument("--anchor")
    cli.add_argument("--anchor-mode", choices=("latest-complete", "calendar-yesterday"), default="latest-complete")
    cli.add_argument("--scope")
    cli.add_argument("--batch-size", type=int, default=5)
    cli.add_argument("--concurrency", type=int, default=2)
    cli.add_argument("--max-input-tokens", type=int, default=8000)
    cli.add_argument("--max-payload-bytes", type=int, default=12288)
    cli.add_argument("--max-output-tokens", type=int, default=8192)
    cli.add_argument("--page-size", type=int, default=1000)
    cli.add_argument("--max-http-requests", type=int)
    cli.add_argument("--calibration-report", type=Path)
    cli.add_argument("--watch", action="store_true")
    return cli


def selected_products(args):
    master = json.loads(extract.MASTER.read_text(encoding="utf-8"))["products"]
    selected = master
    for argument, field in (("codes", "code"), ("sector", "sector"), ("struct", "struct")):
        raw = getattr(args, argument)
        if raw is None:
            continue
        values = {value.strip() for value in raw.split(",") if value.strip()}
        unknown = values - {row[field] for row in master}
        if unknown or not values:
            raise ValueError(f"Invalid {argument}: {sorted(unknown)}")
        selected = [row for row in selected if row[field] in values]
    if args.ownership != "all":
        selected = [row for row in selected if row["ownership"] == args.ownership]
    if not selected:
        raise ValueError("Product filters have an empty intersection")
    return selected


def make_plan(engine, args):
    products = selected_products(args)
    with engine.connect() as conn:
        meta = dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v)).all())
    if not meta.get("anchor"):
        raise ValueError("No complete source day; import source data first")
    complete = date.fromisoformat(meta["anchor"][:10])
    anchor = date.fromisoformat(args.anchor) if args.anchor else complete
    if args.anchor_mode == "calendar-yesterday":
        if args.anchor:
            raise ValueError("Use either --anchor or --anchor-mode")
        anchor = datetime.now(ZoneInfo("Asia/Hong_Kong")).date() - timedelta(days=1)
    if anchor > complete:
        raise ValueError(f"Requested {anchor}, but source is complete only through {complete}")
    ranges = list(dict.fromkeys(value.strip() for value in args.ranges.split(",") if value.strip()))
    if not ranges or set(ranges) - set(PRESETS):
        raise ValueError("Unsupported ranges")
    windows = sorted((build(key, anchor) for key in ranges), key=lambda window: window["days"])
    first = min(window["benchFrom"] for window in windows)
    if meta.get("window_from") and first < meta["window_from"][:10]:
        raise ValueError("Requested baseline predates imported source coverage")
    if anchor != complete and not meta.get("window_from"):
        raise ValueError("Historical replay requires a declared window_from")
    days = []
    for window in windows:
        for start, end in ((window["from"], window["to"]), (window["benchFrom"], window["benchTo"])):
            day, lower = date.fromisoformat(end), date.fromisoformat(start)
            while day >= lower:
                if day.isoformat() not in days:
                    days.append(day.isoformat())
                day -= timedelta(days=1)
    codes = [row["code"] for row in products]
    with engine.connect() as conn:
        counts = conn.execute(select(feeds.c.code, func.count(comments.c.comment_id)).select_from(
            comments.join(feeds, comments.c.feed_id == feeds.c.feed_id)).where(
                feeds.c.code.in_(codes), feeds.c.posted_at >= datetime.combine(date.fromisoformat(first), time.min),
                feeds.c.posted_at < datetime.combine(anchor + timedelta(days=1), time.min),
                comments.c.content.isnot(None), comments.c.content != "",
            ).group_by(feeds.c.code)).all()
    return {"anchor": anchor.isoformat(), "sourceCompleteThrough": complete.isoformat(), "from": first,
            "ranges": [window["key"] for window in windows], "priorityDays": days, "codes": codes,
            "ownership": {row["code"]: row["ownership"] for row in products},
            "sourceState": {key: meta[key] for key in ("anchor", "data_revision", "etl_generation") if key in meta},
            "windows": [{key: window[key] for key in ("key", "from", "to", "benchFrom", "benchTo")}
                        for window in windows], "candidatesByProduct": dict(counts),
            "candidateComments": sum(count for _code, count in counts),
            "estimatedCost": None, "timeBasis": "feed_posted_at", "modelRequestsMade": 0}


def preview(engine, cfg, plan, page_size):
    from ai import neardup, prefilter, schemas
    from ai.lexicon import offpool_stocks, product_aliases

    prompt, version = annotate.resolve("comment_product", cfg)
    policy = BatchPolicy(cfg.micro_batch_size, cfg.max_input_tokens, cfg.max_payload_bytes)
    stats = {"filtered": 0, "nearDuplicateMembers": 0, "alreadyDone": 0, "newOrPending": 0,
             "plannedCommentBatches": 0, "singletonBatches": 0, "oversizedItems": 0, "inputTokensEstimate": 0}
    lexicon = product_aliases.ProductLexicon()
    stock = offpool_stocks.StockLexicon(offpool_stocks.load_from_db(engine, set(plan["codes"])))
    filter_ = prefilter.Prefilter(lexicon, stock, drop_offpool=False)
    first = datetime.combine(date.fromisoformat(plan["from"]), time.min)
    end = datetime.combine(date.fromisoformat(plan["anchor"]) + timedelta(days=1), time.min)
    with extract.candidate_snapshot(engine, plan["codes"], first, end, page_size) as reader:
        for day in plan["priorityDays"]:
            since = datetime.combine(date.fromisoformat(day), time.min)
            for code in plan["codes"]:
                kept = []
                for row in reader(engine, codes=[code], since=since, until=since + timedelta(days=1)):
                    decision = filter_.classify(row.content, code, comment_id=row.comment_id,
                                               author_uid=row.author_uid, feed_id=row.feed_id)
                    if decision.dropped:
                        stats["filtered"] += 1
                    else:
                        kept.append(row)
                reps, members = neardup.fold(kept, key_of=lambda row: (row.code, row.posted_at.date()),
                                             id_of=lambda row: row.comment_id, text_of=lambda row: row.content)
                stats["nearDuplicateMembers"] += len(members)
                payloads = []
                for offset in range(0, len(reps), 200):
                    chunk = reps[offset:offset + 200]
                    with engine.connect() as conn:
                        existing = set(conn.execute(select(annotation_jobs.c.target_id, annotation_jobs.c.input_hash)
                            .where(annotation_jobs.c.target_type == "comment", annotation_jobs.c.subject_code == code,
                                   annotation_jobs.c.task == "comment_product", annotation_jobs.c.status == "done",
                                   annotation_jobs.c.target_id.in_([row.comment_id for row in chunk]))).all())
                    for row in chunk:
                        job = annotate.job_row_for_comment(cfg, prompt, version, row)
                        if (row.comment_id, job["input_hash"]) in existing:
                            stats["alreadyDone"] += 1
                            continue
                        payloads.append(annotate._build_payload("comment_product", job, {
                            "text": row.content, "title": row.title, "parent": row.parent_content,
                            "post_content": row.post_content}))
                batches, oversized = pack_items(payloads, key_of=lambda row: (code, day), payload_of=lambda row: row,
                    system=prompt.SYSTEM, render=prompt.user_message, schema=schemas.batch_json_schema("comment_product", version),
                    policy=policy)
                stats["newOrPending"] += len(payloads)
                stats["oversizedItems"] += len(oversized)
                stats["plannedCommentBatches"] += len(batches)
                stats["singletonBatches"] += sum(len(batch) == 1 for batch in batches)
                for batch in batches:
                    stats["inputTokensEstimate"] += measure(batch, prompt.SYSTEM, prompt.user_message,
                        schemas.batch_json_schema("comment_product", version))["inputTokensEstimate"]
    return {**plan, "commentPlan": stats, "note": "Comment estimates only; post/KOL, summary and retry calls also consume the run budget"}


def check_calibration(cfg, path):
    if cfg.micro_batch_size <= 1:
        return
    if path is None:
        raise ValueError("Batch mode requires --calibration-report from scripts.calibrate; no paid requests sent")
    report = json.loads(path.read_text(encoding="utf-8"))
    expected = {"model": cfg.model, "promptVersion": cfg.prompt_version, "schemaVersion": cfg.schema_version,
                "taxonomyVersion": cfg.taxonomy_version, "batchSize": cfg.micro_batch_size,
                "maxInputTokens": cfg.max_input_tokens, "maxPayloadBytes": cfg.max_payload_bytes}
    expected["maxOutputTokens"] = cfg.max_output_tokens
    if report.get("policy") != expected or report.get("batchGatePassed") is not True:
        raise ValueError("Calibration did not pass or does not match this model/prompt/batch policy")


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    try:
        if args.command in ("run", "resume") and (args.max_http_requests is None or args.max_http_requests < 1):
            raise ValueError("run/resume requires a positive --max-http-requests")
        if not 1 <= args.concurrency <= 4 or args.page_size < 1 or args.max_output_tokens < 1:
            raise ValueError("concurrency must be 1..4 and page-size must be positive")
        BatchPolicy(args.batch_size, args.max_input_tokens, args.max_payload_bytes)
        if args.watch and args.command != "run":
            raise ValueError("--watch is only supported for run")
        if args.scope and args.command != "resume":
            raise ValueError("--scope is only supported for resume")
        engine = make_engine()
        if args.command == "status":
            with engine.connect() as conn:
                value = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "own_analysis_progress")).scalar()
            print(value or json.dumps({"status": "not_started"}))
            return 0
        cfg = replace(config.load(_allow_missing_key=args.command == "plan"),
                      micro_batch_size=args.batch_size, concurrency=args.concurrency,
                      max_input_tokens=args.max_input_tokens, max_payload_bytes=args.max_payload_bytes,
                      max_output_tokens=args.max_output_tokens, grouped_batches=True)
        plan = make_plan(engine, args)
        if args.command == "plan":
            print(json.dumps(preview(engine, cfg, plan, args.page_size), ensure_ascii=False, indent=2))
            return 0
        check_calibration(cfg, args.calibration_report)
        scopes = None
        if args.command == "resume":
            if not args.scope:
                raise ValueError("resume requires --scope")
            with engine.connect() as conn:
                scope = conn.execute(select(analysis_scopes).where(analysis_scopes.c.scope_id == args.scope)).mappings().first()
            if scope is None:
                raise ValueError("Unknown scope")
            if (scope["date_from"].date().isoformat() != plan["from"]
                    or scope["date_to"].date().isoformat() != plan["anchor"]):
                raise ValueError("Scope dates differ; pass the original --ranges and --anchor")
            scope_stats = json.loads(scope["stats_json"] or "{}")
            if any(scope[key] != getattr(cfg, key) for key in ("prompt_version", "schema_version", "taxonomy_version")):
                raise ValueError("Scope model/prompt versions changed; use plan/run to create new tasks")
            if scope_stats.get("model", cfg.model) != cfg.model or scope_stats.get("sourceVersion", {}) != {
                    key: value for key, value in plan["sourceState"].items() if key != "anchor"}:
                raise ValueError("Scope source/model changed; run a new plan")
            codes = json.loads(scope["codes_json"])
            if len(codes) != 1 or not set(codes) <= set(plan["codes"]):
                raise ValueError("Resume requires a matching single-product scope")
            plan["codes"] = codes
            scopes = {codes[0]: args.scope}
        control = RunControl(args.max_http_requests)
        provider = build_provider(cfg, control=control)
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
        previous = signal.signal(signal.SIGINT, lambda *_args: control.stop("cancelled"))
        try:
            while True:
                print(json.dumps({"plan": plan, "requestLimit": control.limit}, ensure_ascii=False), flush=True)
                result = full_own.run_manual(engine, cfg, plan, provider, scopes=scopes, page_size=args.page_size)
                if not args.watch or result["status"] != "complete":
                    return 0 if result["status"] == "complete" else 130 if result["status"] == "cancelled" else 2
                while not control.stop_event.wait(10):
                    if full_own.source_state(engine) != plan["sourceState"]:
                        plan = make_plan(engine, args)
                        break
                if control.stop_event.is_set():
                    return 130
        finally:
            signal.signal(signal.SIGINT, previous)
    except (ValueError, RuntimeError) as exc:
        cli.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())