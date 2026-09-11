"""KOL 域口径（PRD §5 `kolImpact(range)` 与 `kolOpinions(kol, range)`）。

## 为什么只有 kolImpact，没有 kolProfile

PRD §5 把 `kolProfile(name, posts, campFn?)` 也列进了契约函数，spec 里一度给它安排了
`GET /kols/{name}/profile`。**这个端点没有实现，也不应该实现**，理由见 ADR-0015：

PRD §4.3 M5 逐字要求声量排名「随 ETF 与类型筛选变化、不随 KOL 筛选变化」—— 也就是说
页面要的画像是**当前可见帖子子集**上的画像，而那个子集由四级级联筛选＋类型多选＋
`campRule` 开关在前端算出来。它不是一个能用查询参数表达的东西：把筛选语义在 Python 里
再实现一遍，是 CLAUDE.md 明令禁止的「照着行为手推一遍」，而且一处对不齐逐字比对就红。

所以 `kolProfile` 是**已下发数据之上的纯聚合**，留在前端（`frontend/src/lib/profile.js`），
后端不碰。这不违反铁律 1：铁律 1 约束的是口径公式（热度、去重、基准区间、情绪净值、
环比、阶段合并、样本阈值），`kolProfile` 数的是「这批帖子里提自家的有几篇」，
而「这批」正是筛选出来的可见集 —— 它按设计就该随筛选重算。

`kolImpact` 返回里的 `leaders` 是**全量**画像（不带筛选），由供数侧算好，
KOL 详情页拿它定「声量排名第一」与「上一位／下一位」的顺序。
"""

from providers import get_provider
from providers.demo import MISSING

from .ranges import VALID_KEYS


def kol_impact(range_key):
    """区间内全部合作 KOL 的帖子全集（含 AI 标注）＋全量画像榜 leaders。

    一次发整个区间的帖子，而不是按筛选组合切端点：KOL 页的四级级联、类型多选、表内
    关键词都是同一份帖子上的子集运算，按组合切会变成组合爆炸（ADR-0003 端点粒度）。
    """
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().kol_impact(range_key)


def kol_opinions(kol, range_key):
    """这位 KOL 对**发帖记录之外**的产品的观点与操作。

    与 `kol_impact` 分开取，是因为它按 KOL 取、且只有详情页用；并进 kolImpact 会让
    KOL 影响力页每次都白拉 32 个人的观点表（d7 约 1 MB）。

    每行的 `net`（情绪净值）在**有效产品态度评论不足 LOW_SAMPLE 条**时是 `None`：
    样本不够就不输出倾向结论（PRD §3.5、§3.6 的「样本不足」态）。这个 `None` 必须
    原样上线 —— 换成 `0` 就成了「中性」，而中性是个结论，我们并没有得出它。
    """
    if range_key not in VALID_KEYS:
        return MISSING
    return get_provider().kol_opinions(kol, range_key)
