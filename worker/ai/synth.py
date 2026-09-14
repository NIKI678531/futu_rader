"""产品 × 区间级生成物（Layer B）的 Prompt 与 Schema —— 模型**只写字，不写数**（ADR-0020）。

## 七种生成物

| kind | 页面 | 输入（都是 `backend/core/` 算好的事实＋带 id 的证据引文） | 输出 |
|---|---|---|---|
| `hot_summary` | 板块总览榜单 | 正负中计数、主题桶计数、证据 | ≤30 字「现象＋主流观点」 |
| `summary` | P7／S10 当前舆情总结 | 同上＋环比 | 2–4 条要点，每条 ≤60 字＋evidence_ids |
| `theme_label` | P9／S10 观点主题 | 每个极性×aspect 桶的计数与证据 | 每桶标题 ≤12 字＋摘要 ≤40 字 |
| `neg_category` | S10 负面舆情摘要／P9 消极卡 | 每个负面 aspect 桶 | 每桶类别名 ≤10 字＋摘要 ≤40 字 |
| `stage_unit` | P12 阶段观点 | 每个足量时段的计数与证据 | 7 类之一＋≤40 字摘要 |
| `stage_summary` | P12 阶段观点 | 合并后的阶段（多时段） | ≤40 字总结 |
| `topic_label` | P13 产品话题情绪 | 市场方向三色计数与证据 | 标题 ≤16 字＋摘要 ≤40 字 |
| `competitor_reason` | P14 关联竞品 | 竞品在本产品评论区的共现证据 | 喜欢／质疑原因各 ≤3 条 |

## 三条硬约束（schema＋validator 双保险）

1. **不许出现比例或占比**：文字里不得有 `%`／`％`／「占比」「比例」。数由前端从 core 的
   字段渲染；模型写「六成用户」就是在替后端算数（铁律 1）。
2. **evidence_ids ⊆ 输入 id**：模型引用了一条不存在的证据 ⇒ `SchemaError`，整批重试。
   由 `check_evidence_ids()` 在 parse 之后做，strict schema 表达不了「子集」。
3. **字数上限**：strict 模式不支持 `maxLength`，靠 validator。超了判失败重试，不截断 ——
   半句摘要在界面上看不出是被截的。

## 语言

`language` 由调用方给（该产品区间内证据的主流文字：简／繁／粤），PRD O3 未决前先随原文。
"""

import json
import re
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from .schemas import SchemaError, _strictify

VERSION = "synth-v1"

KINDS = ("hot_summary", "summary", "theme_label", "neg_category", "stage_unit",
         "stage_summary", "topic_label", "competitor_reason")

STAGE_CATEGORIES = ("add_opportunity", "pullback_done", "wait", "divergence", "event", "reduce", "product_issue")

_RATIO = re.compile(r"[%％]|占比|比例|成用户|成的用户|成以上|过半|大多数人|所有人|一致认为")


def _no_ratio(text, where):
    if text and _RATIO.search(text):
        raise ValueError(f"{where}: 出现了比例／占比类表述（{_RATIO.search(text).group()!r}），数由后端算，不许模型写")


def _max_len(text, n, where):
    if text is not None and len(text) > n:
        raise ValueError(f"{where}: {len(text)} 字，超过 {n} 字上限")


# ── 模型 ────────────────────────────────────────────────────────────────


class HotSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    evidence_ids: list[str]
    needs_review: bool

    @model_validator(mode="after")
    def _rules(self):
        _max_len(self.text, 30, "hot_summary.text")
        _no_ratio(self.text, "hot_summary.text")
        if not self.evidence_ids:
            raise ValueError("hot_summary: 至少引用一条证据")
        return self


class SummaryPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    evidence_ids: list[str]

    @model_validator(mode="after")
    def _rules(self):
        _max_len(self.text, 60, "summary.point")
        _no_ratio(self.text, "summary.point")
        return self


class Summary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    points: list[SummaryPoint]
    needs_review: bool

    @model_validator(mode="after")
    def _rules(self):
        if not 1 <= len(self.points) <= 4:
            raise ValueError(f"summary: 要点 {len(self.points)} 条，应为 1–4 条")
        return self


class BucketLabel(BaseModel):
    """一个桶（极性×aspect、负面 aspect）的名字与一句摘要。"""
    model_config = ConfigDict(extra="forbid")
    key: str
    title: str
    summary: str
    evidence_ids: list[str]

    @model_validator(mode="after")
    def _rules(self):
        _max_len(self.title, 12, f"{self.key}.title")
        _max_len(self.summary, 40, f"{self.key}.summary")
        _no_ratio(self.title, f"{self.key}.title")
        _no_ratio(self.summary, f"{self.key}.summary")
        return self


class BucketLabelBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[BucketLabel]


