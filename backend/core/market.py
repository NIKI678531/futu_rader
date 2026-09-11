"""市场域基础组口径（PRD §5 `pool(range)`、`ranks(range)`、`benchmark(code, range)`）。

## 铁律 3 落在这里

「全市场排名由后端算好，前端只过滤显示。」PRD §4.1 的筛选总原则逐字：**板块、范围、
搜索与开关只改变可见范围；排名与色阶标尺始终来自全市场。**

具体是三件事，缺一条这条铁律就漏了：

1. **排名** —— `ranks(range).map` 基于完整活跃 ETF 池计算，板块筛选不重算。同一只产品
   在任何筛选组合下名次都必须是同一个数字，否则「第 12 名」就不是一个能对外引用的事实。
2. **色阶标尺** —— 热力图的归一化分母 `pool(range).globalMax` 同样来自全市场；按可见集
   重算的话，筛掉头部产品会让剩下的格子集体变深，看的人会以为讨论量涨了。
3. **可见集** —— 前端拿到的是整池（`pool(range).list`），筛选是它自己的事。后端不认识
   「当前筛了哪个板块」，也不需要认识。

## observe(code, range) 为什么没有端点

它的返回就是 `pool(range).list` 里那一个元素（设计源里连对象身份都相同）。开一个
`/observe/{code}` 端点，产品监控页那句「按当前筛选列出候选产品」就会退化成 120 次往返；
而池本来就是整份下发的，元素已经在手上了。这不是「视图内聚合」（ADR-0015 讲的是必须随
前端筛选重算的东西），是**取集合里的一个元素** —— 没有第二份口径，也没有第二次计算。

## delta 不是端点，是形状

环比 `{text, short, abs, pct, dir}` 内嵌在它所描述的那个数旁边（`benchmark` 的每个字段、
`pool.own.dHeat/dNeg/dPos`）。输入任一侧为 null 时整体是「数据暂不可用」态：
`abs`/`pct` 为 null、`dir` 为 0、文案由后端给（PRD §3.1、§3.6 两套文案不可互换）。

注意 `pct is None` **不等于**不可用：`delta(5, 0)` 是「新增 5」，基数为零算不出百分比，
但增量是确切的 5。判不可用要看 `abs is None`。
"""

from providers import get_provider
from providers.sentinel import MISSING

from .ranges import VALID_KEYS


def pool(range_key):
    """完整活跃 ETF 池在该区间的观测与聚合。

    一次整份下发（d2 约 1.1 MB），不按筛选组合切端点：板块、范围、搜索、开关是同一份
    池上的子集运算，按组合切会变成组合爆炸（ADR-0003 端点粒度），而且每切一次都要
    重新回答一遍「排名要不要跟着变」——答案永远是不变，那就别给它变的机会。
    """
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().pool(range_key)


def ranks(range_key):
    """全市场评论量排名：`{map: {code: 名次}, total: 池内产品数}`。

    只有 map 和 total，没有产品列表——列表是 `pool()` 的事。设计源的 `ranks().list` 与
    `pool().list` 是同一批对象，两处都发等于同一份 120 只产品的观测在线上跑两遍。

    这是**评论量**排名。KOL 声量排名是另一套指标，不受本条约束（PRD §4.3 M5）。
    """
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().ranks(range_key)


def benchmark(code, range_key):
    """当期 vs 基准期的环比。每个字段是一个内嵌 delta，`base` 是基准期的完整观测。

    `buckets` 与 `base.buckets` 逐桶对齐，每桶给出五条序列（评论数／活跃账号数／互动数／
    积极／消极）的 delta。趋势图悬停要用，而悬停不可能每次打一趟接口。
    """
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().benchmark(code, range_key)
