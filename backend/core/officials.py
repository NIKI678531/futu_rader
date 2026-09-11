"""官号域口径（PRD §5 `officialPosts(range)`、`etfMentionsFor(account, range)`）。

## 两条口径上的坑，写在最前面

1. **「提及 ETF」按出现次数累加**（`ETF_MENTION_RULE` 逐字：「一帖内出现 3 次计 3，
   挂载标的至少计 1」）。这与市场域的**评论去重**（PRD §3.2，同一条评论对同一产品只计一次）
   **语义相反**。两者都叫「提及」，但不是一回事，不得互相套用。
2. **阵营三分互斥**：官号页问「这条动态归哪个阵营」，一条只能归一类（own / competitor /
   both / none）。KOL 页问的是「这个 KOL 提没提我们」，「提自家」含 both。
   看起来像不一致，实际是两个不同的问题（ADR-0013 O4）。**不要顺手统一。**

匹配范围只含 ETF 产品池（61 自家 + 59 竞品），个股代码与个股名称不计入。
"""

from providers import get_provider
from providers.sentinel import MISSING

from .ranges import VALID_KEYS


def official_posts(range_key):
    """区间内全部官号的帖子级内容流（含 AI 摘要 / 类型双标签 / 提及产品 / 阵营）。"""
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().official_posts(range_key)


def etf_mentions_for(account, range_key):
    """单个官号在区间内的 ETF 提及统计（自家在前、竞品在后，按出现次数）。"""
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().etf_mentions_for(account, range_key)
