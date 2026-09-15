"""产品话题情绪的计数口径（PRD §4.2 P13）。

PRD 逐字：「市场方向、指数涨跌与宏观事件等不计入产品赞踩的讨论在此呈现」。

叶子模块。原料是 v2 标注的 `market_direction`（ADR-0020）：一条评论对产品 `irrelevant`
但对市场 `bearish`，就落在这里。三色计数是 bullish / bearish / neutral —— 页面契约叫
`positive / negative / neutral`，这里按名映射，**不是**产品态度。

## 一个区间一个话题

没有向量聚类，话题的成员关系只有一个确定性依据：「有市场方向」。所以一个产品 × 区间
只有**一个**话题（市场方向讨论），模型给它起名、写一句摘要（`synthesis_outputs`
kind=`topic_label`，subkey=`market`）。要分成多个话题得先有聚类 —— 那是 P1。
设计源每个区间给三个话题；这里给一个真的，不给三个编的。
"""

from .delta import delta

DIRECTION_TO_TRICOLOR = {"bullish": "positive", "bearish": "negative", "neutral": "neutral"}
DEFAULT_TITLE = "市场方向与指数走势讨论"
MARKET_SUBKEY = "market"


def market_topic(code, units, buckets, bucket_index, label=None, base_units=None):
    """`units`：带 `market_direction`（非 None）与 `posted_at` 的判定单元。返回话题列表（0 或 1 个）。"""
    if not units:
        return []
    nb = len(buckets)
    per_bucket = [0] * nb
    tri = {"positive": 0, "negative": 0, "neutral": 0}
    for u in units:
        k = DIRECTION_TO_TRICOLOR.get(u.get("market_direction"))
        if k is None:
            continue
        tri[k] += 1
        bi = bucket_index(u) if nb else None
        if bi is not None and 0 <= bi < nb:
            per_bucket[bi] += 1
    mentions = sum(tri.values())
    if mentions == 0:
        return []
    peak_i = max(range(nb), key=lambda i: per_bucket[i]) if nb else None
    lab = label or {}
    base_mentions = None if base_units is None else sum(
        unit.get("market_direction") in DIRECTION_TO_TRICOLOR for unit in base_units
    )
    return [
        {
            "id": f"{code}-tp-{MARKET_SUBKEY}",
            "subkey": MARKET_SUBKEY,
            "title": lab.get("title") or DEFAULT_TITLE,
            "summary": lab.get("summary")
            or f"区间内 {mentions} 条评论谈到市场或指数方向而未评价产品本身：看多 {tri['positive']}、看空 {tri['negative']}、无方向 {tri['neutral']}。",
            "labelStatus": "ok" if lab.get("title") else "unavailable",
            "mentions": mentions,
            "delta": delta(mentions, base_mentions),
            "evidenceCount": mentions,
            "confidence": None,
            **tri,
            "buckets": [
                {"label": b["label"], "tip": b["tip"], "mentions": n} for b, n in zip(buckets, per_bucket)
            ],
            "peak": buckets[peak_i]["tip"] if peak_i is not None else None,
            "split": f"看多 {tri['positive']} 条、看空 {tri['negative']} 条、无方向 {tri['neutral']} 条",
            "evidenceIds": lab.get("evidence_ids") or [],
            "reviewState": lab.get("reviewState"),
        }
    ]
