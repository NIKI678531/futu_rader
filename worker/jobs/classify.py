"""学生分类作业（ADR-0021 L1）—— 用本地学生模型先判一遍评论 × 产品，判不准的交 Luna。

    python -m jobs.classify --scope <scope_id>            # 只处理这个抽取范围
    python -m jobs.classify --all-pending                  # 队列里全部 stage=student 的评论任务
    python -m jobs.classify --all-pending --limit 4000
    python -m jobs.classify --scope <scope_id> --dry-run   # 只数不写

## 它在漏斗里的位置

`extract` 排进来的评论任务默认 `stage='student'`。本作业按 2,000 条一批领取，走 onnxruntime
推理，**每条都落库**（`annotation_runs.provider='local_model'`），再按四条路由规则决定要不要把
任务改成 `stage='llm'` 放回 pending 交给 Luna：

1. 任一头最大概率 < `STUDENT_ROUTE_THRESHOLD`（默认 0.85）；
2. relevance 判成 `needs_context` —— 学生看到的上下文和 Luna 一样，它都判不出的 Luna 也未必能，
   但 Luna 至少会给证据与理由，人复核有抓手；
3. 态度前两类概率差 < `STUDENT_MARGIN_THRESHOLD`（默认 0.15）—— 「积极 0.46 / 中性 0.40」
   这种不叫判定；
4. 合规词表命中（`ai/lexicon/compliance_zh.py`）—— 合规是七维里学生没有的那几维，词表召回
   到的一律让 Luna 写 `compliance` 行与证据。

路由走的任务学生行仍在库里，Luna 的行写下时会 supersede 它 —— 链上能看到「学生怎么判、
Luna 怎么改」。没路由的任务直接 `done`，并补一行 `compliance={"tags":[],"rationale":null,
"screen":"lexicon"}`（provider 用规则 run）：读路径按「有没有 compliance 行」区分「查过了没有」
与「没查过」（runbook §20.4），学生通道也必须把这行补上，否则页面上这条评论永远是「未查」。

## 为什么没有学生模型时不是报错停下

`STUDENT_MODEL_DIR` 里没训过的权重（第一次部署、或换机器）时，把全部 student 任务改成 `llm`
放行 —— 漏斗退化成 ADR-0020 的「全部 Luna」，慢但对；停下等人训模型会让页面几小时没有新
数据。事件流里记一条 warn，`--require-model` 可以改成报错。

## 近重复簇成员

`extract` 不会给簇成员排任务（它们的标签由代表写入时 `neardup.propagate` 推过去）。但旧队列
或直接 `enqueue_comments` 进来的任务可能是成员：领到时查它有没有现行的 `duplicate_cluster`
行，有就直接 `done`，不推理。
"""

import argparse
import json
import logging
import os
import sys
import threading
from pathlib import Path

from sqlalchemy import insert, select, update

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import clock  # noqa: E402
from ai import config, neardup, prefilter, schemas  # noqa: E402
from ai.lexicon import compliance_zh  # noqa: E402
from jobs import annotate  # noqa: E402
from models import registry  # noqa: E402
from models.dataset import payload_text  # noqa: E402
from radar_db import make_engine  # noqa: E402
from radar_db.events import emit  # noqa: E402
from radar_db.schema import annotation_jobs, annotation_runs, annotations  # noqa: E402

log = logging.getLogger("worker.classify")

TASK = "comment_product"
BATCH_SIZE = 2000
PROMPT_VERSION = registry.STUDENT_VERSION
_HUMAN_SETTLED = ("approved", "corrected")

# 一条 compliance 行的固定形状：学生通道只做了词表筛，`screen` 字段告诉读者这不是 Luna 的七维。
LEXICON_CLEAN = {"tags": [], "rationale": None, "screen": "lexicon"}

_ROUTE_LABEL = {
    "low_confidence": "低置信",
    "needs_context": "需上下文",
    "attitude_margin": "态度边际",
    "compliance_lexicon": "合规词表",
}


