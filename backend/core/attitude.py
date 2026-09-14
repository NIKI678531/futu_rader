"""有效态度样本阈值（PRD §3.5）。

`sampleSufficient` 是**口径判定**，不是数数：它决定产品监控页出不出「主流倾向」这句
结论、热议总结走不走 `low_sample`。所以它跟热度公式一样只能有一处实现（铁律 1），
`providers/` 里不许再写一遍 `>= 10`。

## 分母里没有中性

逐字来自 `design/radar-data.js:398`：`(pos + neg) >= LOW_SAMPLE`。中性提及说明「有人
在谈，但没表态」，它撑不起一句倾向结论 —— 把它算进分母，一只产品可以靠 10 条「今天
除权」凑够样本，然后页面输出一个由 0 条正面、0 条负面得出的「主流倾向」。

## 这个 10 有三处，是同一个数

`fixtures/meta.json` 的 `thresholds.lowSample`（下发给前端的那份）、
`design/radar-data.js` 的 `LOW_SAMPLE`、这里。改一处要三处一起改。

## 别和 `envelope.LOW_SAMPLE` 弄混

那个是六态里的**状态字符串** `"low_sample"`（PRD §3.6 枚举），这个是**阈值数字**。
名字撞了，含义完全无关。
"""

LOW_SAMPLE = 10


def sample_sufficient(positive, negative):
    """有效态度样本够不够下倾向结论。

    正负两项任意一项未知 ⇒ 够不够也是未知（None），**不是 False**：False 在页面上是
    一句确切的话（「样本不足，暂无主流观点」），拿它盖住「不知道」就是铁律 2 反对的
    那件事。整块态度为 None 时调用方压根不会走到这里，这条分支是给部分缺失留的。
    """
    if positive is None or negative is None:
        return None
    return (positive + negative) >= LOW_SAMPLE
