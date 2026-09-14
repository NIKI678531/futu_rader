"""阶段观点的切分、判定与合并（PRD §4.2 P12 `STAGE_RULE`）—— 设计源算法的 Python 版。

叶子模块：不 import provider。设计源 `design/radar-data.js:1837–1915` 的三步逐字移植，
唯一的替换是**第 2 步的观点分类**：设计源按散列随机挑一类，这里由调用方传 `cat_of(unit)`
（真实实现读 `synthesis_outputs` kind=`stage`，由模型按该时段的评论判 7 类）。

## 哪些是口径、哪些是模型

- 切时段（当日三段／逐日）、样本阈值、情绪（正负净值 ±0.12）、合并（相邻同类归并、
  样本不足并入相邻、≥14 天单日孤立并入前一阶段）—— **全部在这里**，确定性。
- 每个时段属于 7 类里的哪一类、一句话总结 —— 模型。模型没给（还没生成）⇒ `cat=None`，
  该时段按「样本不足」路径并入相邻阶段，页面照常渲染，只是少一个观点标签。

## 情绪不是分类的附属

设计源里 `sentiment = cat.tone`。真库里情绪来自计数（正负净值），分类来自模型；两者
可能不相容（模型说「加仓机会」而计数偏负）。这里**以计数为准**给 `sentiment`，分类照
给并带 `toneMismatch=True`，供 audit 统计。把计数改成迁就模型，就是让模型写数。
"""

from math import ceil

from .attitude import LOW_SAMPLE

PERIODS = (
    {"k": "am", "label": "上午", "from": 0, "to": 12, "text": "00:00–12:00"},
    {"k": "pm", "label": "下午", "from": 12, "to": 17, "text": "12:00–17:00"},
    {"k": "post", "label": "盘后", "from": 17, "to": 24, "text": "17:00–24:00"},
)

STAGE_CATS = {
    "add_opportunity": {"label": "加仓机会", "tone": "positive"},
    "pullback_done": {"label": "回撤到位", "tone": "positive"},
    "wait": {"label": "观望等待", "tone": "neutral"},
    "divergence": {"label": "分歧加大", "tone": "neutral"},
    "event": {"label": "事件驱动", "tone": "neutral", "hue": "event"},
    "reduce": {"label": "减仓离场", "tone": "negative"},
    "product_issue": {"label": "产品问题", "tone": "negative"},
}
CAT_BY_TONE = {
    "positive": ("add_opportunity", "pullback_done", "event"),
    "negative": ("reduce", "product_issue"),
    "neutral": ("wait", "divergence", "event"),
}
STAGE_STYLE = {
    "positive": {"bg": "var(--positive-100)", "fg": "var(--positive-700)", "band": "rgba(31,138,91,0.09)"},
    "negative": {"bg": "var(--negative-100)", "fg": "var(--negative-700)", "band": "rgba(197,48,48,0.08)"},
    "neutral": {"bg": "var(--ink-100)", "fg": "var(--ink-600)", "band": "rgba(110,122,138,0.10)"},
    "event": {"bg": "var(--csop-blue-50)", "fg": "var(--csop-blue-700)", "band": "rgba(35,97,173,0.09)"},
    "low": {"bg": "var(--ink-100)", "fg": "var(--ink-500)", "band": "transparent"},
}
ATT_LABEL = {"positive": "积极", "negative": "消极", "neutral": "中性"}
STAGE_RULE = (
    "阶段观点：当日按上午（00:00–12:00）／下午（12:00–17:00）／盘后（17:00–24:00）三段各归纳一条主流观点；"
    "多日先逐日归纳主流观点与情绪，再由 AI 把观点相近的连续日期合并为同一阶段，14 天及以上视图下单日孤立观点"
    "并入相邻阶段。样本不足的时段不参与合并，只在折线下方以灰点标记。阶段总结描述讨论区观点，不表述与价格的因果关系。"
)
NET_THRESHOLD = 0.12
LOW_DIGEST = "样本不足，暂无主流观点"
NO_TALK_DIGEST = "尚无讨论"

_DOW = ("周日", "周一", "周二", "周三", "周四", "周五", "周六")


def _md(day_iso):
    return day_iso[5:7] + "-" + day_iso[8:10]


