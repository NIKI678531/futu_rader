"""61 只自家产品的全量分析编排（runbook §23、§24）—— 学生通道与 Luna 通道并行。

    python -m jobs.full_own --watch                 # 常驻：每 5 秒轮一只产品，锚点／源数据变了自动重排
    python -m jobs.full_own                         # 跑一遍 61 只就退出（每只一个 tick）
    python -m jobs.full_own --max-items 300         # Luna 通道每 tick 最多领多少条

## 一个 tick 做什么（ADR-0021）

1. `classify.run(scope)`：学生模型把该产品 `stage=student` 的评论任务判一遍，判不准的改成 `stage=llm`。
2. `pipeline.run(scope)`：Luna 只领 `stage=llm` 的评论任务、KOL 评论、帖子；然后逐区间判就绪，
   就绪的 `(code, range)` 进 Layer B（8 线程）。

## 两个通道怎么并行

`--watch` 下学生通道是**一个独立线程**（`StudentChannel`），按产品轮转跑 `classify.run`；
调度器的 tick 只做第 2 步。选这个而不是「tick 里先 classify 再 pipeline」的原因：学生是 CPU 活
（10–12 分钟判完 61 只），Luna 是网络活（20–25 分钟），串起来就是 35 分钟，超了 30 分钟预算；
并行时 Luna 边等响应边有新的 `stage=llm` 任务被学生放出来。两个通道领的是**不相交**的任务集
（`claim` 按 stage 过滤，且有进程级锁），写库各自短事务，SQLite 的 busy_timeout 兜底。
不带 `--watch` 时没有线程：每个 tick 顺序做 1 → 2，跑一遍就退出，行为可预测。

每一步都记一条 `worker_events`（`stage='orchestrator'`），学生／Luna／Layer B 各自的批事件由它们
自己记（L1／L2／L3）。侧栏读 `/api/v1/progress`。
"""

import argparse
import json
import logging
import os
import sys
import threading
import time as _time
from datetime import date, datetime, time
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import func, insert, select, update

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT, ROOT / "worker", ROOT / "backend"):
    sys.path.insert(0, str(folder))

from ai import config
from core.calendar import PRESETS, build
from jobs import classify, extract, pipeline
from radar_db import make_engine
from radar_db.events import emit
from radar_db.leases import WorkerLease
from radar_db.schema import analysis_scopes, annotation_jobs, meta_kv
from radar_db.scope_jobs import scope_condition

log = logging.getLogger("worker.full_own")


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
        emit(engine, "L0", f"{code} 抽取完成：评论候选 {result['comments']['candidates']:,} → 排队 "
                           f"{result['comments']['kept']:,}（近重复成员 {result['comments']['near_duplicate_members']:,}）",
             code=code, scope_id=result["scope_id"],
             data={"candidates": result["comments"]["candidates"], "kept": result["comments"]["kept"],
                   "near_duplicate_members": result["comments"]["near_duplicate_members"]})
    return scopes, start


def queue_status(engine, scope_id):
    with engine.connect() as conn:
        return dict(conn.execute(select(annotation_jobs.c.status, func.count()).where(
            scope_condition(scope_id),
        ).group_by(annotation_jobs.c.status)).all())


