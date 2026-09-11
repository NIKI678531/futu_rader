"""基准区间与时间桶的**唯一算法实现**（PRD §3.1、§5 `buildRange(key)`）。

`core/ranges.py` 是端点侧的入口（校验 key、转发给 provider），这里是算法本身。分开两个
模块只有一个原因：`core/ranges.py` 要 `import providers`，而真实 provider 又要用这套日历
——放一起就是循环 import。这个模块**不 import 任何 provider**，两边都能安全引用。

演示 provider 不走这里：它读的 fixture 逐字来自设计源 `design/radar-data.js` 的
`buildRange()`，那是 100% 还原验收的基准（ADR-0006）。本模块是同一套规则的 Python 版，
`tests/test_calendar.py` 用 fixture 逐字节比对两者——这是「同一口径只有一份」在有两个
运行时（浏览器 / 后端）时唯一可行的表达方式。

锚点不在这里定义。演示锚点冻结在 2026-09-01（ADR-0012），真实锚点是导入时实测的最大
`posted_at`（2026-08-26），由调用方传进来。
"""

from datetime import date, timedelta

# PRD §3.1 的五个预设。顺序与 design/radar-data.js 的 PRESETS 一致。
PRESETS = {
    "d1": (1, "昨日"),
    "d2": (2, "近 2 天"),
    "d7": (7, "近 7 天"),
    "d14": (14, "近 14 天"),
    "d30": (30, "近 30 天"),
}

# JS 的 getUTCDay()：周日 = 0。Python 的 weekday()：周一 = 0。桶里的 `dow` 字段前端在用，
# 必须是 JS 那套编号。
_DOW = ("周日", "周一", "周二", "周三", "周四", "周五", "周六")


def _iso(d):
    return d.isoformat()


def _md(d):
    return d.strftime("%m-%d")


def _dow_index(d):
    return (d.weekday() + 1) % 7


def build(key, anchor):
    """返回 `key` 这个预设区间在 `anchor` 这一天的完整描述。

    `anchor` 是 `datetime.date`（「最近一个完整自然日」）。返回结构与设计源
    `buildRange()` 逐字一致，另加后端补的 `dates`（逐日日历轴，见 tests/test_ranges.py
    —— 桶的粒度在时/日/周之间变，替代不了它）。
    """
    days, label = PRESETS[key]
    to = anchor
    frm = anchor - timedelta(days=days - 1)
    bench_to = frm - timedelta(days=1)
    bench_from = bench_to - timedelta(days=days - 1)
    gran = "hour" if days <= 2 else ("day" if days <= 14 else "week")

    buckets = []
    if gran == "hour":
        for di in range(days):
            day = frm + timedelta(days=di)
            for h in range(24):
                buckets.append(
                    {
                        "i": len(buckets),
                        "span": 1 / 24,
                        "hour": h,
                        "day": _iso(day),
                        # 每 6 小时才标一个刻度，其余留空 —— 横轴标签密度是设计源定的。
                        "label": f"{h:02d}" if h % 6 == 0 else "",
                        "tip": f"{_md(day)} {h:02d}:00–{(h + 1) % 24:02d}:00",
                    }
                )
    elif gran == "day":
        for di in range(days):
            day = frm + timedelta(days=di)
            buckets.append(
                {
                    "i": len(buckets),
                    "span": 1,
                    "day": _iso(day),
                    "dow": _dow_index(day),
                    "label": _md(day),
                    "tip": f"{_md(day)}（{_DOW[_dow_index(day)]}）全天",
                }
            )
    else:
        w = 0
        while w * 7 < days:
            start = frm + timedelta(days=w * 7)
            length = min(7, days - w * 7)
            end = start + timedelta(days=length - 1)
            buckets.append(
                {
                    "i": len(buckets),
                    "span": length,
                    "day": _iso(start),
                    "label": f"W{w + 1}",
                    "tip": f"{_md(start)} ～ {_md(end)}（{length} 天）",
                }
            )
            w += 1

    return {
        "key": key,
        "days": days,
        "from": _iso(frm),
        "to": _iso(to),
        "label": label,
        "text": f"{_iso(frm)} ～ {_iso(to)}",
        "gran": gran,
        "granLabel": {"hour": "按小时", "day": "按自然日", "week": "按自然周"}[gran],
        "buckets": buckets,
        "dates": [_iso(frm + timedelta(days=i)) for i in range(days)],
        "benchFrom": _iso(bench_from),
        "benchTo": _iso(bench_to),
        "benchText": f"{_iso(bench_from)} ～ {_iso(bench_to)}",
        # d1/d2 的基准是「昨日同期」，不是「上一等长区间」—— 两句文案不可互换（PRD §3.1）。
        "benchLabel": "较昨日同期" if days <= 2 else "较上一等长区间",
        "trendTitle": "日内舆情与价格趋势" if days <= 2 else "区间舆情与价格趋势",
    }


def parse_anchor(s):
    """`'2026-08-26'` → `date`。给不出锚点时返回 None，不用今天兜底。

    用系统时间兜底会让「数据止于 8-26」这件事变成「近 7 天一条数据都没有」——
    在界面上和「这周确实没人发帖」长得一模一样（铁律 2 的失败模式）。
    """
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None