def _dow(day_iso):
    from datetime import date

    d = date.fromisoformat(day_iso)
    return _DOW[(d.weekday() + 1) % 7]


def _agg(pts, label, sub, day):
    u = {"day": day, "label": label, "sub": sub, "idxFrom": pts[0]["i"], "idxTo": pts[-1]["i"],
         "mentions": 0, "heat": 0, "positive": 0, "negative": 0, "neutral": 0, "period": None}
    for p in pts:
        u["mentions"] += p["mentions"] or 0
        u["heat"] += p["heat"] or 0
        u["positive"] += p["positive"] or 0
        u["negative"] += p["negative"] or 0
        u["neutral"] += p["neutral"] or 0
    return u


def units_from_series(series, half_day):
    """第 1 步：切时段。小时粒度 → 每天三段；日粒度 → 每天一段。

    `series` 是 `heat_series_for` 的点（`i, day, hour, label, tip, heat, mentions, positive…`）。
    态度为 None（还没标注）的点按 0 参与——这里只切时段，样本够不够在第 2 步判。
    """
    units = []
    if half_day:
        by_day = {}
        for p in series:
            by_day.setdefault(p["day"], []).append(p)
        for day in sorted(by_day):
            for pr in PERIODS:
                pts = [p for p in by_day[day] if pr["from"] <= (p.get("hour") or 0) < pr["to"]]
                if pts:
                    u = _agg(pts, f"{_md(day)} {pr['label']}", pr["text"], day)
                    u["period"] = pr["k"]
                    units.append(u)
    else:
        for p in series:
            units.append(_agg([p], p["label"], _dow(p["day"]), p["day"]))
    return units


def unit_key(unit):
    """时段的稳定键，`synthesis_outputs.subkey` 用它：`2026-08-20|am` 或 `2026-08-20`。"""
    return f"{unit['day']}|{unit['period']}" if unit.get("period") else unit["day"]


def classify_units(units, half_day, cat_of=None, digest_of=None):
    """第 2 步：样本阈值、情绪、观点分类。

    `cat_of(unit)` 返回 7 类之一或 None；`digest_of(unit)` 返回 ≤40 字的一句话或 None。
    两者都由调用方从 `synthesis_outputs` 取；没有就是 None。返回阈值。
    """
    threshold = ceil(LOW_SAMPLE / 2) if half_day else LOW_SAMPLE
    for u in units:
        valid = u["positive"] + u["negative"]
        u["sufficient"] = valid >= threshold
        net = (u["positive"] - u["negative"]) / valid if valid else 0
        u["tone"] = (
            ("positive" if net > NET_THRESHOLD else "negative" if net < -NET_THRESHOLD else "neutral")
            if u["sufficient"] else None
        )
        cat = cat_of(u) if (u["tone"] and cat_of) else None
        u["cat"] = cat if cat in STAGE_CATS else None
        u["toneMismatch"] = bool(u["cat"]) and u["cat"] not in CAT_BY_TONE[u["tone"]]
        if u["tone"]:
            d = digest_of(u) if digest_of else None
            u["digest"] = d or None
        else:
            u["digest"] = LOW_DIGEST if u["mentions"] else NO_TALK_DIGEST
    return threshold


def merge_units(units, half_day, days):
    """第 3 步：相邻同类归并；样本不足／未分类的并入相邻；≥14 天单日孤立并入前一阶段。"""
    stages = []
    for u in units:
        last = stages[-1] if stages else None
        if not u["cat"]:
            if last:
                last["units"].append(u)
                last["low"] += 1
            else:
                stages.append({"cat": None, "units": [u], "low": 1, "absorbed": 0})
            continue
        if last and (last["cat"] == u["cat"] or last["cat"] is None):
            last["cat"] = u["cat"]
            last["units"].append(u)
            continue
        stages.append({"cat": u["cat"], "units": [u], "low": 0, "absorbed": 0})
    if not half_day and days >= 14:
        merged = []
        for s in stages:
            prev = merged[-1] if merged else None
            view_days = len(s["units"]) - s["low"]
            if prev and prev["cat"] and view_days <= 1:
                prev["units"].extend(s["units"])
                prev["low"] += s["low"]
                prev["absorbed"] += view_days
            else:
                merged.append(s)
        stages = merged
    return stages