class StudentChannel(threading.Thread):
    """`--watch` 下的学生线程：按产品轮转跑 `classify.run`，队列空时歇 `idle_seconds`。

    读 `state["scopes"]` 而不是持有一份拷贝：tick 发现源数据变了会重排 scope，线程下一轮自然
    跟上。任何异常只记日志并继续 —— 学生线程停了 Luna 也照样能把剩下的判完，只是慢。
    """

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
                    st = classify.run(self.engine, self.cfg, scope_id=scope_id, model_dir=self.model_dir)
                    processed += st["input"]
                except Exception as exc:  # noqa: BLE001
                    log.exception("学生通道 %s 失败", code)
                    emit(self.engine, "orchestrator", f"{code} 学生通道失败：{str(exc)[:160]}", level="error",
                         code=code, scope_id=scope_id)
            self.rounds += 1
            if processed == 0:
                self.stop_event.wait(self.idle_seconds)

    def stop(self):
        self.stop_event.set()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--anchor")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--max-items", type=int, default=300)
    parser.add_argument("--student-model-dir", help="学生权重目录（默认 STUDENT_MODEL_DIR）")
    parser.add_argument("--no-student", action="store_true", help="不开学生通道（评论任务全部放行 Luna）")
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
        emit(engine, "orchestrator", f"full_own 启动：{len(codes)} 只产品，锚点 {anchor.isoformat()}，"
                                     f"{'常驻' if args.watch else '单轮'}，Luna 每 tick {args.max_items} 条",
             data={"codes": len(codes), "anchor": anchor.isoformat(), "watch": args.watch})
        scopes, start = prepare(engine, cfg, anchor, codes)
        state = {"scopes": scopes}
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
                    "sourceVersion": source_version, "student": not args.no_student}
        for code, scope_id in scopes.items():
            previous = prior.get("products", {}).get(code, {})
            status = queue_status(engine, scope_id)
            complete = (reusable and previous.get("scope") == scope_id and previous.get("complete", False)
                        and all(state_ == "done" for state_ in status))
            progress["products"][code] = {"scope": scope_id, "queue": status, "complete": complete}
        save_progress(engine, progress)
        order = sorted(codes, key=lambda code: sum(count for state_, count in progress["products"][code]["queue"].items() if state_ != "done"))

        student = None
        if args.watch and not args.no_student:
            student = StudentChannel(engine, cfg, state, model_dir=args.student_model_dir)
            student.start()
            emit(engine, "orchestrator", "学生通道线程已启动（与 Luna 通道并行）")

        def run_student_inline(code, scope_id):
            """不带 --watch 时学生段在 tick 里同步跑：没有线程，顺序可预测。`--no-student` 直接放行。"""
            if args.no_student:
                n = classify.route_all_to_llm(engine, scope_id, None, "--no-student")
                return {"input": n, "routed": n}
            return classify.run(engine, cfg, scope_id=scope_id, model_dir=args.student_model_dir)

        def tick():
            nonlocal scopes, start, anchor, source_version, order
            if lease.lost:
                progress["status"] = "lease_lost"
                save_progress(engine, progress)
                emit(engine, "orchestrator", "租约丢失，停止", level="error")
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
                emit(engine, "orchestrator", f"源数据或锚点变化，重排 scope（锚点 {anchor.isoformat()}）", level="warn")
                scopes, start = prepare(engine, cfg, anchor, codes)
                state["scopes"] = scopes
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
                if progress["status"] != "complete":
                    emit(engine, "orchestrator", f"{len(codes)} 只产品全部完成")
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
                emit(engine, "orchestrator", f"{code} 有 dead/failed 任务，跳过", level="warn", code=code,
                     scope_id=scopes[code], data=status)
                return
            if student is None:
                st = run_student_inline(code, scopes[code])
                if st["input"]:
                    emit(engine, "orchestrator", f"{code} 学生段：{st['input']:,} 条 → 路由 Luna {st['routed']:,}",
                         code=code, scope_id=scopes[code], data={"input": st["input"], "routed": st["routed"]})
            t0 = _time.monotonic()
            result = pipeline.run(engine, cfg, scopes[code], max_items=args.max_items, ranges=list(PRESETS))
            progress["products"][code].update(queue=queue_status(engine, scopes[code]),
                                              complete=result.get("complete", False))
            progress["updatedAt"] = datetime.utcnow().isoformat() + "Z"
            steps = {s["task"]: s for s in result["steps"]}
            luna = steps.get("comment_product", {})
            emit(engine, "orchestrator",
                 f"{code} tick：Luna 评论写入 {luna.get('success', 0):,}，就绪区间 {result.get('synth_pairs', 0)}/"
                 f"{result.get('synth_pairs_total', len(PRESETS))}，{'完成' if result.get('complete') else '未完'}"
                 f"（{_time.monotonic() - t0:.0f}s）",
                 code=code, scope_id=scopes[code],
                 data={"queue": progress["products"][code]["queue"], "complete": result.get("complete", False),
                       "synth_pairs": result.get("synth_pairs")})
            if result.get("aborted"):
                progress["status"] = "configuration_error"
                save_progress(engine, progress)
                emit(engine, "orchestrator", f"配置错误，停止：{result['aborted'][:160]}", level="error")
                if args.watch:
                    scheduler.pause()
                return
            save_progress(engine, progress)
            print(json.dumps({"code": code, **progress["products"][code]}, ensure_ascii=True), flush=True)

        try:
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
        finally:
            if student is not None:
                student.stop()
                student.join(timeout=30)


if __name__ == "__main__":
    raise SystemExit(main())
