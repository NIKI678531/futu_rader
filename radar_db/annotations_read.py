"""「现行结论」的**唯一**查询实现（ADR-0019 §1）—— backend 的 SqlProvider 与 worker 的
synthesize 共用这一份。

ADR-0019 逐条：

① **链末** —— 没有任何一行 supersede 它；
② `review_state != 'rejected'`；
③ 链末有多条（ADR-0017 遗留的双现行情况）时，人工 `approved` / `corrected`
   优先于模型待审行；同一优先级再取 `created_at` 最新的一条；
④ 链末是 rejected ⇒ 该单元**当前没有结论**，不回退到旧行。

④ 是 ① 与 ② 的乘积，不用另写分支：rejected 的那一行被 ② 滤掉，而被它 supersede 的旧行
被 ① 滤掉，于是这个单元一行都不剩。**判链末的子查询里不能再加 `review_state` 条件**，
加了就等于「被 rejected 的行不算数」，旧结论会自己爬回页面上，`--reject` 这唯一的
下线通道当场失效。

## 为什么搬到 radar_db

原本它是 `backend/providers/sql.py` 的一个方法。Layer B（`worker/jobs/synthesize.py`）
要按同一条规则取评论的态度与 aspect 去组事实 JSON —— 在 worker 里再写一遍就是两份
发布规则，改一处漏一处。`radar_db/` 本来就是两侧共用的那一层（schema 也在这里）。
"""

import json

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from .comment_filter import qualifying_feed_scope_predicate
from .comment_routes import readiness_on_connection, route_exists_predicate
from .schema import (
    annotation_jobs,
    annotation_runs,
    annotations,
    comments,
    feeds,
    review_decisions,
)


_HUMAN_SETTLED = frozenset(("approved", "corrected"))

# Eligibility is independent of the model/prompt rollout. Keep both deployed
# comment-product prompt contracts readable through this one seam.
COMMENT_PRODUCT_PROMPT_VERSIONS = (
    "comment-product-v2",
    "comment-product-v3",
)