def new_run_id():
    import uuid

    return "stu-" + clock.now().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]


# ── 路由判定 ───────────────────────────────────────────────────────────


def route_reasons(pred, text, *, route_thr=None, margin_thr=None):
    """一条学生预测 → 命中的路由规则名列表（空 ⇒ 学生结论生效）。

    `pred` 是 `models.infer.Student.predict` 的一条：`{head: {label, max, margin, probs}}`。
    态度头只在 relevance=relevant 时参与判定：无关评论的态度是无定义的量，拿它的概率卡阈值
    会把大半无关评论也送去 Luna。
    """
    route_thr = registry.route_threshold() if route_thr is None else route_thr
    margin_thr = registry.margin_threshold() if margin_thr is None else margin_thr
    rel = pred["relevance"]
    reasons = []
    if rel["label"] == "needs_context":
        reasons.append("needs_context")
    if rel["max"] < route_thr:
        reasons.append("low_confidence")
    if rel["label"] == "relevant":
        att = pred["attitude"]
        if att["max"] < route_thr and "low_confidence" not in reasons:
            reasons.append("low_confidence")
        if att["margin"] < margin_thr:
            reasons.append("attitude_margin")
    if compliance_zh.hits(text):
        reasons.append("compliance_lexicon")
    return reasons


def needs_review(pred, *, review_thr=None):
    review_thr = registry.review_threshold() if review_thr is None else review_thr
    if pred["relevance"]["max"] < review_thr:
        return True
    return pred["relevance"]["label"] == "relevant" and pred["attitude"]["max"] < review_thr


# ── 落库 ───────────────────────────────────────────────────────────────


def _open_runs(engine, cfg, schema_version, student_model_id, now):
    """一条 `local_model` 运行给学生行，一条 `rule` 运行给词表 compliance 行。"""
    run_id = new_run_id()
    rule_run_id = ("rule-" + run_id)[:40]
    with engine.begin() as conn:
        conn.execute(insert(annotation_runs).values(
            run_id=run_id, task=TASK, provider="local_model", model_id=student_model_id,
            model_revision=None, prompt_version=PROMPT_VERSION,
            taxonomy_version=cfg.taxonomy_version, schema_version=schema_version,
            started_at=now, status="running", input_count=0, success_count=0, error_count=0,
            token_input=None, token_output=None, token_reasoning=None,
        ))
    prefilter.open_rule_run(engine, rule_run_id, TASK, now,
                            taxonomy_version=cfg.taxonomy_version, schema_version=schema_version)
    return run_id, rule_run_id


def _close_runs(engine, run_id, rule_run_id, stats):
    from radar_db.revisions import bump_revision, mark_synthesis, ranges_touching

    with engine.begin() as conn:
        if stats["written"]:
            bump_revision(conn, "annotation")
            annotate._mark_touched_ranges(conn, run_id, mark_synthesis, ranges_touching)
        for rid, ok in ((run_id, stats["written"]), (rule_run_id, stats["compliance_rows"])):
            conn.execute(update(annotation_runs).where(annotation_runs.c.run_id == rid).values(
                finished_at=clock.now(), status="done" if not stats["errors"] else "partial",
                input_count=stats["input"], success_count=ok, error_count=stats["errors"],
            ))


def _insert(conn, job, kind, value, conf, review_state, run_id, input_hash, now):
    """写一行学生结论，supersede 链末的非人工裁决行（与 `annotate._write` 同一条纪律）。返回 annotation_id。"""
    prev = conn.execute(
        select(annotations.c.annotation_id, annotations.c.review_state).where(
            annotations.c.target_type == "comment", annotations.c.target_id == job["target_id"],
            annotations.c.subject_code == job["subject_code"], annotations.c.kind == kind,
        ).order_by(annotations.c.annotation_id.desc()).limit(1)
    ).first()
    supersedes = prev.annotation_id if prev is not None and prev.review_state not in _HUMAN_SETTLED else None
    res = conn.execute(insert(annotations).values(
        target_type="comment", target_id=job["target_id"], subject_code=job["subject_code"], kind=kind,
        value_json=json.dumps(value, ensure_ascii=False), calibrated_confidence=conf, run_id=run_id,
        input_hash=input_hash, review_state=review_state, created_at=now, supersedes_id=supersedes,
    ))
    return res.inserted_primary_key[0]


