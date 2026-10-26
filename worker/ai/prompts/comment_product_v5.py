"""Strict high-risk negative-comment classifier."""

from .comment_product_v4 import SYSTEM as _V4_SYSTEM, user_message

VERSION = "comment-product-v5"
REQUIRES_FULL_REANNOTATION = True

SYSTEM = _V4_SYSTEM.split("# 市场方向与合规", 1)[0] + r"""

# 市场方向与高风险负面评论（舆情）

market_direction 取 bullish/bearish/neutral/null，与产品态度独立。

旧版的一般 `compliance_concern` 已废止，不得输出。普通费率、点差、跟踪误差、披露位置、
KYC、风险提示或一般服务抱怨，即使态度负面，也不是本字段所称的舆情。

compliance_tags 现在只允许以下五类：
- abusive_attack：针对指定产品或南方东英的直接辱骂、侮辱、恶意人身攻击；只批评费率高、
  产品差、客服慢不算辱骂攻击；
- regulatory_complaint：明确已经或打算向监管、消委会、港交所或警方投诉举报；
- serious_allegation：明确指控操纵、欺诈、内幕、利益输送等重大违法违规；
- unverified_claim：传播未经证实的清盘、停牌、被查、跑路、暴雷等重大传闻；
- mobilization：号召集体投诉、刷差评、转发或扩散上述重大内容。

高风险标签必须同时满足：这是一条评论；内容与指定产品或南方东英相关；相应的产品态度或
发行商态度为 negative；命中以上至少一类。任一条件不满足都输出空数组。不要把父帖观点、
市场下跌判断、普通负面体验或一般合规疑问继承到评论上。

# 输出纪律

逐条输出；item_id 原样抄回。任何 evidence 都必须来自当前 comment 的连续原文。拿不准时
needs_review=true，并在 uncertainty_reasons 说明。不要输出 JSON 之外的文字。
"""
