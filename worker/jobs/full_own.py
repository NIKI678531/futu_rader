import argparse
import json
import logging
import os
import sys
import threading
from contextlib import nullcontext
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import func, insert, select, update

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT, ROOT / "worker", ROOT / "backend"):
    sys.path.insert(0, str(folder))

from ai import config
from core.calendar import PRESETS, build
from jobs import annotate, classify, extract, pipeline
from radar_db import make_engine
from radar_db.events import emit
from radar_db.leases import WorkerLease
from radar_db.revisions import ai_source_version
from radar_db.schema import analysis_scopes, annotation_jobs, comments, feeds, meta_kv
from radar_db.scope_jobs import scope_condition
from radar_db.time_windows import utc_naive_to_hkt

log = logging.getLogger("worker.full_own")


def save_progress(engine, progress):
    value = json.dumps(progress, ensure_ascii=False, default=str)
    with engine.begin() as conn:
        if not conn.execute(update(meta_kv).where(meta_kv.c.k == "own_analysis_progress").values(v=value)).rowcount:
            conn.execute(insert(meta_kv).values(k="own_analysis_progress", v=value))


def prepare(engine, cfg, anchor, codes, *, ranges=None, optimized=False, page_size=1000):
    with engine.connect() as conn:
        source_version = ai_source_version(dict(conn.execute(
            select(meta_kv.c.k, meta_kv.c.v)
        ).all()))
    windows = [build(key, anchor) for key in (ranges or PRESETS)]
    start = min(date.fromisoformat(window["benchFrom"]) for window in windows)
    end = datetime.combine(anchor, time.min)
    first = datetime.combine(start, time.min)
    scopes = {}
    missing = []
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
        missing.append(code)
    snapshot = (extract.candidate_snapshot(engine, missing, first, end + timedelta(days=1), page_size)
                if optimized and missing else nullcontext(None))
    with snapshot as reader:
        for code in missing:
            result = extract.run(engine, cfg, codes=[code], date_from=first, date_to=end,
                                 task="both", drop_offpool=False, candidate_reader=reader,
                                 report_dir=None if optimized else extract.REPORT_DIR)
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


class StudentChannel(threading.Thread):
    """Continuously drain the CPU student stage while the LLM channel runs."""

    def __init__(self, engine, cfg, state, *, idle_seconds=5, model_dir=None):
        super().__init__(name="student-channel", daemon=True)
        self.engine, self.cfg, self.state = engine, cfg, state
        self.idle_seconds, self.model_dir = idle_seconds, model_dir
        self.stop_event = threading.Event()
        self.rounds = 0

    def run(self):
        while not self.stop_event.is_set():
            processed = 0
            for code, scope_id in list(self.state.get("scopes", {}).items()):
                if self.stop_event.is_set():
                    break
                try:
                    stats = classify.run(
                        self.engine,
                        self.cfg,
                        scope_id=scope_id,
                        model_dir=self.model_dir,
                    )
                    processed += stats["input"]
                except Exception as exc:  # noqa: BLE001
                    log.exception("学生通道 %s 失败", code)
                    emit(
                        self.engine,
                        "orchestrator",
                        f"{code} 学生通道失败：{str(exc)[:160]}",
                        level="error",
                        code=code,
                        scope_id=scope_id,
                    )
            self.rounds += 1
            if processed == 0:
                self.stop_event.wait(self.idle_seconds)

    def stop(self):
        self.stop_event.set()


