"""市场域叙述组口径（PRD §5.5／§5.7／§5.8／§5.10／§5.11 与热议总结）。

基础组回答「这只产品在这段时间里有多少条讨论」，叙述组回答「讨论在说什么」。
六个函数：舆情总结、热议总结、正负主题、负面舆情类别、关联竞品、需合规关注。

## 这里的 status 是六态，不是 HTTP 码

`competitors` / `compliance` / `hot_summary` 都带一个 `status`，取值是 PRD §3.6 的接口
枚举（ok / empty / unavailable / low_sample / na）。它描述的是**这个字段怎么了**，与
响应本身是不是 200 无关 —— 数据缺失永远是 200：

- `na`         字段不适用。同业产品不纳入需合规关注识别，不是「没查到」。
- `unavailable` 该有值但取不到。风险识别没覆盖到这只产品、KOL 身份映射没接上。
- `empty`      查过了，确实没有。
- `low_sample` 有效态度样本低于阈值，按 PRD §3.5 不输出倾向结论。

四者在页面上是四句不同的话，合并任意两个都会让人读出一个我们没得出的结论。
所以后端下发的是**状态**，不是空数组 —— 空数组只能表达 `empty` 一种。

## 空列表不等于没有意义

`themes` / `neg_cats` 返回数组，空数组的语义是「区间内没有可归类的主题」，页面渲染
「暂无内容」。绝不因为空就在别处填 0（铁律 2）。

## 为什么 themes 一次给两个极性

设计源签名是 `themesFor(code, range, polarity)`，但两处调用点都是正负各取一次。同步
Suspense 下一次 read() 未命中就是一次串行往返，分成两个端点等于白挨一次。端点给
`{positive, negative}`，门面保留三参签名按极性取用（ADR-0005、ADR-0003）。

## 为什么 hot_summary 按区间整池下发

板块总览榜单每一行都调一次 `hotSummaryFor`。按产品切端点，一屏 120 行就是 120 次
串行往返；整池一份不到 100 KB。这是取集合里的元素，不是聚合（同 observe，≠ ADR-0015）。
"""

from providers import get_provider
from providers.demo import MISSING

from .ranges import VALID_KEYS


def hot_summaries(range_key):
    """整池的热议总结：`{code: {status, text, sample, tone?, ok}}`。

    `ok` 为 False 时 `text` 已经是该状态对应的文案（「数据暂不可用」「暂无相关内容」
    「样本不足，暂无主流观点」），前端直接渲染，不在屏里按 status 拼一遍文案 ——
    那就是把口径实现成了两份（铁律 1）。
    """
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().hot_summaries(range_key)


def summary_for(code, range_key):
    """当前舆情总结：`{text, sample, low}`。

    `low` 为 True 时正文里已经写明「低于 N 条的判定阈值，本区间不输出整体倾向结论」。
    阈值来自 /meta 的 thresholds.lowSample，同一个数只有一处定义。
    """
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().summary_for(code, range_key)


def themes_for(code, range_key):
    """正负主题：`{positive: [...], negative: [...]}`，各自按提及数降序。

    每个主题带 `delta`（内嵌环比形状）、`buckets`（逐桶提及数）与 `evidenceCount`。
    """
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().themes_for(code, range_key)


def neg_cats_for(code, range_key):
    """负面舆情类别：可归类、可行动的那部分，**不等于全部消极观点**。

    `shareOfNegative` 的分母是消极总数，因此各类别占比之和小于 100% —— 这是口径本身
    的形状，不是漏算。
    """
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().neg_cats_for(code, range_key)


def competitors_for(code, range_key):
    """关联竞品：`{status, list}`，双向（自家 → 竞品、竞品 → 自家）。

    每行的 `relation` 是 `confirmed`（客户维护的固定对位映射）或 `auto_candidate`
    （AI 依据同板块同结构识别，页面上必须显示「待确认」）。把 auto_candidate 当成
    confirmed 显示，等于替客户确认了一段他们没确认过的对位关系。
    `status: 'unavailable'` 是传播关系数据本身取不到，与「没有对位竞品」（empty）不同。
    """
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().competitors_for(code, range_key)


def compliance_for(code, range_key):
    """需合规关注：`{status, list}`。AI 只识别信号并保留原文与命中依据。

    不判定言论真伪、不判定产品是否违规，每条的 `reviewState` 恒为 `ai_pending`
    （「AI 识别 · 待人工确认」，PRD §4.1／§4.2 逐字）。
    `na` = 同业产品不纳入识别；`unavailable` = 这只产品的风险识别没接上。
    """
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().compliance_for(code, range_key)