def _cluster_members(conn, jobs):
    """这批任务里哪些 `(target_id, code)` 已经是近重复簇成员（现行 `duplicate_cluster` 行）。"""
    if not jobs:
        return set()
    ids = sorted({j["target_id"] for j in jobs})
    newer = annotations.alias("newer")
    chain_end = ~select(newer.c.annotation_id).where(newer.c.supersedes_id == annotations.c.annotation_id).exists()
    out = set()
    for i in range(0, len(ids), 900):
        rows = conn.execute(select(annotations.c.target_id, annotations.c.subject_code).where(
            annotations.c.kind == "duplicate_cluster", annotations.c.target_type == "comment",
            annotations.c.target_id.in_(ids[i:i + 900]), annotations.c.review_state != "rejected", chain_end,
        ))
        out.update((r[0], r[1]) for r in rows)
    return out


def _route_all_to_llm(engine, scope_id, limit, reason):
    """没有学生模型时的退化路径：把 student 任务整体放行给 Luna。返回改了多少条。"""
    q = select(annotation_jobs.c.job_id).where(
        annotation_jobs.c.task == TASK, annotation_jobs.c.stage == annotate.STAGE_STUDENT,
        annotation_jobs.c.status.in_(("pending", "claimed")),
    )
    if scope_id is not None:
        from radar_db.scope_jobs import scope_condition
        q = q.where(scope_condition(scope_id))
    if limit:
        q = q.limit(limit)
    now = clock.now()
    with engine.begin() as conn:
        ids = [r[0] for r in conn.execute(q)]
        for i in range(0, len(ids), 500):
            conn.execute(update(annotation_jobs).where(annotation_jobs.c.job_id.in_(ids[i:i + 500])).values(
                stage=annotate.STAGE_LLM, status="pending", lease_until=None, updated_at=now,
                last_error=f"学生模型不可用，放行 Luna：{reason}"[:2000]))
    return len(ids)


# ── 主流程 ─────────────────────────────────────────────────────────────


def _fresh_stats():
    return {"run_id": None, "input": 0, "written": 0, "routed": 0, "done": 0, "members": 0,
            "superseded": 0, "errors": 0, "compliance_rows": 0, "batches": 0,
            "relevance": {"relevant": 0, "irrelevant": 0, "needs_context": 0},
            "routes": {k: 0 for k in _ROUTE_LABEL}, "student_available": True}


