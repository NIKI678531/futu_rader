"""GET /api/v1/progress 的载荷（ADR-0021）—— 漏斗跑到哪了，只读。

## 为什么是一个独立端点而不是塞进 /meta

`/meta` 是启动时拿一次的常量与主数据；进度是每几秒刷一次的东西，两者缓存周期完全不同。
`/meta` 的 `analysisProgress`（`core/meta.py`）只有「61 只里完成几只」这一句，侧栏要看的是
队列、吞吐与事件流。这里把它原样嵌进 `summary`，两处不会各算一份。

## 载荷里每个数的口径

- `queue`：`annotation_jobs` 里 `task='comment_product'` 的行按 `stage × status` 计数。六个 status
  全部列出，没有的写 `0` —— 这是**数出来的零**（表在、查过、确实没有），不是未知。
- `tasks`：帖子与 KOL 评论任务按 status 计数（它们只有 `llm` 一段，不分 stage）。
- `synthesis.dirtyProducts`：`meta_kv` 里 `synth_dirty_<code>_<range>` 为 `"1"` 的**产品**数
  （去重到 code）；`productsWithOutputs`：`synthesis_outputs` 里出现过的产品数。
- `throughput.itemsPerSec5m`：近 5 分钟 `status='done'` 且 `updated_at` 落在窗内的评论任务数 ÷ 300。
  一条都没有 ⇒ `null`，不写 `0`（速率未知与速率为零在页面上必须长得不一样，铁律 2）。
  `etaSeconds`＝待办（pending＋claimed，两段合计）÷ 速率；速率为 `null` 就也是 `null`。
- `events`：最近 200 条按 id 升序（`radar_db.events.recent`）；`latestEventId` 给增量拉取当游标。

## 「近 5 分钟」的参考点不读系统时钟

backend 没有 `worker/clock.py` 那扇门（守卫③），而且就算有也不该用：compose 里 worker 跑在
`Asia/Hong_Kong`、backend 容器是 UTC，`updated_at` 是 worker 按它的钟写的，backend 拿自己的钟
去框「近 5 分钟」会差 8 小时，窗里永远一条都没有。所以参考点取**数据自己的钟**：评论任务最近
一次状态变更的 `updated_at`，窗＝它往前 300 秒。worker 在跑时它与墙上的钟只差几秒；worker 停了，
它停在最后一次变动 —— 这时若队列已空（没有 pending/claimed），「当前吞吐」这个量不存在，两个
字段都是 `null`；若队列没空（worker 死了、任务还挂着），这里给出的是**最后一个活动窗**的速率与
按它算的剩余时间，backend 没有钟、判不出它已经过期 —— 侧栏判「还活着吗」看 `latestEventId`
在两次轮询之间有没有前进，不看这两个数。

## 什么时候整个 data 是 None

demo provider 没有库（`_engine` 为 None）—— 信封判 `unavailable`。库在但还没跑迁移 0008
（没有 `stage` 列、没有 `worker_events` 表）时，查询会抛：同样返回 None，不猜。
"""

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from providers import get_provider

log = logging.getLogger("core.progress")

JOB_STATUSES = ("pending", "claimed", "done", "failed", "dead", "superseded")
STAGES = ("student", "llm")
OTHER_TASKS = ("post_annotation", "kol_comment_opinion")
EVENTS_DEFAULT = 200
EVENTS_MAX = 500
THROUGHPUT_WINDOW_SECONDS = 300


def _engine():
    provider = get_provider()
    return getattr(provider, "_engine", None)


def _summary():
    from core.meta import version_payload

    try:
        return version_payload().get("analysisProgress")
    except Exception:  # noqa: BLE001  进度概要坏了不该让整块进度不可用
        log.exception("version_payload 失败")
        return None


def _active_progress(conn):
    from sqlalchemy import select
    from radar_db.schema import meta_kv

    value = conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "own_analysis_progress")).scalar()
    return json.loads(value) if value else {}


def _scope_filter(conn):
    from sqlalchemy import or_, select, true
    from radar_db.schema import analysis_scope_jobs, annotation_jobs

    progress = _active_progress(conn)
    scopes = [row["scope"] for row in progress.get("products", {}).values() if row.get("scope")]
    if not scopes:
        return true()
    return or_(annotation_jobs.c.scope_id.in_(scopes),
               select(analysis_scope_jobs.c.job_id).where(
                   analysis_scope_jobs.c.scope_id.in_(scopes),
                   analysis_scope_jobs.c.job_id == annotation_jobs.c.job_id).exists())


def _queue(conn):
    from sqlalchemy import func, select

    from radar_db.schema import annotation_jobs

    out = {stage: {st: 0 for st in JOB_STATUSES} for stage in STAGES}
    rows = conn.execute(
        select(annotation_jobs.c.stage, annotation_jobs.c.status, func.count())
        .where(annotation_jobs.c.task == "comment_product", _scope_filter(conn))
        .group_by(annotation_jobs.c.stage, annotation_jobs.c.status)
    )
    for stage, status, n in rows:
        if stage in out and status in out[stage]:
            out[stage][status] = n
    return out


