"""讨论热度（PRD §3.3，CLAUDE.md 铁律 1 点名的三条口径之一）。

设计源逐字：`讨论热度 ＝ 评论量 ＋ 0.3 × 点赞 ＋ 1 × 转发`
（`design/radar-data.js` 的 `HEAT_FORMULA` / `heatOf()`，`fixtures/meta.json` 的
`heat.weights` 是同一组权重的下发副本）。

叶子模块，不 import provider —— 真实 provider 与端点共用这一份。演示 provider 不走这里
（fixture 里的热度值来自设计源，是 100% 还原验收的基准）。

## 转发数未知时的下限口径（ADR-0022）

评论量与点赞是 dump 的列，永远有值；转发数只在源侧 raw_json 坏掉的那 0.09% 帖子上
是未知。原来的规则是「任何一项未知，热度就是未知」：那一帖让所在产品整个窗口的热度
变成 None，`own.heat` 再让它传染到公司级 KPI。合成库实测 d30 有 99/120 只产品热度为
None、评论量前十名全灰 —— 一个只影响千分之一帖子的缺口，把页面上最常看的那一列
整个抹掉了。

[ADR-0022](../../docs/adr/0022-heat-lower-bound-disclosure.md) 把它改成**按已知项计算并
披露未知帖数**：调用方把窗口内**已知**转发的和与转发未知的帖子数一起传进来，
`unknown_posts > 0` 时算出来的热度是一个**下限**，和它并排下发的 `heatUnknownPosts`
让读的人知道下限差在哪里。这不是「把 None 当 0」——铁律 2 反对的是用一个看着完整的
数字冒充未知；这里未知的那部分被逐字说了出来，数字只是被如实标成了下限。

三条边界照旧守着：`comments` 或 `likes` 为 None 仍是 None（它们没有「已知部分」可言）；
`shares` 为 None 且 `unknown_posts == 0` 仍是 None（没有任何披露，就没有资格给数）；
`unknown_posts > 0` 时结果**等于**已知项之和 —— 不给平均值、不做估算，估算出来的数字
和真的长得一样。
"""

import math

# 与 fixtures/meta.json 的 heat.weights、design/radar-data.js 的 HEAT_W 同源。
WEIGHTS = {"like": 0.3, "share": 1}


def heat_of(comments, likes, shares, unknown_posts=0):
    """`unknown_posts` 是窗口内转发数未知的帖子数；`shares` 是其余帖子的已知和。

    `unknown_posts > 0` 时返回值是下限（ADR-0022），调用方必须把 `unknown_posts` 一并
    下发（`heatUnknownPosts`），否则这个数就成了没有标注的近似值。
    """
    if comments is None or likes is None:
        return None
    if shares is None:
        if not unknown_posts:
            return None
        # 已知和为 None 而未知帖数大于零：调用方没有任何一帖的转发数可加，已知和就是 0。
        # 披露了未知帖数，这个 0 才是「已知部分为零」而不是「当作零」。
        shares = 0
    x = comments + WEIGHTS["like"] * likes + WEIGHTS["share"] * shares
    # JS 的 Math.round 是「向正无穷取半」，Python 的 round() 是银行家舍入
    # （`round(2.5) == 2`）。热度要和设计源对得上，用前者。
    return math.floor(x + 0.5)