def _chunked(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def current_annotations(
    engine,
    kind,
    target_type,
    ids=None,
    window=None,
    subject_code=None,
    parent_filter_config=None,
):
    """某个 kind 的现行结论，键为判定单元的后两半 `(target_id, subject_code)`。

    - `target_type` 必需：`annotations.target_id` 在 `feed` 与 `comment` 两个命名空间里各自取值。
    - `ids` 按 900 一批切（SQLite 绑定变量上限 999，ADR-0016）；给了空集合就一条查询都不发。
    - `window=(lo, hi)` 按**帖子的** `posted_at` 收半开窗口（评论的 posted_at 在瘦库里大量为 NULL，
      归桶口径一路取所在帖子的）。
    - `subject_code` 给了就只取这只产品的判定单元。
    - 评论内容路由已激活时，评论必须仍存在同 code 的有效路由；否则仅在迁移兼容期按
      `parent_filter_config` 限定同 code 的合格父帖。失效历史结论仍留作审计，但不进入页面或合成。

    返回 `{(target_id, subject_code): {annotation_id, created_at, review_state, confidence,
    value, input_hash, feed_id, posted_at}}`。标注表不存在（没跑过 Alembic 的库）⇒ `{}`，那是「还没标注」
    不是 500；`value_json` 不是合法 JSON 则照常抛 —— 那是库坏了，不能伪装成「这条还没标」。
    """
    if ids is not None and not ids:
        return {}

    if target_type == "comment":
        src = annotations.join(comments, comments.c.comment_id == annotations.c.target_id).join(
            feeds, feeds.c.feed_id == comments.c.feed_id
        )
    else:
        src = annotations.join(feeds, feeds.c.feed_id == annotations.c.target_id)

    newer = annotations.alias("newer")
    chain_end = ~(
        select(newer.c.annotation_id).where(newer.c.supersedes_id == annotations.c.annotation_id).exists()
    )
    stmt = (
        select(
            annotations.c.annotation_id,
            annotations.c.target_id,
            annotations.c.subject_code,
            annotations.c.value_json,
            annotations.c.review_state,
            annotations.c.calibrated_confidence,
            annotations.c.created_at,
            annotations.c.run_id,
            annotations.c.input_hash,
            annotations.c.supersedes_id,
            feeds.c.feed_id,
            feeds.c.posted_at,
        )
        .select_from(src)
        .where(
            annotations.c.kind == kind,
            annotations.c.target_type == target_type,
            annotations.c.review_state != "rejected",
            chain_end,
        )
    )
    if window is not None:
        stmt = stmt.where(feeds.c.posted_at >= window[0], feeds.c.posted_at < window[1])
    if subject_code is not None:
        stmt = stmt.where(annotations.c.subject_code == subject_code)
    route_active = False
    if target_type == "comment":
        try:
            with engine.connect() as conn:
                route_active = readiness_on_connection(conn)
        except SQLAlchemyError:
            route_active = False
    if route_active:
        stmt = stmt.where(
            route_exists_predicate(
                annotations.c.target_id,
                annotations.c.subject_code,
            )
        )
    elif parent_filter_config is not None:
        if target_type != "comment":
            raise ValueError("parent_filter_config is only valid for comment annotations")
        stmt = stmt.where(
            annotations.c.subject_code == feeds.c.code,
            qualifying_feed_scope_predicate(
                feeds,
                parent_filter_config,
                codes=(subject_code,) if subject_code is not None else None,
            ),
        )

    chunks = [None] if ids is None else _chunked(list(ids), 900)
    out = {}
    try:
        with engine.connect() as conn:
            for chunk in chunks:
                q = stmt if chunk is None else stmt.where(annotations.c.target_id.in_(chunk))
                for r in conn.execute(q):
                    unit = (r.target_id, r.subject_code)
                    prev = out.get(unit)
                    # ③ 同一链末多行取最新。`created_at` 同秒时按 annotation_id 兜底，
                    # 不然「最新」会随库的返回顺序变，两次请求两个答案。
                    rank = (
                        r.review_state in _HUMAN_SETTLED,
                        r.created_at,
                        r.annotation_id,
                    )
                    prev_rank = None if prev is None else (
                        prev["review_state"] in _HUMAN_SETTLED,
                        prev["created_at"],
                        prev["annotation_id"],
                    )
                    if prev_rank is not None and prev_rank >= rank:
                        continue
                    out[unit] = {
                        "annotation_id": r.annotation_id,
                        "created_at": r.created_at,
                        "review_state": r.review_state,
                        # 校准置信度，当前全库为 NULL（ADR-0017 §4）。照取不硬编码 None。
                        "confidence": r.calibrated_confidence,
                        "value": json.loads(r.value_json),
                        "feed_id": r.feed_id,
                        "posted_at": r.posted_at,
                        # 训练集要按写入方（rule／LLM／local_model／propagated）筛，run_id 是唯一线索。
                        "run_id": r.run_id,
                        "input_hash": r.input_hash,
                        "supersedes_id": r.supersedes_id,
                    }
    except SQLAlchemyError:
        return {}
    return out


def released_annotations(
    engine,
    kind,
    target_type,
    *,
    task,
    prompt_version,
    ids=None,
    window=None,
    subject_code=None,
    parent_filter_config=None,
):
    """Return current conclusions with auditable task/review lineage.

    ``current_annotations`` owns chain selection and parent-feed scope.  This
    second half of the same read seam owns queue/run lineage. ``prompt_version``
    may be one version or an iterable of compatible versions. A job only gates
    the annotation produced from the *same* input hash: a pending prompt
    experiment must not hide an otherwise valid historical conclusion, while
    an explicitly superseded/failed job for that exact input remains blocked.

    Human corrections intentionally have ``run_id=review:<decision_id>`` and
    no fabricated ``annotation_runs`` row. They are released only after every
    correction link is verified against ``review_decisions`` and the chain
    terminates at an otherwise publishable model run.
    """

    raw = current_annotations(
        engine,
        kind,
        target_type,
        ids=ids,
        window=window,
        subject_code=subject_code,
        parent_filter_config=parent_filter_config,
    )
    if not raw:
        return {}

    allowed_prompt_versions = (
        {prompt_version}
        if isinstance(prompt_version, str)
        else set(prompt_version)
    )
    if not allowed_prompt_versions or any(
        not isinstance(version, str) or not version for version in allowed_prompt_versions
    ):
        raise ValueError("prompt_version must name at least one non-empty version")

    target_ids = {unit[0] for unit in raw}
    jobs_by_input = {}
    origin_run_ids = {}
    run_policies = {}
    try:
        with engine.connect() as conn:
            for chunk_number, chunk in enumerate(_chunked(sorted(target_ids), 900)):
                latest = (
                    select(
                        annotation_jobs.c.target_id.label("target_id"),
                        annotation_jobs.c.subject_code.label("subject_code"),
                        annotation_jobs.c.input_hash.label("input_hash"),
                        func.max(annotation_jobs.c.job_id).label("job_id"),
                    )
                    .where(
                        annotation_jobs.c.target_type == target_type,
                        annotation_jobs.c.task == task,
                        annotation_jobs.c.target_id.in_(chunk),
                    )
                    .group_by(
                        annotation_jobs.c.target_id,
                        annotation_jobs.c.subject_code,
                        annotation_jobs.c.input_hash,
                    )
                    .subquery(f"latest_release_jobs_{chunk_number}")
                )
                for row in conn.execute(
                    select(
                        annotation_jobs.c.target_id,
                        annotation_jobs.c.subject_code,
                        annotation_jobs.c.input_hash,
                        annotation_jobs.c.status,
                    ).join(latest, annotation_jobs.c.job_id == latest.c.job_id)
                ):
                    jobs_by_input[(row.target_id, row.subject_code, row.input_hash)] = {
                        "status": row.status,
                    }

            for unit, row in raw.items():
                origin_run_ids[row["annotation_id"]] = _review_origin_run_id(
                    conn,
                    row,
                    unit=unit,
                    kind=kind,
                    target_type=target_type,
                )

            run_ids = {run_id for run_id in origin_run_ids.values() if run_id}
            for chunk in _chunked(sorted(run_ids), 900):
                for row in conn.execute(
                    select(
                        annotation_runs.c.run_id,
                        annotation_runs.c.task,
                        annotation_runs.c.provider,
                        annotation_runs.c.prompt_version,
                    ).where(annotation_runs.c.run_id.in_(chunk))
                ):
                    run_policies[row.run_id] = {
                        "task": row.task,
                        "provider": row.provider,
                        "prompt_version": row.prompt_version,
                    }
    except SQLAlchemyError:
        # Missing/corrupt lineage must never widen what downstream consumers
        # treat as released AI output.
        return {}

    out = {}
    for unit, row in raw.items():
        policy = run_policies.get(origin_run_ids.get(row["annotation_id"]))
        if (
            not policy
            or policy["task"] != task
            or policy["provider"] == "rule"
            or policy["prompt_version"] not in allowed_prompt_versions
        ):
            continue
        job = jobs_by_input.get((*unit, row.get("input_hash")))
        if job is not None and job["status"] != "done":
            continue
        out[unit] = row
    return out


def _review_origin_run_id(conn, row, *, unit, kind, target_type):
    """Validate ``review.py`` correction links and return their model run id.

    The correction row itself is auditable through ``review_decisions`` rather
    than ``annotation_runs``.  Walking to the originating run also supports a
    correction of an earlier correction without weakening model-run checks.
    """

    current = row
    seen = set()
    while isinstance(current.get("run_id"), str) and current["run_id"].startswith("review:"):
        if current.get("review_state") not in _HUMAN_SETTLED:
            return None
        suffix = current["run_id"][len("review:"):]
        if not suffix.isdigit() or int(suffix) <= 0 or f"review:{int(suffix)}" != current["run_id"]:
            return None
        decision_id = int(suffix)
        if decision_id in seen:
            return None
        seen.add(decision_id)

        decision = conn.execute(
            select(
                review_decisions.c.annotation_id,
                review_decisions.c.decision,
                review_decisions.c.corrected_value_json,
            ).where(review_decisions.c.decision_id == decision_id)
        ).mappings().first()
        if (
            decision is None
            or decision["decision"] != "correct"
            or current.get("supersedes_id") != decision["annotation_id"]
            or decision["corrected_value_json"] is None
        ):
            return None
        try:
            corrected_value = json.loads(decision["corrected_value_json"])
        except (TypeError, json.JSONDecodeError):
            return None
        if corrected_value != current.get("value"):
            return None

        previous = conn.execute(
            select(
                annotations.c.annotation_id,
                annotations.c.target_type,
                annotations.c.target_id,
                annotations.c.subject_code,
                annotations.c.kind,
                annotations.c.value_json,
                annotations.c.review_state,
                annotations.c.run_id,
                annotations.c.input_hash,
                annotations.c.supersedes_id,
            ).where(annotations.c.annotation_id == decision["annotation_id"])
        ).mappings().first()
        if (
            previous is None
            or previous["target_type"] != target_type
            or previous["target_id"] != unit[0]
            or previous["subject_code"] != unit[1]
            or previous["kind"] != kind
            or previous["input_hash"] != current.get("input_hash")
        ):
            return None
        try:
            previous_value = json.loads(previous["value_json"])
        except (TypeError, json.JSONDecodeError):
            return None
        current = {**previous, "value": previous_value}

    return current.get("run_id")
