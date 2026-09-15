"""观点主题与负面舆情类别的**计数**口径（PRD §4.2 P9、§4.1 S10、§3.4 三层负面）。

叶子模块：不 import provider，只吃已经取出来的判定单元。

## 主题 ＝ 极性 × aspect 桶（ADR-0020）

主题聚类没有向量模型可用（90 天试点只用 LLM），于是主题的**成员关系**来自模型逐条给出的
`aspects` 标签：一条「费率是同类里最低的」在 positive × fee 桶里。这是确定性的 —— 同一批
标注怎么分桶都得出同一个数。模型只负责给每个桶**起名字、写一句摘要**（`synthesis_outputs`
kind=`theme_label`），不负责数数。名字没生成时这里给 aspect 的固定中文名，摘要给一句
只含计数的事实句 —— 那不是观点，是数。

一条评论有两个 aspect 就进两个桶，所以各桶 `share` 之和可以超过 100%。这是口径的形状。
`aspects=[]` 的相关评论进 `other`。

## 负面类别 ≠ 全部消极观点

PRD §3.4 第 2 层「可归类、可行动的产品问题」：`performance`（跌了骂）与 `trading_intent`
（说要卖）是情绪与动作，不是产品问题，不进负面类别。所以各类别 `shareOfNegative` 之和
小于 100% —— `core/narrative.neg_cats_for` 的 docstring 早就说了这是口径本身的形状。

## `confidence` 恒为 None

设计源给每个主题一个 62–94 的「置信度」。真库里没有校准概率（ADR-0017 §4），写任何数
都是编的。前端按缺失态渲染。
"""

from collections import defaultdict

from .delta import delta
from .lifecycle import LIFE_LABEL, SEVERITY_LABEL, lifecycle, severity

ASPECT_LABEL = {
    "fee": "费率与管理费",
    "liquidity": "流动性与成交",
    "spread": "买卖点差",
    "tracking": "跟踪表现与折溢价",
    "dividend": "分红派息",
    "leverage_decay": "杠杆／反向损耗",
    "performance": "涨跌表现",
    "trading_intent": "买卖与仓位意向",
    "other": "其他产品讨论",
}
POLARITY_LABEL = {"positive": "积极", "negative": "消极"}

# 负面类别只收「产品问题」类 aspect（见模块头）。
NEG_CATEGORY_ASPECTS = ("fee", "liquidity", "spread", "tracking", "dividend", "leverage_decay", "other")
NEG_CATEGORY_MAX = 6


def _aspects_of(unit):
    a = unit.get("aspects") or []
    return list(a) if a else ["other"]


def bucket_counts(units, nb, bucket_index):
    """`{aspect: {mentions, per_bucket[], first, last}}`，`bucket_index(unit)` 给桶号或 None。"""
    out = {}
    for u in units:
        for asp in _aspects_of(u):
            s = out.setdefault(asp, {"mentions": 0, "per_bucket": [0] * nb, "first": None, "last": None})
            s["mentions"] += 1
            bi = bucket_index(u) if nb else None
            if bi is not None and 0 <= bi < nb:
                s["per_bucket"][bi] += 1
            ts = u.get("posted_at")
            if ts is not None:
                s["first"] = ts if s["first"] is None or ts < s["first"] else s["first"]
                s["last"] = ts if s["last"] is None or ts > s["last"] else s["last"]
    return out