def _process_batch(engine, cfg, prompt, schema_version, student, jobs, run_id, rule_run_id, stats, scope_id):
    """一批：取源文本 → 推理 → 一个事务写完全部行与任务状态。"""
    now = clock.now()
    sources = annotate._load_sources(engine, TASK, jobs)
    with engine.connect() as conn:
        members = _cluster_members(conn, jobs)

    usable, texts, payload_hash = [], [], {}
    to_done_direct, to_supersede, to_dead = [], [], []
    for job in jobs:
        key = (job["target_id"], job["subject_code"])
        if key in members:
            to_done_direct.append(job)
            continue
        src = sources.get(("comment", job["target_id"]))
        if not src or not (src.get("text") or "").strip():
            to_dead.append(job)
            continue
        payload = annotate._build_payload(TASK, job, src)
        h = schemas.input_hash(payload, model=cfg.model, prompt_version=annotate.prompt_version(prompt, cfg),
                               taxonomy_version=cfg.taxonomy_version, schema_version=schema_version)
        if h != job["input_hash"]:
            to_supersede.append(job)
            continue
        usable.append((job, src))
        texts.append(payload_text(payload))

    preds = student.predict(texts) if texts else []

    counts = {"relevant": 0, "irrelevant": 0, "needs_context": 0}
    routed_by = {k: 0 for k in _ROUTE_LABEL}
    n_routed = n_done = 0
    with engine.begin() as conn:
        for job in to_done_direct:
            conn.execute(update(annotation_jobs).where(annotation_jobs.c.job_id == job["job_id"])
                         .values(status="done", updated_at=now, lease_until=None, last_error=None))
        for job in to_supersede:
            conn.execute(update(annotation_jobs).where(annotation_jobs.c.job_id == job["job_id"])
                         .values(status="superseded", lease_until=None, last_error="Source input changed"))
        for job in to_dead:
            conn.execute(update(annotation_jobs).where(annotation_jobs.c.job_id == job["job_id"])
                         .values(status="dead", attempts=annotation_jobs.c.attempts + 1, lease_until=None,
                                 last_error="源文本为空或不存在，不应进队列", updated_at=now))
        for (job, src), pred in zip(usable, preds):
            rel = pred["relevance"]["label"]
            counts[rel] += 1
            review = "needs_review" if needs_review(pred) else "pending"
            rows = []
            ann_id = _insert(conn, job, "relevance", rel, pred["relevance"]["max"], review, run_id,
                             job["input_hash"], now)
            rows.append(("relevance", json.dumps(rel, ensure_ascii=False), pred["relevance"]["max"], review, ann_id))
            if rel == "relevant":
                att = pred["attitude"]
                ann_id = _insert(conn, job, "attitude", att["label"], att["max"], review, run_id,
                                 job["input_hash"], now)
                rows.append(("attitude", json.dumps(att["label"], ensure_ascii=False), att["max"], review, ann_id))
            stats["written"] += 1
            reasons = route_reasons(pred, src.get("text") or "")
            if reasons:
                for r in reasons:
                    routed_by[r] += 1
                n_routed += 1
                conn.execute(update(annotation_jobs).where(annotation_jobs.c.job_id == job["job_id"]).values(
                    stage=annotate.STAGE_LLM, status="pending", lease_until=None, updated_at=now,
                    last_error=None))
            else:
                _insert(conn, job, "compliance", LEXICON_CLEAN, None, "pending", rule_run_id,
                        prefilter.rule_input_hash(src.get("text"), "compliance_lexicon"), now)
                stats["compliance_rows"] += 1
                n_done += 1
                conn.execute(update(annotation_jobs).where(annotation_jobs.c.job_id == job["job_id"]).values(
                    status="done", updated_at=now, lease_until=None, last_error=None))
            # 簇成员抄代表的结论。路由走的也抄：成员先拿到学生的判断，Luna 改了再跟着换。
            neardup.propagate(conn, job["target_id"], job["subject_code"], rows, run_id, now,
                              taxonomy_version=cfg.taxonomy_version, schema_version=schema_version)

    stats["input"] += len(jobs)
    stats["members"] += len(to_done_direct)
    stats["superseded"] += len(to_supersede)
    stats["errors"] += len(to_dead)
    stats["routed"] += n_routed
    stats["done"] += n_done + len(to_done_direct)
    stats["batches"] += 1
    for k in counts:
        stats["relevance"][k] += counts[k]
    for k in routed_by:
        stats["routes"][k] += routed_by[k]

    codes = {j["subject_code"] for j in jobs}
    code = next(iter(codes)) if len(codes) == 1 else None
    msg = (f"{code or '多产品'} 批 {len(jobs):,} → 相关 {counts['relevant']:,} / 无关 {counts['irrelevant']:,}"
           f" / 需上下文 {counts['needs_context']:,} → 路由 Luna {n_routed:,}")
    if to_done_direct:
        msg += f"（近重复成员 {len(to_done_direct):,}）"
    emit(engine, "L1", msg, code=code, scope_id=scope_id, run_id=run_id,
         data={"n": len(jobs), **counts, "routed": n_routed, "routes": routed_by, "members": len(to_done_direct),
               "superseded": len(to_supersede), "dead": len(to_dead)})