def prioritize_scope(engine, scope_id, anchor, *, own, daily=False):
    changes = defaultdict(list)
    for target_type, source in (
        ("comment", annotation_jobs.join(comments, comments.c.comment_id == annotation_jobs.c.target_id)
         .join(feeds, feeds.c.feed_id == comments.c.feed_id)),
        ("feed", annotation_jobs.join(feeds, feeds.c.feed_id == annotation_jobs.c.target_id)),
    ):
        with engine.connect() as conn:
            rows = conn.execute(select(annotation_jobs.c.job_id, annotation_jobs.c.priority, feeds.c.posted_at)
                                .select_from(source).where(scope_condition(scope_id),
                                    annotation_jobs.c.target_type == target_type,
                                    annotation_jobs.c.status.in_(("pending", "claimed"))))
            for job_id, priority, posted_at in rows:
                updated = annotate.job_priority(posted_at, anchor, own=own, current=True)
                if daily and posted_at is not None:
                    posted_day = utc_naive_to_hkt(posted_at).date()
                    updated = 100000 - (anchor - posted_day).days * 100 + (2 if own else 0)
                if priority != updated:
                    changes[updated].append(job_id)
    with engine.begin() as conn:
        for priority, ids in changes.items():
            for offset in range(0, len(ids), 500):
                conn.execute(update(annotation_jobs).where(annotation_jobs.c.job_id.in_(ids[offset:offset + 500]))
                             .values(priority=priority))
    return sum(len(ids) for ids in changes.values())


def source_state(engine):
    with engine.connect() as conn:
        values = dict(conn.execute(select(meta_kv.c.k, meta_kv.c.v)).all())
    return {
        **({"anchor": values["anchor"]} if values.get("anchor") else {}),
        **ai_source_version(values),
    }