def _tasks(conn):
    from sqlalchemy import func, select

    from radar_db.schema import annotation_jobs

    out = {task: {st: 0 for st in JOB_STATUSES} for task in OTHER_TASKS}
    rows = conn.execute(
        select(annotation_jobs.c.task, annotation_jobs.c.status, func.count())
        .where(annotation_jobs.c.task.in_(OTHER_TASKS), _scope_filter(conn))
        .group_by(annotation_jobs.c.task, annotation_jobs.c.status)
    )
    for task, status, n in rows:
        if task in out and status in out[task]:
            out[task][status] = n
    return out


def _synthesis(conn):
    from sqlalchemy import func, select

    from radar_db.schema import meta_kv, synthesis_outputs

    progress = _active_progress(conn)
    codes = set(progress.get("products", {}))
    ranges = set(progress.get("ranges", []))
    dirty = set()
    for k, v in conn.execute(select(meta_kv.c.k, meta_kv.c.v).where(meta_kv.c.k.like("synth_dirty_%"))):
        if v == "1":
            # synth_dirty_<code>_<range>：区间名不含下划线，从右边切一刀就是 code。
            code, range_key = k[len("synth_dirty_"):].rsplit("_", 1)
            if (not codes or code in codes) and (not ranges or range_key in ranges):
                dirty.add(code)
    query = select(func.count(func.distinct(synthesis_outputs.c.code)))
    if codes:
        query = query.where(synthesis_outputs.c.code.in_(codes))
    if ranges:
        query = query.where(synthesis_outputs.c.range_key.in_(ranges))
    if progress.get("anchor"):
        query = query.where(synthesis_outputs.c.anchor == progress["anchor"])
    with_outputs = conn.execute(query).scalar_one()
    return {"dirtyProducts": len(dirty), "productsWithOutputs": int(with_outputs)}


def _throughput(conn, now=None):
    """`now` 只给测试冻结参考点用；不给就取评论任务最近一次变动的 `updated_at`（见模块文档）。"""
    from sqlalchemy import DateTime, func, select

    from radar_db.schema import annotation_jobs

    is_comment = (annotation_jobs.c.task == "comment_product") & _scope_filter(conn)
    unavailable = {"itemsPerSec5m": None, "etaSeconds": None}
    pending = conn.execute(
        select(func.count()).select_from(annotation_jobs).where(
            is_comment, annotation_jobs.c.status.in_(("pending", "claimed")),
        )
    ).scalar_one()
    if not pending:
        return unavailable
    ref = now or conn.execute(
        select(func.max(annotation_jobs.c.updated_at, type_=DateTime)).where(is_comment)
    ).scalar()
    if isinstance(ref, str):  # SQLite 的 max() 可能把 DateTime 当文本吐回来
        ref = datetime.fromisoformat(ref)
    if ref is None:
        return unavailable
    since = ref - timedelta(seconds=THROUGHPUT_WINDOW_SECONDS)
    done = conn.execute(
        select(func.count()).select_from(annotation_jobs).where(
            is_comment, annotation_jobs.c.status == "done",
            annotation_jobs.c.updated_at > since, annotation_jobs.c.updated_at <= ref,
        )
    ).scalar_one()
    if not done:
        return unavailable
    rate = done / THROUGHPUT_WINDOW_SECONDS
    return {"itemsPerSec5m": round(rate, 4), "etaSeconds": int(pending / rate)}


def _clamp_limit(limit):
    try:
        n = int(limit) if limit is not None else EVENTS_DEFAULT
    except (TypeError, ValueError):
        n = EVENTS_DEFAULT
    return max(1, min(n, EVENTS_MAX))


def progress_payload(*, now=None, events_limit=EVENTS_DEFAULT):
    engine = _engine()
    if engine is None:
        return None
    from radar_db.events import latest_id, recent

    try:
        with engine.connect() as conn:
            payload = {
                "summary": _summary(),
                "queue": _queue(conn),
                "tasks": _tasks(conn),
                "synthesis": _synthesis(conn),
                "throughput": _throughput(conn, now),
                "batchRun": _active_progress(conn).get("batchRun"),
            }
    except Exception as exc:  # noqa: BLE001  没跑迁移 0008 的库：整块「暂不可用」，不猜
        log.warning("进度查询失败（库没有 stage 列或 worker_events 表？）：%s", str(exc)[:200])
        return None
    payload["events"] = recent(engine, limit=_clamp_limit(events_limit))
    payload["latestEventId"] = latest_id(engine)
    return payload


def events_payload(*, after=None, limit=EVENTS_DEFAULT):
    engine = _engine()
    if engine is None:
        return None
    from radar_db.events import latest_id, recent

    try:
        after_id = int(after) if after is not None and str(after).strip() != "" else None
    except (TypeError, ValueError):
        after_id = None
    return {"events": recent(engine, after=after_id, limit=_clamp_limit(limit)), "latestEventId": latest_id(engine)}
