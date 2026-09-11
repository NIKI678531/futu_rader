"""价格与阶段组口径（PRD §5：`candlesFor` / `heatSeriesFor` / `stagesFor` / `dailyFor`）。

这一组把「讨论」和「价格」画在同一张图上，因此它有两条别处没有的约束。

## 一、K 线的空桶是六态里最容易出人命的一格

`candlesFor` 的每一根 K 线是 `{bucket, open, high, low, close, note}`。缺失时四个价格
字段全是 `None`，并且 `note` 说清楚为什么缺 —— 休市日、午间休市、非交易时段、整周休市、
价格数据暂不可用。这五句不是同一件事：前四种是「这段时间市场没开，本来就没有价格」，
最后一种才是「该有价格但我们取不到」。

把 `None` 渲染成 `0`，图上会画出一根**掉到零的 K 线**：一只 68 港元的 ETF 在周末跌到 0
再弹回来。这不是「一格显示得不好看」，是一个会被当成崩盘截图发出去的结论。所以这一组
的 provider 一律原样下发 `None`，绝不在任何一层补零（铁律 2）。

## 二、桶必须与舆情序列共用同一套

`candlesFor` 的 `list` 与 `buildRange(key).buckets` 逐桶对齐，`heatSeriesFor` 同理 ——
K 线、热度折线、趋势折线画在同一条横轴上。桶由 `buildRange` 下发、前端不自行算
（PRD §5 逐字），这一组只是照着那份桶取数。

## 三、阶段合并是口径，不是前端的分组

`stagesFor` 里那段「相邻且分类相同的时段归为同一阶段、样本不足的时段并入相邻阶段、
14 天及以上视图下单日孤立观点并入前一阶段」全部在后端完成，前端拿到的 `stages` 已经
是合并好的结果，只负责画色带和表格。判定用的阈值随响应下发（`threshold`）：小时粒度
是 `stageHalfDay`（5），日粒度是 `LOW_SAMPLE`（10）—— 它取决于粒度，所以是**响应里的
一个字段**，不是 /meta 上的一个常量（/meta 里那两个数是它的取值来源，不是判定本身）。

`status` 三态各是一句不同的话：`ok` 有阶段观点，`low_sample` 有讨论但都不够下结论，
`empty` 是区间内根本没有讨论，`unavailable` 是阶段观点尚未生成。四态在页面上分别对应
四块不同的内容，合并任意两个都会说出一个我们没得出的结论。
"""

from providers import get_provider
from providers.sentinel import MISSING

from .ranges import VALID_KEYS


def candles_for(code, range_key):
    """价格 K 线，与 `buildRange(range_key).buckets` 逐桶对齐。

    `status` 为 `'unavailable'` 时整份都是空 K（该产品没有价格数据源）；为 `'ok'` 时
    仍可能有空桶（休市），两者的区别在每根 K 的 `note` 上。`missing` 是空桶计数。
    """
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().candles_for(code, range_key)


def heat_series_for(code, range_key):
    """逐桶讨论热度序列（热度口径＝PRD §3.3，与全站同源）。

    **第一期没有屏幕直接读它** —— 它是 `stagesFor` 的上游输入，而 `stagesFor` 把算好的
    序列内嵌在 `series` 里一起下发（热度折线与阶段色带共用同一套几何，分两次取数就是
    白挨一次串行往返）。这个端点存在是因为 PRD §5 要求每个契约函数 1:1 对应端点。
    两者不会发散：它们是同一次计算的两个出口。
    """
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().heat_series_for(code, range_key)


def stages_for(code, range_key):
    """阶段观点：`{status, series, stages, granularity, granLabel, rule, threshold, unitCount}`。

    合并在这里完成，前端不得自行合并（见模块头第三节）。`stages[].sentiment` 在样本
    不足时是 `None`，`categoryLabel` 随之是「样本不足」—— 那是六态里的「样本不足」
    （查过了、不下结论），与字段级 `None` 的「暂不可用」是两态，别在渲染层合并。
    """
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().stages_for(code, range_key)


def daily_for(code):
    """日度评论量／活跃账号／价格序列（固定 42 天，**不吃区间**）。

    唯一不带 range 参数的契约函数：它给的是完整日历，由调用方自己截。`'ALL'` 是全市场
    合计的伪代码。第一期同样没有屏幕直接读它（理由同 `heat_series_for`）。
    """
    return get_provider().daily_for(code)
