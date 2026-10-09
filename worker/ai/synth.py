"""产品 × 区间级生成物（Layer B）的 Prompt 与 Schema —— 模型**只写字，不写数**（ADR-0020）。

## 七种生成物

| kind | 页面 | 输入（都是 `backend/core/` 算好的事实＋带 id 的证据引文） | 输出 |
|---|---|---|---|
| `hot_summary` | 板块总览榜单 | 正负中计数、主题桶计数、证据 | ≤30 字「现象＋主流观点」 |
| `summary` | P7／S10 当前舆情总结 | 同上＋环比 | 最多 5 条具体要点，每条 ≤100 字＋evidence_ids |
| `theme_label` | P9／S10 观点主题 | 每个极性×aspect 桶的计数与证据 | 具体标题＋详细摘要＋1–3 条理由 |
| `neg_category` | S10 负面舆情摘要／P9 消极卡 | 每个负面 aspect 桶 | 具体问题名＋详细摘要＋1–3 条理由 |
| `stage_unit` | P12 阶段观点 | 每个足量时段的计数与证据 | 7 类之一＋≤40 字摘要 |
| `stage_summary` | P12 阶段观点 | 合并后的阶段（多时段） | ≤40 字总结 |
| `topic_label` | P13／市场风向表 | 市场方向、产品态度计数与两类证据 | 标题 ≤16 字＋行情摘要 ≤180 字 |
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

VERSION = "synth-v8"

KINDS = ("hot_summary", "summary", "theme_label", "neg_category", "stage_unit",
         "stage_summary", "topic_label", "competitor_reason")

STAGE_CATEGORIES = ("add_opportunity", "pullback_done", "wait", "divergence", "event", "reduce", "product_issue")

_RATIO = re.compile(r"[%％]|占比|比例|成用户|成的用户|成以上|过半|大多数人|所有人|一致认为")
_TOPIC_GENERIC_WORDS = (
    "市场关注度", "整体情绪", "短线观点", "方向确认", "风险偏好", "不确定性",
    "市場關注度", "整體情緒", "短線觀點", "方向確認", "風險偏好", "不確定性",
    "另一部分", "有所", "继续", "可能", "主要", "普遍", "整体", "部分", "评论",
    "讨论", "投资者", "市场", "行情", "后市", "短线", "走势", "方向", "观点", "情绪",
    "指数", "板块", "关注度", "预期", "认为", "关注", "呈现", "保持", "维持", "等待",
    "确认", "仍然", "存在", "回升", "上涨", "下跌", "回落", "反弹", "震荡", "偏多",
    "偏空", "看多", "看空", "乐观", "谨慎", "观望", "不确定", "分歧", "升温", "转弱",
    "转强", "多空", "为主", "偏向", "较为", "的", "了", "与", "和", "但", "而", "对",
    "中", "有", "仍", "较", "偏", "是",
    "繼續", "可能", "主要", "普遍", "整體", "部分", "評論", "討論", "投資者", "市場",
    "行情", "後市", "短線", "走勢", "方向", "觀點", "情緒", "指數", "板塊", "關注度",
    "預期", "認為", "關注", "呈現", "保持", "維持", "等待", "確認", "仍然", "存在",
    "回升", "上漲", "下跌", "回落", "反彈", "震盪", "偏多", "偏空", "看多", "看空",
    "樂觀", "謹慎", "觀望", "不確定", "分歧", "升溫", "轉弱", "轉強", "多空", "為主",
    "偏向", "較為", "與", "對", "仍", "較", "是",
)


def _no_ratio(text, where):
    if text and _RATIO.search(text):
        raise ValueError(f"{where}: 出现了比例／占比类表述（{_RATIO.search(text).group()!r}），数由后端算，不许模型写")


def _max_len(text, n, where):
    if text is not None and len(text) > n:
        raise ValueError(f"{where}: {len(text)} 字，超过 {n} 字上限")


def _topic_sentence_is_generic(text):
    """True when a sentence contains only market-direction boilerplate."""
    remainder = re.sub(r"[\s，,；;：:。！？!?、（）()《》「」『』]", "", text)
    for word in sorted(_TOPIC_GENERIC_WORDS, key=len, reverse=True):
        remainder = remainder.replace(word, "")
    return not remainder


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
        _max_len(self.text, 100, "summary.point")
        _no_ratio(self.text, "summary.point")
        if not self.evidence_ids:
            raise ValueError("summary.point: 至少引用一条证据")
        return self


class ThemeKeyPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    evidence_ids: list[str]

    @model_validator(mode="after")
    def _rules(self):
        _max_len(self.text, 72, "theme.key_point")
        _no_ratio(self.text, "theme.key_point")
        if not self.evidence_ids:
            raise ValueError("theme.key_point: 至少引用一条证据")
        return self


class Summary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    points: list[SummaryPoint]
    needs_review: bool

    @model_validator(mode="after")
    def _rules(self):
        if not 1 <= len(self.points) <= 5:
            raise ValueError(f"summary: 要点 {len(self.points)} 条，应为 1–5 条")
        return self


class BucketLabel(BaseModel):
    """一个桶（极性×aspect、负面 aspect）的名字与一句摘要。"""
    model_config = ConfigDict(extra="forbid")
    key: str
    title: str
    summary: str
    key_points: list[ThemeKeyPoint]
    evidence_ids: list[str]

    @model_validator(mode="after")
    def _rules(self):
        _max_len(self.title, 20, f"{self.key}.title")
        _max_len(self.summary, 120, f"{self.key}.summary")
        if not 1 <= len(self.key_points) <= 3:
            raise ValueError(f"{self.key}: key_points 应为 1–3 条")
        generic_titles = {
            "产品整体体验获认可", "产品整体体验受认可", "产品整体体验受质疑",
            "其他产品体验获认可", "其他产品体验受认可", "其他产品体验受质疑",
        }
        if self.title.strip() in generic_titles:
            raise ValueError(f"{self.key}.title: 必须指出具体产品体验，不能使用泛化标题")
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
        _max_len(self.summary, 180, "topic.summary")
        _no_ratio(self.title, "topic.title")
        _no_ratio(self.summary, "topic.summary")
        if not self.evidence_ids:
            raise ValueError("topic_label: 至少引用一条证据")
        sentences = [part.strip() for part in re.split(r"[。！？!?]+", self.summary) if part.strip()]
        if not 2 <= len(sentences) <= 4:
            raise ValueError(f"topic.summary: 应写 2–4 句，当前为 {len(sentences)} 句")
        generic = [sentence for sentence in sentences if _topic_sentence_is_generic(sentence)]
        if generic:
            raise ValueError(f"topic.summary: 存在无具体证据锚点的泛化套话 {generic[0]!r}")
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
            for point in getattr(r, "key_points", []):
                _check(point.evidence_ids, f"{getattr(r, 'key', '')}.key_point")
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
3. **不判断真伪、不预测涨跌、不给投资建议、不自行推断因果**。只有证据明确表达原因或影响时，才可写「评论将……归因于……／认为……影响……」来转述；不得把同时发生或先后出现自行改写成因果。
4. **随原文语言**：`language` 是这批引文的主流文字（zh-Hans 简体／zh-Hant 繁体／yue 粤语），用同一种写。
5. 拿不准就 `needs_review=true`（有该字段的 kind）。
6. 不要输出 JSON 之外的任何文字。"""

