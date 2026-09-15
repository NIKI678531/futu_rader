"""读路径缓存失效的两个开关：域修订号与「汇总待更新」脏标记。

- `bump_revision(conn, domain)`：`meta_kv.<domain>_revision` 换一个随机值。SqlProvider
  的 `refresh()` 比对整份 meta_kv，值一变就丢缓存。
- `mark_synthesis(conn, codes, dirty, ranges)`：`meta_kv.synth_dirty_<code>_<range>` 写 1/0。
  读路径见 1 就把该产品该区间的 Layer B 生成物标成「待更新」。

## 为什么 `mark_synthesis` 要按区间标

标注写入方（`annotate._close_run`）原来把产品的**六个**区间全部标脏。但一轮只改了 8 月
20 日的评论时，`d30` 与 `mtd` 确实要重算，`d1`（8 月 25 日）却一条都没动 —— 标脏它会让
页面上一块本来正确的内容变成「待更新」，直到下一次汇总。所以调用方按变更评论的日期算出
相交的区间，只传那几个（`ranges` 参数）；不传就是旧行为，全标。
"""

import uuid

from sqlalchemy import insert, update

from .schema import meta_kv

ALL_RANGES = ("d1", "d2", "d7", "d14", "d30", "mtd")


def bump_revision(conn, domain):
    key = f"{domain}_revision"
    revision = uuid.uuid4().hex
    result = conn.execute(update(meta_kv).where(meta_kv.c.k == key).values(v=revision))
    if not result.rowcount:
        conn.execute(insert(meta_kv).values(k=key, v=revision))
    return revision


def mark_synthesis(conn, codes, dirty, ranges=ALL_RANGES):
    for code in set(codes):
        if not code:
            continue
        for range_key in ranges:
            key = f"synth_dirty_{code}_{range_key}"
            value = "1" if dirty else "0"
            if not conn.execute(update(meta_kv).where(meta_kv.c.k == key).values(v=value)).rowcount:
                conn.execute(insert(meta_kv).values(k=key, v=value))


def ranges_touching(anchor, day_lo, day_hi, presets=ALL_RANGES):
    """`[day_lo, day_hi]`（含）这段日期与哪些预设区间的当前窗或基准窗相交。

    `anchor` 是 `datetime.date`；`day_lo/day_hi` 同。区间日历用 `backend/core/calendar.build`
    —— 那是唯一实现处，这里只取它的四个日期边界。import 放在函数内：radar_db 被 backend
    与 worker 共用，模块级 import backend 会让 worker 的启动路径依赖 backend 的 sys.path 守卫。
    """
    import sys
    from datetime import date
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1] / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from core.calendar import build  # noqa: E402

    out = []
    for key in presets:
        rng = build(key, anchor)
        for lo_k, hi_k in (("from", "to"), ("benchFrom", "benchTo")):
            lo, hi = date.fromisoformat(rng[lo_k]), date.fromisoformat(rng[hi_k])
            if lo <= day_hi and day_lo <= hi:
                out.append(key)
                break
    return out