class StageUnit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    category: Literal[STAGE_CATEGORIES]  # type: ignore[valid-type]
    digest: str
    evidence_ids: list[str]
    needs_review: bool

    @model_validator(mode="after")
    def _rules(self):
        _max_len(self.digest, 40, f"{self.key}.digest")
        _no_ratio(self.digest, f"{self.key}.digest")
        return self


class StageUnitBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[StageUnit]


class StageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    summary: str
    evidence_ids: list[str]

    @model_validator(mode="after")
    def _rules(self):
        _max_len(self.summary, 40, f"{self.key}.summary")
        _no_ratio(self.summary, f"{self.key}.summary")
        return self


class StageSummaryBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[StageSummary]


class TopicLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    summary: str
    evidence_ids: list[str]

    @model_validator(mode="after")
    def _rules(self):
        _max_len(self.title, 16, "topic.title")
        _max_len(self.summary, 40, "topic.summary")
        _no_ratio(self.title, "topic.title")
        _no_ratio(self.summary, "topic.summary")
        return self


class CompetitorReason(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    like_reasons: list[str]
    dislike_reasons: list[str]
    evidence_ids: list[str]
    needs_review: bool

    @model_validator(mode="after")
    def _rules(self):
        for group, name in ((self.like_reasons, "like"), (self.dislike_reasons, "dislike")):
            if len(group) > 3:
                raise ValueError(f"{self.code}.{name}_reasons: 最多 3 条")
            for r in group:
                _max_len(r, 40, f"{self.code}.{name}_reasons")
                _no_ratio(r, f"{self.code}.{name}_reasons")
        return self


class CompetitorReasonBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: list[CompetitorReason]


MODELS = {
    "hot_summary": HotSummary,
    "summary": Summary,
    "theme_label": BucketLabelBatch,
    "neg_category": BucketLabelBatch,
    "stage_unit": StageUnitBatch,
    "stage_summary": StageSummaryBatch,
    "topic_label": TopicLabel,
    "competitor_reason": CompetitorReasonBatch,
}


def json_schema(kind):
    import copy

    return _strictify(copy.deepcopy(MODELS[kind].model_json_schema()))


def parse(kind, raw, allowed_ids, expected_keys=None):
    """校验一次生成输出；`allowed_ids` 是输入里全部证据 id；`expected_keys` 是批式 kind 的桶键集合。"""
    try:
        obj = MODELS[kind].model_validate(raw)
    except ValidationError as exc:
        raise SchemaError(f"{kind} 输出不合 schema: {exc}") from exc
    check_evidence_ids(kind, obj, set(allowed_ids))
    if expected_keys is not None:
        got = {r.key if hasattr(r, "key") else r.code for r in obj.results}
        if got != set(expected_keys):
            raise SchemaError(
                f"{kind}: 键集合不一致。缺少 {sorted(set(expected_keys) - got)}；多出 {sorted(got - set(expected_keys))}"
            )
    return obj


def check_evidence_ids(kind, obj, allowed):
    """模型引用的每个 evidence_id 都必须来自输入。引用了不存在的 id ＝ 编了证据。"""
    def _check(ids, where):
        bad = [i for i in ids if i not in allowed]
        if bad:
            raise SchemaError(f"{kind} {where}: 引用了输入里没有的证据 id {bad[:3]}")

    if hasattr(obj, "results"):
        for r in obj.results:
            _check(r.evidence_ids, getattr(r, "key", None) or getattr(r, "code", ""))
    elif hasattr(obj, "points"):
        for p in obj.points:
            _check(p.evidence_ids, "point")
    else:
        _check(obj.evidence_ids, "")


# ── Prompt ──────────────────────────────────────────────────────────────

_COMMON = """你是富途牛牛 ETF 讨论区舆情工作台的**撰稿员**，不是分析员：数已经由系统算好并放在 `facts` 里，你只负责把讨论区的观点写成人能读的句子。

硬规则：
1. **只写观点，不写数**。不得出现百分比、占比、比例、「六成」「过半」「大多数人」「所有人一致」这类表述。要表达多寡只能用「多条评论」「个别评论」这样的词。
2. **每句话都要能回到原文**：`evidence` 里每条引文带 `id`，你写的每条结论都要在 `evidence_ids` 里列出支撑它的引文 id，且只能用给出的 id。
3. **不判断真伪、不预测涨跌、不给投资建议、不表述与价格的因果关系**。写「讨论区认为……」而不是「……将会……」。
4. **随原文语言**：`language` 是这批引文的主流文字（zh-Hans 简体／zh-Hant 繁体／yue 粤语），用同一种写。
5. 拿不准就 `needs_review=true`（有该字段的 kind）。
6. 不要输出 JSON 之外的任何文字。"""

SYSTEMS = {
    "hot_summary": _COMMON + """

# 任务：热议总结（板块总览榜单的一句话）

写一句 **不超过 30 字** 的「现象＋主流观点」，例如「分派到账稳定获认可，倾向继续持有收息」。
- 必须是**观点**，不是正负面判断（不要写「整体偏正面」）。
- 取 `facts.themes` 里提及最多的一两个桶的观点，引用其证据。""",

    "summary": _COMMON + """

# 任务：当前舆情总结（产品监控页要点列表）

写 **1–4 条要点**，每条 **不超过 60 字**，按重要性排序：
- 第一条概括讨论最集中的方面（看 `facts.themes` 与 `facts.negCategories` 谁的提及多）。
- 积极与消极若都有，各给一条；只有一方就只写一方。
- 有 `facts.compliance` 命中时，最后一条提示「有评论涉及……（需合规关注）」，不判真伪。
- 每条列出支撑它的 `evidence_ids`。""",

    "theme_label": _COMMON + """

# 任务：给观点主题起名

`facts.buckets` 里每个桶是「一种极性 × 一个方面」的评论集合，带计数与证据。对**每个桶**输出：
- `key`：原样抄回。
- `title`：**不超过 12 字**的具体观点名，如「费率同类最低」「点差过大影响短线」。不要只重复方面名（不要写「费率」）。
- `summary`：**不超过 40 字**，概括这个桶里评论在说什么。
- `evidence_ids`：支撑标题的引文。
桶一个都不能少、不能多。""",

    "neg_category": _COMMON + """

# 任务：给负面舆情类别起名

`facts.buckets` 是消极评论里「可归类、可行动的产品问题」桶（费率／流动性／点差／跟踪／分红／损耗／其他）。对每个桶输出：
- `key`：原样抄回。
- `title`：**不超过 10 字**的问题名，如「跟踪偏差扩大」「派息不及预期」。
- `summary`：**不超过 40 字**，说清用户在质疑什么（不写建议）。
- `evidence_ids`。""",

    "stage_unit": _COMMON + """

# 任务：判定每个时段的主流观点分类并写一句摘要

`facts.units` 里每个时段带正／负／中计数（`tone` 是系统按计数算好的情绪）与该时段的证据。对每个时段输出：
- `key`：原样抄回。
- `category`：7 类之一——`add_opportunity` 加仓机会 | `pullback_done` 回撤到位 | `wait` 观望等待 | `divergence` 分歧加大 | `event` 事件驱动 | `reduce` 减仓离场 | `product_issue` 产品问题。
  - `tone=positive` 时通常在 加仓机会／回撤到位／事件驱动 里选；`negative` 在 减仓离场／产品问题；`neutral` 在 观望等待／分歧加大／事件驱动。与情绪明显不符时选你认为对的，并 `needs_review=true`。
- `digest`：**不超过 40 字**，描述该时段讨论区的主流观点，不表述与价格的因果。
- `evidence_ids`。""",

    "stage_summary": _COMMON + """

# 任务：给合并后的阶段写一句总结

`facts.stages` 里每个阶段由若干连续时段合并而成（同一观点分类），带各时段摘要与证据。对每个阶段输出 `key` 原样抄回、**不超过 40 字**的 `summary`、`evidence_ids`。总结描述这一段时间讨论区的观点走向，不表述与价格的因果关系。""",

    "topic_label": _COMMON + """

# 任务：给「产品话题情绪」起名

`facts.topic` 是区间内谈市场方向、指数涨跌、宏观事件而**没有评价产品本身**的评论集合，带看多／看空／无方向计数与证据。输出：
- `title`：**不超过 16 字**，点出讨论围绕的具体话题（如「恒科反弹持续性」「美联储议息预期」）。
- `summary`：**不超过 40 字**，概括多空各自在说什么。
- `evidence_ids`。""",

    "competitor_reason": _COMMON + """

# 任务：关联竞品——用户为什么喜欢／质疑它

`facts.competitors` 里每只竞品带它在**本产品**讨论区里被提到的证据。对每只输出：
- `code`：原样抄回。
- `like_reasons`：用户喜欢或选择它的原因，**最多 3 条**，每条 **不超过 40 字**；没有就空数组。
- `dislike_reasons`：用户质疑或放弃它的原因，同上。
- `evidence_ids`。
- 证据不足以说出任何原因时两个数组都空，`needs_review=true`。""",
}


def system_prompt(kind):
    return SYSTEMS[kind]


def user_message(kind, payload):
    """`payload` 由 `jobs/synthesize.py` 组装：`{product, range, language, facts, evidence}`。
    `evidence` 已经过 `redact.scrub_text`；这里不再拼任何字段。"""
    return (
        f"请按系统提示完成「{kind}」，只输出 JSON：\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=1)
    )
