"""评论×产品标注 Prompt v3：把产品身份信号与题材／标的上下文彻底分开。

v3 沿用 v2 的七维输出结构，所以仍配对 schema ``v2``。版本升级只改变输入语义：

* ``product.aliases`` 只含能唯一指向指定产品的写法；
* ``family_terms`` / ``underlying_terms`` 只能帮助理解题材，不能单独证明相关；
* 最多三层父回复与帖子信息只用于消歧，不能把上下文作者的观点移植给当前评论；
* 三层上下文后仍无法确定指代时保持 ``needs_context``，不猜。

新版本必须重标已有 comment_product 判定单元；否则 ``fill_missing_only`` 会把旧版错误结论
当成“字段齐全”而跳过。调度层通过 ``REQUIRES_FULL_REANNOTATION`` 读取这一策略。
"""

from .comment_product_v2 import SYSTEM as _V2_SYSTEM
from .comment_product_v2 import user_message

VERSION = "comment-product-v3"
REQUIRES_FULL_REANNOTATION = True

_IDENTITY_AND_CONTEXT_RULES = r"""# v3 产品身份硬规则（优先于下文其他说明）

`product` 中各字段的证据等级不同，绝不能混用：

- `code`、`name` 与 `aliases` 是当前指定产品的唯一标识。评论自身明确出现其中一项，才是直接的产品相关证据。
- `family_terms` 是多只产品共享的指数／资产族词，例如「恒指／HSI」「BTC」「ETH」。
- `underlying_terms` 是杠杆／反向产品所跟踪的股票或资产词，例如「NVDA」。
- `family_terms` 和 `underlying_terms` **不能单独证明**评论在评价当前产品；只出现这些词时，通常是在聊指数、资产或标的，判 `irrelevant`，并按需填写 `market_direction`。
- 明确提到其他 ETF、却没有提到当前产品或作出与当前产品的比较，判 `irrelevant`。

以 3037 为例：

- 「恒指今日要跌」「HSI 又穿两万」只谈共享指数，均为 `irrelevant`。
- 「$02800.HK$ 費率較低」明确谈其他 ETF，对 3037 为 `irrelevant`。
- 「$03037.HK$ 點差太大」明确出现当前产品代码，对 3037 为 `relevant`。
BTC／ETH 等资产词遵循完全相同的规则：资产本身不等于跟踪它的某一只 ETF。

# v3 上下文硬规则

- `parent_comments` 若存在，是由近到远、最多三层的父回复正文；旧数据也可能只有单条 `parent_comment`。
- 父回复、`post_title`、`post_context` **只用于消歧**当前评论里的「这只／它／同意」等指代，不能把上下文作者的观点当成当前评论的观点。
- 一条自足的指数／标的评论不会因为所在帖子属于当前产品，就自动变成产品评价。
- 看完最多三层父回复仍不能唯一确定指向当前产品，必须保持 `needs_context`；此时 `attitude=null`、`aspects=[]`、`evidence=null`、`needs_review=true`，并写明指代仍不清楚。
"""

SYSTEM = _IDENTITY_AND_CONTEXT_RULES + "\n\n" + _V2_SYSTEM