def themes(code, units_by_polarity, base_units_by_polarity, buckets, bucket_index, labels=None):
    """`{positive: [...], negative: [...]}`，各按提及数降序。

    - `units_by_polarity`：`{"positive": [unit], "negative": [unit]}`，unit 至少含
      `aspects`、`posted_at`。
    - `base_units_by_polarity`：基准期同形；整个为 None 表示基准期未标注 ⇒ `delta` 暂不可用。
    - `buckets`：`build_range(key)["buckets"]`。
    - `labels`：`{(polarity, aspect): {"title", "summary"}}`，来自 synthesis_outputs；可为空。
    """
    labels = labels or {}
    nb = len(buckets)
    out = {}
    for pol in ("positive", "negative"):
        units = units_by_polarity.get(pol) or []
        total = len(units)
        counts = bucket_counts(units, nb, bucket_index)
        base_counts = None
        if base_units_by_polarity is not None:
            base_counts = bucket_counts(base_units_by_polarity.get(pol) or [], 0, bucket_index)
        rows = []
        for asp, s in counts.items():
            lab = labels.get((pol, asp)) or {}
            base_n = None if base_counts is None else base_counts.get(asp, {}).get("mentions", 0)
            share = (s["mentions"] / total * 100) if total else 0
            life = lifecycle(s["mentions"], base_n)
            sev = severity(share, life)
            rows.append(
                {
                    "id": f"{code}-{pol}-{asp}",
                    "polarity": pol,
                    "aspect": asp,
                    "title": lab.get("title") or ASPECT_LABEL.get(asp, asp),
                    "summary": lab.get("summary")
                    or f"区间内 {s['mentions']} 条{POLARITY_LABEL[pol]}评论涉及{ASPECT_LABEL.get(asp, asp)}。",
                    "labelStatus": "ok" if lab.get("title") else "unavailable",
                    "mentions": s["mentions"],
                    "share": share,
                    "hasMeta": pol == "negative" and asp in NEG_CATEGORY_ASPECTS,
                    "severityLabel": SEVERITY_LABEL.get(sev),
                    "lifecycleLabel": LIFE_LABEL.get(life),
                    "firstSeenAt": s["first"].strftime("%m-%d %H:%M") if s["first"] else None,
                    "lastSeenAt": s["last"].strftime("%m-%d %H:%M") if s["last"] else None,
                    "delta": delta(s["mentions"], base_n),
                    "confidence": None,
                    "buckets": [
                        {"label": b["label"], "tip": b["tip"], "mentions": n}
                        for b, n in zip(buckets, s["per_bucket"])
                    ],
                    "evidenceCount": s["mentions"],
                    "evidenceIds": lab.get("evidence_ids") or [],
                }
            )
        rows.sort(key=lambda r: (-r["mentions"], r["aspect"]))
        out[pol] = rows
    return out


def neg_categories(code, neg_units, base_neg_units, buckets, bucket_index, labels=None, stamp=None):
    """负面舆情类别（3–6 个，按提及数降序）。`neg_units` 是全部消极判定单元。

    `stamp(ts)` 把时间戳格式化成页面要的 `MM-DD HH:00`；默认取 `%m-%d %H:00`。
    """
    labels = labels or {}
    stamp = stamp or (lambda ts: ts.strftime("%m-%d %H:00"))
    neg_total = len(neg_units)
    if neg_total == 0:
        return []
    nb = len(buckets)
    counts = bucket_counts(neg_units, nb, bucket_index)
    base_counts = None if base_neg_units is None else bucket_counts(base_neg_units, 0, bucket_index)
    rows = []
    for asp in NEG_CATEGORY_ASPECTS:
        s = counts.get(asp)
        if not s:
            continue
        lab = labels.get(asp) or {}
        base_n = None if base_counts is None else base_counts.get(asp, {}).get("mentions", 0)
        life = lifecycle(s["mentions"], base_n)
        share = s["mentions"] / neg_total * 100
        sev = severity(share, life)
        rows.append(
            {
                "id": f"{code}-nc-{asp}",
                "aspect": asp,
                "label": lab.get("title") or ASPECT_LABEL.get(asp, asp),
                "summary": lab.get("summary") or f"区间内 {s['mentions']} 条消极评论涉及{ASPECT_LABEL.get(asp, asp)}。",
                "labelStatus": "ok" if lab.get("title") else "unavailable",
                "lifecycle": life,
                "lifecycleLabel": LIFE_LABEL.get(life) if life else None,
                "mentions": s["mentions"],
                "shareOfNegative": share,
                "delta": delta(s["mentions"], base_n),
                "firstSeenAt": stamp(s["first"]) if s["first"] else None,
                "lastSeenAt": stamp(s["last"]) if s["last"] else None,
                "severity": sev,
                "severityLabel": SEVERITY_LABEL.get(sev) if sev else None,
                "evidenceCount": s["mentions"],
                "evidenceIds": lab.get("evidence_ids") or [],
            }
        )
    rows.sort(key=lambda r: (-r["mentions"], r["aspect"]))
    return rows[:NEG_CATEGORY_MAX]


def group_by_polarity(units):
    out = defaultdict(list)
    for u in units:
        if u.get("attitude") in ("positive", "negative"):
            out[u["attitude"]].append(u)
    return dict(out)
