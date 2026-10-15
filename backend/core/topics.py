"""产品话题情绪的计数口径（PRD §4.2 P13）。

PRD 逐字：「市场方向、指数涨跌与宏观事件等不计入产品赞踩的讨论在此呈现」。

叶子模块。原料是 v2 标注的 `market_direction`（ADR-0020）：一条评论对产品 `irrelevant`
但对市场 `bearish`，就落在这里。三色计数是 bullish / bearish / neutral —— 页面契约叫
`positive / negative / neutral`，这里按名映射，**不是**产品态度。

`market_outlook_row` 是组合视图：`attitude*` 读取当前产品的产品态度，
`sentiment*` 读取独立的市场方向话题。两种结论不得互相推导。

## 一个区间一个话题

没有向量聚类，话题的成员关系只有一个确定性依据：「有市场方向」。所以一个产品 × 区间
只有**一个**话题（市场方向讨论），模型给它起名、写一句摘要（`synthesis_outputs`
kind=`topic_label`，subkey=`market`）。要分成多个话题得先有聚类 —— 那是 P1。
设计源每个区间给三个话题；这里给一个真的，不给三个编的。
"""

from .attitude import LOW_SAMPLE
from .delta import delta

DIRECTION_TO_TRICOLOR = {"bullish": "positive", "bearish": "negative", "neutral": "neutral"}
DEFAULT_TITLE = "市场方向与指数走势讨论"
MARKET_SUBKEY = "market"
# Read-side contract for the P6 market-opinion prose.  The backend deliberately
# owns this string instead of importing worker code; an older generation may be
# structurally valid JSON while still carrying the superseded, generic prompt.
MARKET_OUTLOOK_SYNTH_VERSION = "synth-v8"

MARKET_SENTIMENT_LABELS = {
    "bullish": "看多",
    "neutral_bullish": "中性偏看多",
    "neutral": "中性",
    "neutral_bearish": "中性偏看空",
    "bearish": "看空",
}

PRODUCT_ATTITUDE_LABELS = {
    "positive": "积极偏多",
    "neutral_positive": "中性偏积极",
    "neutral": "中性",
    "neutral_negative": "中性偏消极",
    "negative": "消极偏多",
}


def _unknown_product_opinion(status="unavailable"):
    return {
        "attitude": None,
        "attitudeLabel": None,
        "attitudeStatus": status,
    }


def classify_product_opinion(positive, negative, neutral=0):
    """Classify product attitude without mixing in market direction.

    Per PRD §3.4, neutral comments are reported separately and do not dilute
    the ``positive + negative`` denominator.  Unknown counts and a genuinely
    low sample remain distinct states instead of being manufactured as neutral.
    """
    if positive is None or negative is None:
        return _unknown_product_opinion()
    analysed_total = positive + negative + int(neutral or 0)
    if analysed_total == 0:
        return _unknown_product_opinion()
    total = positive + negative
    if total < LOW_SAMPLE:
        return _unknown_product_opinion("low_sample")
    margin = positive - negative
    if margin > 0:
        attitude = "positive" if margin * 4 >= total else "neutral_positive"
    elif margin < 0:
        attitude = "negative" if -margin * 4 >= total else "neutral_negative"
    else:
        attitude = "neutral"
    return {
        "attitude": attitude,
        "attitudeLabel": PRODUCT_ATTITUDE_LABELS[attitude],
        "attitudeStatus": "ok",
    }


def product_attitude_counts(units):
    """Count known product-attitude units while preserving unavailable data."""
    if units is None:
        return None
    counts = {"positive": 0, "negative": 0, "neutral": 0}
    for unit in units:
        attitude = unit.get("attitude")
        if attitude in counts:
            counts[attitude] += 1
    return counts


def classify_market_sentiment(positive, negative, neutral):
    """Map market-direction counts to one stable five-level display label.

    ``None`` remains unknown.  A real zero total means that no direction was
    identified and therefore has no sentiment conclusion.  For a non-empty
    sample, a net margin of at least 25% of all directional and neutral rows is
    a clear view; a smaller non-zero margin is deliberately labelled as a lean.
    Keeping this boundary here prevents screens and providers from inventing
    slightly different meanings for ``中性偏看多／看空``.
    """
    values = (positive, negative, neutral)
    if any(value is None for value in values):
        return {"sentiment": None, "sentimentLabel": None}
    total = sum(values)
    if total <= 0:
        return {"sentiment": None, "sentimentLabel": None}
    margin = positive - negative
    if margin > 0:
        sentiment = "bullish" if margin * 4 >= total else "neutral_bullish"
    elif margin < 0:
        sentiment = "bearish" if -margin * 4 >= total else "neutral_bearish"
    else:
        sentiment = "neutral"
    return {"sentiment": sentiment, "sentimentLabel": MARKET_SENTIMENT_LABELS[sentiment]}


def market_discussion_count(topics):
    """Return a sortable topic count while preserving unknown versus empty."""
    if topics is None:
        return None
    return sum(int(topic.get("mentions") or 0) for topic in topics if isinstance(topic, dict))


def market_outlook_row(product, topics, attitude_counts):
    """Combine independent product-attitude and market-direction conclusions."""
    base = {
        "code": str(product["code"]),
        "name": product["name"],
        "status": "unavailable",
        "summary": None,
        "stale": None,
        "reviewState": None,
        "evidenceIds": [],
        **_unknown_product_opinion(),
        **classify_market_sentiment(None, None, None),
    }
    opinion = classify_product_opinion(*(
        (None, None) if attitude_counts is None else (
            attitude_counts.get("positive"), attitude_counts.get("negative"),
            attitude_counts.get("neutral"),
        )
    ))
    if topics is None:
        return {**base, **opinion}
    valid = [topic for topic in topics if isinstance(topic, dict)]
    if not valid:
        return {**base, **opinion, "status": "empty"}

    sentiment = classify_market_sentiment(*(
        sum(int(topic.get(key) or 0) for topic in valid)
        for key in ("positive", "negative", "neutral")
    ))
    primary = max(valid, key=lambda topic: (int(topic.get("mentions") or 0), str(topic.get("id") or "")))
    # SQL topics carry a deterministic fallback sentence while their AI label is
    # pending.  This table promises an AI topic summary, so do not present that
    # fallback as generated prose.  Frozen demo rows predate ``labelStatus`` and
    # are complete by construction.
    label_status = primary.get("labelStatus")
    stale = bool(primary.get("stale"))
    # Freshness is an operational signal, not an availability gate.  The
    # currently published generation stays visible while the dirty/older
    # input is being recomputed (ADR-0033); unsupported prompt versions are
    # still rejected independently through ``labelStatus``.
    summary = primary.get("summary") if label_status in (None, "ok") else None
    return {
        **base,
        **opinion,
        **sentiment,
        "status": "ok" if summary else "unavailable",
        "summary": summary or None,
        "stale": stale,
        "reviewState": primary.get("reviewState"),
        "evidenceIds": list(primary.get("evidenceIds") or []),
    }


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
