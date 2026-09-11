"""讨论热度（PRD §3.3，CLAUDE.md 铁律 1 点名的三条口径之一）。

设计源逐字：`讨论热度 ＝ 评论量 ＋ 0.3 × 点赞 ＋ 1 × 转发`
（`design/radar-data.js` 的 `HEAT_FORMULA` / `heatOf()`，`fixtures/meta.json` 的
`heat.weights` 是同一组权重的下发副本）。

叶子模块，不 import provider —— 真实 provider 与端点共用这一份。演示 provider 不走这里
（fixture 里的热度值来自设计源，是 100% 还原验收的基准）。

三项里**任何一项未知，热度就是未知**。`0.3 × None` 不是 `0`，写成 0 就是在说
「这只产品没人转发」，而事实是「转发数没采到」（铁律 2）。
"""

import math

# 与 fixtures/meta.json 的 heat.weights、design/radar-data.js 的 HEAT_W 同源。
WEIGHTS = {"like": 0.3, "share": 1}


def heat_of(comments, likes, shares):
    if comments is None or likes is None or shares is None:
        return None
    x = comments + WEIGHTS["like"] * likes + WEIGHTS["share"] * shares
    # JS 的 Math.round 是「向正无穷取半」，Python 的 round() 是银行家舍入
    # （`round(2.5) == 2`）。热度要和设计源对得上，用前者。
    return math.floor(x + 0.5)