def run(engine, cfg=None, *, scope_id=None, limit=None, batch_size=BATCH_SIZE, student=None,
        model_dir=None, dry_run=False, require_model=False):
    """跑到队列空或 `limit` 用完。返回统计 dict。

    `student`：注入一个有 `predict(texts)` 与 `model_id` 的对象（测试用）；不给就从 `model_dir`
    加载 `models.infer.Student`。
    """
    cfg = cfg or config.load(_allow_missing_key=True)
    prompt, schema_version = annotate.resolve(TASK, cfg)
    stats = _fresh_stats()
    stats["pending_before"] = annotate.pending_count(engine, TASK, scope_id, stage=annotate.STAGE_STUDENT)
    if dry_run:
        stats["dry_run"] = True
        return stats

    if student is None:
        try:
            from models.infer import Student, StudentUnavailable
            student = Student(model_dir)
        except Exception as exc:  # noqa: BLE001  StudentUnavailable 或依赖缺失都走同一条退化路径
            if require_model:
                raise
            n = _route_all_to_llm(engine, scope_id, limit, str(exc))
            stats.update(student_available=False, routed=n, input=n, reason=str(exc)[:300])
            emit(engine, "L1", f"学生模型不可用，{n:,} 条评论任务放行 Luna：{str(exc)[:120]}",
                 level="warn", scope_id=scope_id, data={"routed": n})
            log.warning("学生模型不可用（%s），%d 条放行 Luna", exc, n)
            return stats

    now = clock.now()
    run_id, rule_run_id = _open_runs(engine, cfg, schema_version, getattr(student, "model_id", "student"), now)
    stats["run_id"] = run_id
    budget = limit
    try:
        while budget is None or budget > 0:
            n = batch_size if budget is None else min(batch_size, budget)
            jobs = annotate.claim(engine, TASK, n, scope_id=scope_id, stage=annotate.STAGE_STUDENT)
            if not jobs:
                break
            if budget is not None:
                budget -= len(jobs)
            try:
                _process_batch(engine, cfg, prompt, schema_version, student, jobs, run_id, rule_run_id, stats, scope_id)
            except Exception as exc:  # noqa: BLE001  一批推理／写库炸了：放回 pending，别让整队卡在 claimed
                log.exception("学生批处理失败，%d 条放回待办", len(jobs))
                for job in jobs:
                    annotate._release(engine, job, f"学生批失败：{exc}")
                stats["errors"] += len(jobs)
                emit(engine, "L1", f"学生批失败（{len(jobs)} 条放回）：{str(exc)[:160]}", level="error",
                     scope_id=scope_id, run_id=run_id)
                break
    finally:
        _close_runs(engine, run_id, rule_run_id, stats)
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description="学生模型分类作业（ADR-0021 L1）")
    ap.add_argument("--scope", help="只处理这个抽取范围")
    ap.add_argument("--all-pending", action="store_true", help="队列里全部 stage=student 的评论任务")
    ap.add_argument("--limit", type=int, help="最多处理多少条")
    ap.add_argument("--batch", type=int, default=BATCH_SIZE)
    ap.add_argument("--model-dir", help="学生权重目录（默认 STUDENT_MODEL_DIR）")
    ap.add_argument("--dry-run", action="store_true", help="只数待办不推理不写")
    ap.add_argument("--require-model", action="store_true", help="没有学生模型时报错而不是放行 Luna")
    args = ap.parse_args(argv)
    if not args.scope and not args.all_pending:
        ap.error("给 --scope 或 --all-pending")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s %(message)s")
    engine = make_engine()
    cfg = config.load(_allow_missing_key=True)
    stats = run(engine, cfg, scope_id=args.scope, limit=args.limit, batch_size=args.batch,
                model_dir=args.model_dir, dry_run=args.dry_run, require_model=args.require_model)
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
