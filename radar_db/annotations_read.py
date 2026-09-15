"""「现行结论」的**唯一**查询实现（ADR-0019 §1）—— backend 的 SqlProvider 与 worker 的
synthesize 共用这一份。

ADR-0019 逐条：

① **链末** —— 没有任何一行 supersede 它；
② `review_state != 'rejected'`；
③ 链末有多条（ADR-0017 遗留的双现行情况）时取 `created_at` 最新的一条；
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

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .schema import annotations, comments, feeds


def _chunked(items, n):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def current_annotations(engine, kind, target_type, ids=None, window=None, subject_code=None):
    """某个 kind 的现行结论，键为判定单元的后两半 `(target_id, subject_code)`。

    - `target_type` 必需：`annotations.target_id` 在 `feed` 与 `comment` 两个命名空间里各自取值。
    - `ids` 按 900 一批切（SQLite 绑定变量上限 999，ADR-0016）；给了空集合就一条查询都不发。
    - `window=(lo, hi)` 按**帖子的** `posted_at` 收半开窗口（评论的 posted_at 在瘦库里大量为 NULL，
      归桶口径一路取所在帖子的）。
    - `subject_code` 给了就只取这只产品的判定单元。

    返回 `{(target_id, subject_code): {annotation_id, created_at, review_state, confidence,
    value, feed_id, posted_at}}`。标注表不存在（没跑过 Alembic 的库）⇒ `{}`，那是「还没标注」
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
                    if prev is not None and (prev["created_at"], prev["annotation_id"]) >= (
                        r.created_at, r.annotation_id,
                    ):
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
                    }
    except SQLAlchemyError:
        return {}
    return out
