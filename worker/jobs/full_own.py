import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, time
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import func, insert, select, update

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT, ROOT / "worker", ROOT / "backend"):
    sys.path.insert(0, str(folder))

from ai import config
from core.calendar import PRESETS, build
from jobs import extract, pipeline
from radar_db import make_engine
from radar_db.leases import WorkerLease
from radar_db.schema import analysis_scopes, annotation_jobs, meta_kv
from radar_db.scope_jobs import scope_condition


def save_progress(engine, progress):
    value = json.dumps(progress, ensure_ascii=False, default=str)
    with engine.begin() as conn:
        if not conn.execute(update(meta_kv).where(meta_kv.c.k == "own_analysis_progress").values(v=value)).rowcount:
            conn.execute(insert(meta_kv).values(k="own_analysis_progress", v=value))


def prepare(engine, cfg, anchor, codes):
    with engine.connect() as conn:
        source_version = dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v).where(
            meta_kv.c.k.in_(("data_revision", "etl_generation")),
        )).all())
    windows = [build(key, anchor) for key in PRESETS]
    start = min(date.fromisoformat(window["benchFrom"]) for window in windows)
    end = datetime.combine(anchor, time.min)
    first = datetime.combine(start, time.min)
    scopes = {}
    for code in codes:
        with engine.connect() as conn:
            existing = conn.execute(select(analysis_scopes).where(
                analysis_scopes.c.codes_json == json.dumps([code]),
                analysis_scopes.c.date_from == first, analysis_scopes.c.date_to == end,
                analysis_scopes.c.task == "both", analysis_scopes.c.prompt_version == cfg.prompt_version,
                analysis_scopes.c.schema_version == cfg.schema_version,
                analysis_scopes.c.taxonomy_version == cfg.taxonomy_version,
            ).order_by(analysis_scopes.c.created_at.desc())).mappings().first()
        old_stats = json.loads(existing["stats_json"] or "{}") if existing else {}
        if (existing and existing["stats_json"] and old_stats.get("sourceVersion", {}) == source_version
            and old_stats.get("model", cfg.model) == cfg.model):
            scopes[code] = existing["scope_id"]
            continue
        result = extract.run(engine, cfg, codes=[code], date_from=first, date_to=end,
                             task="both", drop_offpool=False)
        result.update(sourceVersion=source_version, model=cfg.model)
        with engine.begin() as conn:
            conn.execute(update(analysis_scopes).where(analysis_scopes.c.scope_id == result["scope_id"])
                         .values(stats_json=json.dumps(result, ensure_ascii=False)))
        scopes[code] = result["scope_id"]
    return scopes, start