def stage_key(stage):
    """阶段的稳定键（给模型写阶段总结时用）：成员时段键相连。"""
    return "+".join(unit_key(u) for u in stage["units"])


def build(code, series, gran, days, cat_of=None, digest_of=None, summary_of=None):
    """完整的 `stagesFor` 载荷。`summary_of(stage)` 返回该阶段 ≤40 字总结或 None。"""
    half = gran == "hour"
    base = {
        "code": code,
        "granularity": "half_day" if half else "day",
        "granLabel": "按上午／下午／盘后归纳" if half else "按自然日归纳，相近观点合并为阶段",
        "series": series,
        "stages": [],
        "rule": STAGE_RULE,
    }
    units = units_from_series(series, half)
    threshold = classify_units(units, half, cat_of, digest_of)
    stages = merge_units(units, half, days)

    out = []
    for i, s in enumerate(stages):
        f, l = s["units"][0], s["units"][-1]
        cat = STAGE_CATS.get(s["cat"]) if s["cat"] else None
        mentions = sum(u["mentions"] for u in s["units"])
        heat = sum(u["heat"] for u in s["units"])
        pos = sum(u["positive"] for u in s["units"])
        neg = sum(u["negative"] for u in s["units"])
        sample = pos + neg
        # 情绪以计数为准（模块头）。
        net = (pos - neg) / sample if sample else 0
        tone = (
            ("positive" if net > NET_THRESHOLD else "negative" if net < -NET_THRESHOLD else "neutral")
            if cat else None
        )
        style = STAGE_STYLE[cat.get("hue") or tone] if cat and tone else STAGE_STYLE["low"]
        summary = None
        if cat:
            summary = summary_of(s) if summary_of else None
            if summary is None and len(s["units"]) == 1:
                summary = f["digest"]
        if half:
            sub = f["sub"] if (f["sub"] == l["sub"] and f["day"] == l["day"]) \
                else f["sub"].split("–")[0] + "–" + l["sub"].split("–")[1]
        else:
            sub = f"{len(s['units'])} 天"
        out.append(
            {
                "n": i + 1, "from": f["day"], "to": l["day"], "idxFrom": f["idxFrom"], "idxTo": l["idxTo"],
                "key": stage_key(s),
                "label": f["label"] if f["label"] == l["label"] else f"{f['label']} — {l['label']}",
                "sub": sub,
                "category": s["cat"], "categoryLabel": cat["label"] if cat else "样本不足",
                "sentiment": tone, "sentimentLabel": ATT_LABEL[tone] if tone else "—",
                "bg": style["bg"], "fg": style["fg"], "band": style["band"],
                # 模型还没写这一段 ⇒ None（暂不可用），不是「样本不足」那句。
                "summary": summary if cat else LOW_DIGEST,
                "mentions": mentions, "heat": heat, "sample": sample,
                "evidenceCount": sample if cat else 0,
                "unitCount": len(s["units"]), "lowUnits": s["low"], "absorbed": s["absorbed"],
                "digests": [
                    {
                        "key": unit_key(u), "label": u["label"], "sub": u["sub"], "tone": u["tone"],
                        "toneLabel": ATT_LABEL[u["tone"]] if u["tone"] else "样本不足",
                        "digest": u["digest"], "mentions": u["mentions"], "heat": u["heat"],
                        "sufficient": u["sufficient"], "category": u["cat"],
                    }
                    for u in s["units"]
                ],
            }
        )
    any_view = any(s["category"] for s in out)
    any_mention = any((p.get("mentions") or 0) > 0 for p in series)
    any_sufficient = any(u["sufficient"] for u in units)
    # 有足量时段但一个分类都没有 ⇒ 模型还没写（unavailable），不是样本不足。
    status = "ok" if any_view else ("unavailable" if any_sufficient else ("low_sample" if any_mention else "empty"))
    return {**base, "status": status, "stages": out, "unitCount": len(units), "threshold": threshold,
            "units": [{"key": unit_key(u), "tone": u["tone"], "sufficient": u["sufficient"],
                       "mentions": u["mentions"], "positive": u["positive"], "negative": u["negative"],
                       "neutral": u["neutral"], "day": u["day"], "period": u.get("period"),
                       "idxFrom": u["idxFrom"], "idxTo": u["idxTo"]} for u in units]}
