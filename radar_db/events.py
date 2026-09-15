"""worker 事件流的写入与读取（ADR-0021）—— `worker_events` 表的唯一入口。

## 两条纪律

1. **`emit()` 永远不向上抛。** 它是进度显示，不是业务写入：分类作业写完 2,000 条标注之后
   记一行「写完了」，这一行写失败不该让那 2,000 条标注回滚，更不该让作业停下。所以它用
   自己的短事务，任何异常只进日志。
2. **表会自己修剪。** 侧栏只看最近几百条，长期追溯走 `annotation_runs` / `analysis_scopes`。
   超过 `MAX_ROWS` 时删到 `KEEP_ROWS` —— 一次删一批而不是每次删一行，SQLite 上每次 emit 都
   多一条 DELETE 会把写锁争用翻倍。

## 为什么 `data` 是 JSON 文本

事件带的结构化数据（各类计数）形状随阶段不同：L1 报「相关／无关／需上下文／路由」，L3 报
「calls／written／skipped」。给它们各开列会让表随阶段演进不停加列，而消费方只是原样展示。
"""

import json
import logging
from datetime import datetime

from sqlalchemy import delete, func, insert, select

from .schema import worker_events

log = logging.getLogger("radar_db.events")

MAX_ROWS = 6000
KEEP_ROWS = 5000

LEVELS = ("info", "warn", "error")
STAGES = ("L0", "L1", "L2", "L3", "orchestrator")


def emit(engine, stage, message, *, level="info", code=None, scope_id=None, run_id=None,
         data=None, now=None):
    """写一条事件。返回 event_id；失败返回 None 且**不抛**。"""
    if level not in LEVELS:
        level = "info"
    if stage not in STAGES:
        stage = "orchestrator"
    ts = now or datetime.now()
    try:
        with engine.begin() as conn:
            res = conn.execute(
                insert(worker_events).values(
                    ts=ts, level=level, stage=stage, code=code, scope_id=scope_id, run_id=run_id,
                    message=str(message)[:4000],
                    data_json=json.dumps(data, ensure_ascii=False, default=str) if data is not None else None,
                )
            )
            event_id = res.inserted_primary_key[0]
            _trim(conn)
        return event_id
    except Exception as exc:  # noqa: BLE001  进度显示写失败不能拖垮作业
        log.warning("事件写入失败（忽略）：%s", str(exc)[:200])
        return None


def _trim(conn):
    total = conn.execute(select(func.count()).select_from(worker_events)).scalar_one()
    if total <= MAX_ROWS:
        return
    # 保留 id 最大的 KEEP_ROWS 行：找到第 KEEP_ROWS 大的 id，删比它小的。
    cutoff = conn.execute(
        select(worker_events.c.event_id).order_by(worker_events.c.event_id.desc())
        .offset(KEEP_ROWS - 1).limit(1)
    ).scalar()
    if cutoff is not None:
        conn.execute(delete(worker_events).where(worker_events.c.event_id < cutoff))


def recent(engine, after=None, limit=200):
    """最近的事件，按 id 升序。`after` 给了只取 id 更大的（增量拉取）。

    不给 `after` 时取的是**最新的** `limit` 条再正序排 —— 侧栏打开时要看的是最近发生的事，
    不是表里最老的 200 条。表不存在（没跑 0008 的库）返回 `[]`，那是「还没有事件」不是 500。
    """
    limit = max(1, min(int(limit or 200), 500))
    try:
        with engine.connect() as conn:
            if after is not None:
                q = (select(worker_events).where(worker_events.c.event_id > int(after))
                     .order_by(worker_events.c.event_id.asc()).limit(limit))
                rows = conn.execute(q).mappings().all()
            else:
                q = select(worker_events).order_by(worker_events.c.event_id.desc()).limit(limit)
                rows = list(reversed(conn.execute(q).mappings().all()))
    except Exception as exc:  # noqa: BLE001
        log.warning("事件读取失败：%s", str(exc)[:200])
        return []
    return [_to_dict(r) for r in rows]


def latest_id(engine):
    try:
        with engine.connect() as conn:
            return conn.execute(select(func.max(worker_events.c.event_id))).scalar()
    except Exception:  # noqa: BLE001
        return None


def _to_dict(r):
    data = None
    if r["data_json"]:
        try:
            data = json.loads(r["data_json"])
        except ValueError:
            data = None
    ts = r["ts"]
    return {
        "id": r["event_id"],
        "ts": ts.strftime("%Y-%m-%d %H:%M:%S") if isinstance(ts, datetime) else str(ts),
        "level": r["level"],
        "stage": r["stage"],
        "code": r["code"],
        "scopeId": r["scope_id"],
        "runId": r["run_id"],
        "message": r["message"],
        "data": data,
    }
