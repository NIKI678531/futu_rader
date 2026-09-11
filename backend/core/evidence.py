"""产品监控证据组口径（PRD §5：`topicsFor` / `kolMentionsFor` / `evidenceFor`）。

叙述组回答「讨论在说什么」，证据组回答「凭什么这么说」—— 产品监控页上每一个结论
旁边都有一个入口，点开是支撑它的原帖。所以这一组的正确性判据不是数对不对，而是
**点开的证据和点的那个结论是不是一回事**。

## ctxKey 是「哪一个结论」的地址

`evidenceFor(code, ctxKey, polarity, n)` 的 ctxKey 形如 `<区间>|<面板 id>`，面板 id
覆盖产品监控页上五种入口：

- `sum`                     当前舆情总结
- `<code>-pos|neg-<i>`      正／负面主题（`themesFor` 的 id）
- `<code>-tp-<i>`           热议话题（`topicsFor` 的 id）
- `<竞品代码><极性>`         关联竞品的正／负面讨论（注意 code 是**竞品自己**的代码）
- `stage-<n>-<起始日>`       阶段观点

四个参数都参与选取，任何一个被静默忽略都会让侧栏显示**另一个结论的证据** —— 这是
本组最贵的一类 bug：它不报错、数量对、格式对，只是配错了对象。所以未知的 ctxKey 或
极性一律 MISSING → 404（屏级错误条），不回落到「随便给几条」。

## n 的钳位是口径，不是防御

设计源逐字 `Math.max(1, Math.min(12, count || 6))`：默认 6 条、上限 12 条。它决定
侧栏取几条，前端不得自己再钳一次（铁律 1）。
"""

from providers import get_provider
from providers.sentinel import MISSING

from .ranges import VALID_KEYS

# PRD §5 `evidenceFor` 逐字：不传取 6 条，最多 12 条。
DEFAULT_COUNT = 6
MAX_COUNT = 12

POLARITIES = ("positive", "negative", "neutral")


def topics_for(code, range_key):
    """热议话题：按提及数降序的话题列表。

    每条带 `buckets`（逐桶提及数）、`delta`（内嵌环比形状）、`evidenceCount`、
    `peak`（峰值桶）与 `split`（正／中／负构成）。区间内没有提及时是空数组 ——
    「暂无内容」，不是 0 条话题里挑出来的 0（铁律 2）。
    """
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().topics_for(code, range_key)


def kol_mentions_for(code, range_key):
    """KOL 提及：`{status, scope, list}`，范围恒为客户维护的合作 KOL 名单。

    每行的 `dominantAttitude` 在**有效样本不足 3 条时为 None**（PRD §5 表 P8
    「主要态度（<3 条不输出）」）—— 少数几条帖子推不出一个人的倾向，硬给一个标签
    就是拿噪声当结论。前端渲染「暂不可用」，绝不是 0、不是空白、也不是随便挑一个
    极性。`status: 'unavailable'` 则是另一件事：KOL 身份映射对这只产品没接上，
    整张表都取不到，与「名单里没人提过它」（空列表 → empty）不同。
    """
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().kol_mentions_for(code, range_key)


def evidence_for(code, ctx_key, polarity, count=None):
    """某个结论的原文证据，按发布时间倒序，最多 12 条。

    区间从 ctx_key 里取（它的形状就是 `<区间>|<面板 id>`），未知区间、未知极性、
    未知面板 id 一律 MISSING。
    """
    parts = str(ctx_key).split("|")
    if len(parts) != 2 or parts[0] not in VALID_KEYS or not parts[1]:
        return MISSING
    if polarity not in POLARITIES:
        return MISSING
    n = max(1, min(MAX_COUNT, count or DEFAULT_COUNT))
    return get_provider().evidence_for(code, ctx_key, polarity, n)