def queue_status(engine, scope_id):
    with engine.connect() as conn:
        return dict(conn.execute(select(annotation_jobs.c.status, func.count()).where(
            scope_condition(scope_id),
        ).group_by(annotation_jobs.c.status)).all())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--anchor")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--max-items", type=int, default=300)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = config.load()
    engine = make_engine()
    with engine.connect() as conn:
        measured = date.fromisoformat(conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "anchor")).scalar_one())
    anchor = date.fromisoformat(args.anchor) if args.anchor else measured
    if anchor != measured:
        raise SystemExit("Anchor must match the complete source day; no synthetic month end")
    master = json.loads((ROOT / "backend/fixtures/demo/master.json").read_text(encoding="utf-8"))
    codes = [row["code"] for row in master["products"] if row["ownership"] == "own"]
    if len(codes) != 61:
        raise SystemExit("Own product master must contain 61 products")
    with WorkerLease(engine, "own-analysis") as lease:
        scopes, start = prepare(engine, cfg, anchor, codes)
        with engine.connect() as conn:
            source_version = dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v).where(
                meta_kv.c.k.in_(("data_revision", "etl_generation")),
            )).all())
            saved = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "own_analysis_progress")).scalar()
        prior = json.loads(saved) if saved else {}
        reusable = (prior.get("anchor") == anchor.isoformat() and prior.get("model") == cfg.model
                    and prior.get("sourceVersion", {}) == source_version)
        progress = {"anchor": anchor.isoformat(), "baselineFrom": start.isoformat(), "products": {},
                    "status": "running", "model": cfg.model, "batchSize": cfg.micro_batch_size,
                    "sourceVersion": source_version}
        for code, scope_id in scopes.items():
            previous = prior.get("products", {}).get(code, {})
            status = queue_status(engine, scope_id)
            complete = (reusable and previous.get("scope") == scope_id and previous.get("complete", False)
                        and all(state == "done" for state in status))
            progress["products"][code] = {"scope": scope_id, "queue": status, "complete": complete}
        save_progress(engine, progress)
        order = sorted(codes, key=lambda code: sum(count for state, count in progress["products"][code]["queue"].items() if state != "done"))

        def tick():
            nonlocal scopes, start, anchor, source_version, order
            if lease.lost:
                progress["status"] = "lease_lost"
                save_progress(engine, progress)
                if args.watch:
                    scheduler.pause()
                return
            with engine.connect() as conn:
                current_anchor = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "anchor")).scalar_one()
                current_source = dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v).where(
                    meta_kv.c.k.in_(("data_revision", "etl_generation")),
                )).all())
            if current_source != source_version or (not args.anchor and current_anchor != anchor.isoformat()):
                if not args.anchor:
                    anchor = date.fromisoformat(current_anchor)
                scopes, start = prepare(engine, cfg, anchor, codes)
                source_version = current_source
                progress.update(status="running", anchor=anchor.isoformat(), baselineFrom=start.isoformat())
                progress["sourceVersion"] = source_version
                for product_code, scope_id in scopes.items():
                    progress["products"][product_code] = {"scope": scope_id, "queue": queue_status(engine, scope_id), "complete": False}
                save_progress(engine, progress)
            if current_anchor != anchor.isoformat():
                progress["status"] = "source_changed"
                save_progress(engine, progress)
                return
            pending = [code for code in order if not progress["products"][code]["complete"]]
            if not pending:
                progress["status"] = "complete"
                save_progress(engine, progress)
                return
            code = pending[0]
            order.remove(code)
            order.append(code)
            status = queue_status(engine, scopes[code])
            if status.get("dead") or status.get("failed"):
                progress["products"][code]["blocked"] = "failed_jobs"
                save_progress(engine, progress)
                return
            result = pipeline.run(engine, cfg, scopes[code], max_items=args.max_items, ranges=list(PRESETS))
            progress["products"][code].update(queue=queue_status(engine, scopes[code]),
                                              complete=result.get("complete", False))
            progress["updatedAt"] = datetime.utcnow().isoformat() + "Z"
            if result.get("aborted"):
                progress["status"] = "configuration_error"
                save_progress(engine, progress)
                if args.watch:
                    scheduler.pause()
                return
            save_progress(engine, progress)
            print(json.dumps({"code": code, **progress["products"][code]}, ensure_ascii=True), flush=True)

        if args.watch:
            scheduler = BlockingScheduler(timezone="Asia/Hong_Kong")
            logging.getLogger("apscheduler").setLevel(logging.ERROR)
            if os.getenv("FMP_API_KEY"):
                from jobs.sync_prices import sync
                from market_data.fmp import FmpClient
                scheduler.add_job(lambda: sync(engine, FmpClient(), codes, start, anchor, force=True),
                                  "interval", hours=1, max_instances=1, coalesce=True)
            scheduler.add_job(tick, "interval", seconds=5, max_instances=1, coalesce=True,
                              next_run_time=datetime.now())
            try:
                scheduler.start()
            except (KeyboardInterrupt, SystemExit):
                scheduler.shutdown(wait=True)
        else:
            for _ in codes:
                tick()
            return 0 if all(row["complete"] for row in progress["products"].values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())