SYSTEMS = {
    "hot_summary": _COMMON + """

# 任务：热议总结（板块总览榜单的一句话）

写一句 **不超过 30 字** 的「现象＋主流观点」，例如「分派到账稳定获认可，倾向继续持有收息」。
- 必须是**观点**，不是正负面判断（不要写「整体偏正面」）。
- 取 `facts.themes` 里提及最多的一两个桶的观点，引用其证据。
- 当 `facts.sampleSufficient=false` 时，不得写「主流」「多数」「趋势」；只客观概括一条已有评论，并用「有评论提到……」开头。""",

    "summary": _COMMON + """

# 任务：当前舆情总结（产品监控页要点列表）

写 **3–5 条要点**，页面最多只展示 5 条；每条 **不超过 100 字**，按重要性排序：
- 按「具体产品体验／使用场景／讨论现象」组织要点，不得按「积极观点／消极观点」分组，也不要用这两个词作为要点开头；同一体验同时有认可和质疑时，应在同一条里写清分歧。
- 每条都要同时写清：评价的是哪项具体产品体验、评论提到的现象或使用场景、用户因此怎样评价产品。不要只写「整体获认可／整体受质疑」「体验较好／体验不佳」等泛化结论。
- 第一条概括讨论最集中的具体问题，并写清「什么现象／原因 → 用户因此怎样评价产品」。
- 再补次要原因、不同使用场景或观点变化；优先写费率、点差、成交深度、跟踪误差／折溢价、派息、杠杆损耗、产品结构、披露、服务或交易渠道等可核验细节，避免只复述正负数量。
- 有 `facts.compliance` 命中时，最后一条提示「有评论涉及……（需合规关注）」，不判真伪。
- 每条列出支撑它的 `evidence_ids`。""",

    "theme_label": _COMMON + """

# 任务：给观点主题起名

`facts.buckets` 里每个桶是「一种极性 × 一个方面」的评论集合，带计数与证据。对**每个桶**输出：
- `key`：原样抄回。
- `title`：**不超过 20 字**的具体结论，写成「具体产品属性／使用场景＋用户感受或结果」，如「溢价收窄改善入场体验」「港元柜台成交深度不足」。严禁只重复方面名，也严禁「产品整体体验获认可／受质疑」「其他产品体验获认可／受质疑」等泛化标题。
- `summary`：**不超过 120 字**，至少点出一个证据中出现的具体对象、现象或场景，并说明它为何令用户肯定／质疑这只产品；证据没有因果时只并列写现象与评价，不补造因果。
- `key_points`：1–3 条关键理由，每条含不超过 72 字的 `text` 和自己的 `evidence_ids`；每条应保留区别于其他话题的具体细节，不能换一种说法重复标题。
- `evidence_ids`：支撑标题的引文。
桶一个都不能少、不能多。""",

    "neg_category": _COMMON + """

# 任务：给负面舆情类别起名

`facts.buckets` 是消极评论里「可归类、可行动的产品问题」桶（费率／流动性／点差／跟踪／分红／损耗／其他）。对每个桶输出：
- `key`：原样抄回。
- `title`：**不超过 20 字**的具体问题名，如「跟踪偏差扩大」「派息到账慢于预期」。不得使用「整体体验受质疑」等泛化标题。
- `summary`：**不超过 120 字**，点出具体产品属性、使用场景或异常现象，说清用户在质疑什么（不写建议）。
- `key_points`：1–3 条具体理由，每条不超过 72 字并带自己的 `evidence_ids`。
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

# 任务：生成产品评论中的市场观点摘要

`facts.topic` 是区间内的市场方向计数；`facts.productAttitude` 是当前产品的积极／消极／中性态度计数，二者语义独立。摘要的主体必须是评论对该 ETF 所对应市场、指数、行业及成分股行情的看法，不是抽象的情绪标签。`evidence` 中：
- `market_direction` 是该评论的方向标注；`direction_quote` 是支持方向标注的短引文；`text` 是同一条评论的脱敏上下文，用来保留公司、指数、行业、财报、供需和催化剂等细节。
- `product_attitude` 只用于在两类结论相反时说明产品态度，不得把费率、点差、跟踪误差等产品体验写入市场观点摘要。

输出：
- `title`：**不超过 16 字**，点出讨论围绕的具体话题（如「恒科反弹持续性」「美联储议息预期」）。
- `summary`：写成 **2–4 句、不超过 180 字**；有至少三条具体证据时目标 **130–180 字**，证据不足宁可更短，禁止凑字或补造。尽量按「具体市场对象与行情 → 驱动或制约因素 → 对相关行业／成分股的影响链路 → 短线取向与分歧」组织。
- 每句至少保留一个证据原文中的具体锚点：公司／指数／行业名、财报或业绩指标、供需／库存／产能、回购、估值、政策／宏观数据、资金或技术位、催化剂／风险之一。优先沿用原文中的专有名词；专名、数字和因果链都必须在所引 `evidence_ids` 对应的 `text` 中出现，不能凭产品名称或常识补全。
- 禁止用「市场关注度升温」「整体情绪偏乐观／谨慎」「后市仍有不确定性」「多空存在分歧」等套话单独成句；必须紧接证据里的具体对象与原因。不要逐条复述评论，要合并同一事件，并保留不同评论的关键分歧。
- 结构示例（**仅示范详细程度，示例名词不是默认事实**）：若且仅若证据原文确有「美光财报超预期、存储缺货、回购、景气修复、海力士补涨」等信息，可写成「评论围绕美光财报超预期、存储缺货与回购预期展开，并把这些催化与存储景气修复、海力士补涨联系起来……」；其他 ETF 必须换成各自证据中的对象和逻辑。
- 只有 `facts.productAttitude.sampleSufficient=true` 时，才可归纳整体产品态度；若此时市场方向与产品态度相反，必须明确写成「市场方向讨论……，但产品态度……」两层结论，不得把市场方向冒充产品舆情。只能写「多条评论押注……」等证据强度，不得擅自升级成全市场共识。
- `facts.productAttitude.sampleSufficient=false` 时，产品态度样本不足：不得写「整体积极／消极」「想买／不想买」或比较两类结论，只总结带 `market_direction` 的行情证据。
- 可以转述「评论预期上涨／回落」「讨论认为……」，但不能把群体观点写成系统自己的预测，也不能给出买卖建议。
- 这里只总结市场、指数、行业或成分股方向，不评价 ETF 的费率、点差、跟踪误差等产品体验。
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