def run_manual(engine, cfg, plan, provider, *, scopes=None, page_size=1000):
    from ai.providers.base import RunStopped

    control = provider.control
    state = source_state(engine)
    if state != plan["sourceState"]:
        raise ValueError("Source changed after planning; create a new plan")
    anchor = date.fromisoformat(plan["anchor"])
    codes = plan["codes"]
    with WorkerLease(engine, "own-analysis") as lease:
        def check_source():
            if lease.lost:
                control.stop("lease_lost")
            elif source_state(engine) != state:
                control.stop("source_changed")
        control.before_request = check_source
        progress = {"anchor": plan["anchor"], "baselineFrom": plan["from"],
                    "scope": "all" if len(codes) == 120 else "selection", "ranges": plan["ranges"],
                    "sourceVersion": {key: value for key, value in state.items() if key != "anchor"},
                    "model": cfg.model, "batchSize": cfg.micro_batch_size, "status": "running", "products": {}}
        try:
            if scopes is None:
                scopes, _start = prepare(engine, cfg, anchor, codes, ranges=plan["ranges"],
                                          optimized=True, page_size=page_size)
            for code, scope in scopes.items():
                prioritize_scope(engine, scope, anchor, own=plan["ownership"][code] == "own", daily=True)
                progress["products"][code] = {"scope": scope, "queue": queue_status(engine, scope), "complete": False}
            order, blocked = list(codes), set()
            def priority_for(code):
                with engine.connect() as conn:
                    return conn.execute(select(func.max(annotation_jobs.c.priority)).where(
                        scope_condition(scopes[code]), annotation_jobs.c.status == "pending",
                    )).scalar() or 0
            priorities = {code: priority_for(code) for code in codes}
            while True:
                check_source()
                control.check()
                available = [code for code in order if not progress["products"][code]["complete"] and code not in blocked]
                if not available:
                    progress["status"] = "blocked" if blocked else "complete"
                    break
                code = max(available, key=lambda candidate: priorities[candidate])
                order.remove(code)
                order.append(code)
                before = queue_status(engine, scopes[code])
                classify.run(engine, cfg, scope_id=scopes[code])
                result = pipeline.run(engine, cfg, scopes[code], provider=provider,
                                      max_items=cfg.micro_batch_size * cfg.concurrency,
                                      ranges=plan["ranges"], anchor_override=anchor, audit_report=False,
                                      synth_workers=cfg.concurrency, stage=annotate.STAGE_LLM)
                after = queue_status(engine, scopes[code])
                priorities[code] = priority_for(code)
                row = progress["products"][code]
                row.update(queue=after, complete=result.get("complete", False))
                if result.get("aborted"):
                    control.stop(control.reason or "configuration_error")
                elif not row["complete"] and before == after:
                    row["blocked"] = "failed_or_leased_jobs_or_synthesis"
                    blocked.add(code)
                progress["batchRun"] = control.snapshot()
                progress["updatedAt"] = datetime.utcnow().isoformat() + "Z"
                save_progress(engine, progress)
                print(json.dumps({"code": code, **row, "httpAttempts": control.attempts}), flush=True)
        except RunStopped:
            progress["status"] = control.reason or "cancelled"
        except BaseException:
            progress["status"] = "error"
            raise
        finally:
            for row in progress["products"].values():
                row["queue"] = queue_status(engine, row["scope"])
            progress["batchRun"] = control.snapshot()
            save_progress(engine, progress)
            control.before_request = None
    return progress


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--anchor")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--max-items", type=int, default=300)
    parser.add_argument("--student-model-dir", help="学生权重目录（默认 STUDENT_MODEL_DIR）")
    parser.add_argument("--no-student", action="store_true", help="不开学生推理，评论任务全部放行主模型")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--ranges", default=",".join(PRESETS))
    parser.add_argument("--max-http-requests", type=int, required=True)
    parser.add_argument("--sync-prices", action="store_true")
    args = parser.parse_args()
    if args.max_http_requests < 1:
        parser.error("--max-http-requests must be positive")
    ranges = list(dict.fromkeys(key.strip() for key in args.ranges.split(",") if key.strip()))
    if not ranges or any(key not in PRESETS for key in ranges):
        parser.error("--ranges must contain supported date presets: " + ",".join(PRESETS))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    cfg = config.load()
    from ai.providers import build as build_provider
    from ai.providers.base import RunControl, RunStopped
    control = RunControl(args.max_http_requests)
    provider = build_provider(cfg, control=control)
    engine = make_engine()
    with engine.connect() as conn:
        measured = date.fromisoformat(conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "anchor")).scalar_one())
    anchor = date.fromisoformat(args.anchor) if args.anchor else measured
    if anchor != measured:
        raise SystemExit("Anchor must match the complete source day; no synthetic month end")
    master = json.loads((ROOT / "backend/fixtures/demo/master.json").read_text(encoding="utf-8"))
    codes = [row["code"] for row in master["products"] if args.all or row["ownership"] == "own"]
    ownership = {row["code"]: row["ownership"] for row in master["products"]}
    if len(codes) != (120 if args.all else 61):
        raise SystemExit("Product master must contain 61 own and 59 peer products")
    with WorkerLease(engine, "own-analysis") as lease, control.interruptible():
        emit(engine, "orchestrator", f"Starting LLM analysis for {len(codes)} products: {','.join(ranges)}")
        scopes, start = prepare(engine, cfg, anchor, codes, ranges=ranges)
        student_state = {"scopes": scopes}
        for code, scope_id in scopes.items():
            prioritize_scope(engine, scope_id, anchor, own=ownership[code] == "own")
        with engine.connect() as conn:
            source_version = ai_source_version(dict(conn.execute(
                select(meta_kv.c.k, meta_kv.c.v)
            ).all()))
            saved = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "own_analysis_progress")).scalar()
        prior = json.loads(saved) if saved else {}
        reusable = (prior.get("anchor") == anchor.isoformat() and prior.get("model") == cfg.model
                    and prior.get("sourceVersion", {}) == source_version)
        progress = {"anchor": anchor.isoformat(), "baselineFrom": start.isoformat(), "products": {},
                    "status": "running", "model": cfg.model, "batchSize": cfg.micro_batch_size,
                    "sourceVersion": source_version, "scope": "all" if args.all else "own", "ranges": ranges,
                    "student": not args.no_student}
        for code, scope_id in scopes.items():
            previous = prior.get("products", {}).get(code, {})
            status = queue_status(engine, scope_id)
            complete = (reusable and previous.get("scope") == scope_id and previous.get("complete", False)
                        and all(state == "done" for state in status))
            progress["products"][code] = {"scope": scope_id, "queue": status, "complete": complete}
        save_progress(engine, progress)
        order = sorted(codes, key=lambda code: sum(count for state, count in progress["products"][code]["queue"].items() if state != "done"))

        student = None
        if args.watch and not args.no_student:
            student = StudentChannel(engine, cfg, student_state, model_dir=args.student_model_dir)
            student.start()
            emit(engine, "orchestrator", "学生通道线程已启动（与主模型通道并行）")

        def run_student_inline(scope_id):
            if args.no_student:
                routed = classify.route_all_to_llm(engine, scope_id, None, "--no-student")
                return {"input": routed, "routed": routed}
            return classify.run(engine, cfg, scope_id=scope_id, model_dir=args.student_model_dir)

        def tick():
            nonlocal scopes, start, anchor, source_version, order
            if control.stop_event.is_set():
                progress["status"] = control.reason
                progress["batchRun"] = control.snapshot()
                save_progress(engine, progress)
                if args.watch:
                    scheduler.shutdown(wait=False)
                return
            if lease.lost:
                progress["status"] = "lease_lost"
                save_progress(engine, progress)
                if args.watch:
                    scheduler.pause()
                return
            with engine.connect() as conn:
                current_anchor = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "anchor")).scalar_one()
                current_source = ai_source_version(dict(conn.execute(
                    select(meta_kv.c.k, meta_kv.c.v)
                ).all()))
            if current_source != source_version or (not args.anchor and current_anchor != anchor.isoformat()):
                if not args.anchor:
                    anchor = date.fromisoformat(current_anchor)
                scopes, start = prepare(engine, cfg, anchor, codes, ranges=ranges)
                student_state["scopes"] = scopes
                source_version = current_source
                progress.update(status="running", anchor=anchor.isoformat(), baselineFrom=start.isoformat())
                progress["sourceVersion"] = source_version
                for product_code, scope_id in scopes.items():
                    prioritize_scope(engine, scope_id, anchor, own=ownership[product_code] == "own")
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
            if student is None:
                student_stats = run_student_inline(scopes[code])
                if student_stats["input"]:
                    emit(
                        engine,
                        "orchestrator",
                        f"{code} 学生段：{student_stats['input']:,} 条 → 路由主模型 {student_stats['routed']:,}",
                        code=code,
                        scope_id=scopes[code],
                        data={"input": student_stats["input"], "routed": student_stats["routed"]},
                    )
            emit(engine, "L2", f"Processing {code}", code=code, scope_id=scopes[code], data=status)
            try:
                result = pipeline.run(
                    engine,
                    cfg,
                    scopes[code],
                    max_items=args.max_items,
                    ranges=ranges,
                    provider=provider,
                    synth_workers=cfg.concurrency,
                    stage=annotate.STAGE_LLM,
                )
            except RunStopped:
                result = {"complete": False, "aborted": control.reason}
            progress["products"][code].update(queue=queue_status(engine, scopes[code]),
                                              complete=result.get("complete", False))
            emit(engine, "L3", f"{code}: {result.get('synth_pairs', 0)} ready ranges",
                 code=code, scope_id=scopes[code], data=progress["products"][code])
            progress["updatedAt"] = datetime.utcnow().isoformat() + "Z"
            if result.get("aborted"):
                progress["status"] = control.reason or "configuration_error"
                progress["batchRun"] = control.snapshot()
                save_progress(engine, progress)
                if args.watch:
                    scheduler.shutdown(wait=False)
                return
            save_progress(engine, progress)
            print(json.dumps({"code": code, **progress["products"][code]}, ensure_ascii=True), flush=True)

        try:
            if args.watch:
                scheduler = BlockingScheduler(timezone="Asia/Hong_Kong")
                logging.getLogger("apscheduler").setLevel(logging.ERROR)
                if args.sync_prices and os.getenv("FMP_API_KEY"):
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
                    if control.stop_event.is_set():
                        break
                return 0 if all(row["complete"] for row in progress["products"].values()) else 2
        finally:
            if student is not None:
                student.stop()
                student.join(timeout=30)


if __name__ == "__main__":
    raise SystemExit(main())
