"""生成 fixtures/sixstate/ —— 六态每一态钉一个可触发的样例。

## 为什么需要它

演示数据是自洽生成的，字段永远齐全：`likes` 不会缺、`etf_mentions` 不会空。也就是说
六态里除了 `0` 和「待确认」，其余几态在演示态下**一次都不会发生**，铁律 2（字段级 null
绝不落成 0）从来没有被真正执行过一次。等真实库接上、大量 AI 派生字段变成 null 的那天
才发现渲染成了 0，就已经在向产品团队撒谎了。

所以手工构造一份缺失态数据，让红线断言有东西可断。

## 构造原则

- **从演示 fixture 派生，只改要改的字段。** 字段形状与真实响应完全一致，不会出现
  「测试里的假数据形状和线上不一样，所以测过了也白测」。
- **确定性。** 全是写死的下标与取值，没有随机。
- **每条只演一态**，出问题时一眼看得出是哪一态崩了。

跑法：`cd backend && .venv/Scripts/python fixtures/make_sixstate.py`
"""

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMO = HERE / "demo"
OUT = HERE / "sixstate"

RANGE = "d7"
ISSUER = "华夏"       # 有提及 ETF 的发行商官号
BARE = "易方达"        # 区间内一条 ETF 都没提的官号，用来触发「—」

# ── 市场域（板块总览／产品监控）的样本产品 ────────────────────────────────
#
# 全部取演示池里**真实存在**的代码，不新造产品：/meta 不在场景层覆盖（MASTER 仍是完整
# 120 只），而设计源镜像里的主题、竞品、K 线等仍按 code 取数。造一个 MASTER 里没有的
# 代码，页面会在离六态十万八千里的地方炸。
CTRL = "3033"       # 对照：字段齐全的自家产品，也是产品监控页的默认产品
NO_ACCOUNTS = "3469"  # 活跃账号数取不到 → 「数据暂不可用」＋「该产品的账号口径尚未核验」
NO_HEAT = "3174"      # 讨论热度取不到 → 榜单那一格「数据暂不可用」，且传染成合计未知
NO_ATTITUDE = "3442"  # 态度样本全零 → 真零 ＋ 样本不足 ＋ 占比「—」
RISK_NULL = "3153"    # 自家但没做过合规扫描 → complianceCount 为 null（演示数据本来如此）
RISK_ZERO = "3037"    # 自家且扫过、确实零条 → complianceCount 为 0（真零对照）
PEERS = ["3032", "3589"]  # 两只竞品：竞品不纳入合规识别，complianceCount 结构性不适用

MARKET_CODES = [CTRL, NO_ACCOUNTS, NO_HEAT, NO_ATTITUDE, RISK_NULL, RISK_ZERO] + PEERS

# `delta()` 缺输入时的返回，与 design/radar-data.js 第 223 行逐字相同。
# 抄这两个**字面量**是安全的：它们里面没有任何计算 —— 没有 toFixed、没有百分比、
# 没有正负号拼接。真正会算错的那几支（`+12（+25.0%）`）一律不在这里手写，它们只由
# generate.mjs 跑设计源产出（铁律 1）。
UNAVAILABLE = {"text": "数据暂不可用", "short": "暂不可用", "abs": None, "pct": None, "dir": 0}
# base 为 0 且增量为 0 的分支（同一函数第 227 行）：结构性的「—」，不是「不知道」。
NIL = {"text": "—", "short": "—", "abs": 0, "pct": None, "dir": 0}


def load(name):
    return json.loads((DEMO / f"{name}.json").read_text(encoding="utf-8"))


def build_posts():
    """六态样本贴。全部落在 d7，其余区间照常回落演示数据。"""
    src = load("official_posts")[RANGE]
    tpl = next(p for p in src if p["mentioned"])  # 有挂载标的的模板

    def make(base, account, post_id, **over):
        p = dict(base)
        p["account"] = account
        p["accountFull"] = account
        p["accountType"] = "发行商官号"
        p["isIssuer"] = True
        p["id"] = post_id
        p.update(over)
        return p

    return [
        # ① `0` —— 真的数过了，确实是零。**必须仍然渲染成 0。**
        #    没有这一条，「null 不许显示成 0」很容易矫枉过正，把真零也一起抹掉。
        make(tpl, ISSUER, "sixstate-zero", likes=0, comments=0, shares=0,
             engagement=0, confidence=0.95,
             summary="互动为零的帖子：赞、评、转都确实是 0，不是缺数据。", hasSummary=True),

        # ② 暂不可用 —— 字段应有值，但数据源没提供。数值位渲染长文案「数据暂不可用」。
        #    渲染成 0 或空白都是撒谎，这是整套护栏要守的那一条。
        make(tpl, ISSUER, "sixstate-null", likes=None, comments=None, shares=None,
             engagement=None, confidence=0.95,
             summary="平台计数字段未回传：三个数都应该有值，但当前取不到。", hasSummary=True),

        # ③ 待确认 —— AI 标注置信度低于 lowConfidence(0.7)，标签照挂但标「待确认」。
        make(tpl, ISSUER, "sixstate-pending", likes=12, comments=3, shares=1,
             engagement=16, confidence=0.42,
             summary="类型置信度 0.42，低于阈值，类型标「待确认」。", hasSummary=True),

        # ④ 待确认的反面 —— 置信度达标就不该挂「待确认」。
        make(tpl, ISSUER, "sixstate-confident", likes=88, comments=9, shares=4,
             engagement=101, confidence=0.96,
             summary="类型置信度 0.96，达标，不标「待确认」。", hasSummary=True),

        # ⑤ 暂无内容 —— 检查过了，确实没有摘要（图片帖）。与「暂不可用」不是一回事：
        #    一个是「查过了没有」，一个是「应该有但没取到」。
        make(tpl, ISSUER, "sixstate-nosummary", likes=5, comments=0, shares=0,
             engagement=5, confidence=0.95, hasSummary=False, summary=""),

        # ⑥ 「—」的来源 —— 这个官号区间内一条 ETF 都没提，清单表该显示
        #    「— 区间内未提及 ETF」，而不是「0 只 · 0 次」。
        make(tpl, BARE, "sixstate-nomention", likes=3, comments=1, shares=0,
             engagement=4, confidence=0.9, mentioned=[], camp="none",
             campPrimary="none", hasSummary=True,
             summary="这条没提任何 ETF：字段不适用，不是数值为零。"),
    ]


def build_mentions(posts):
    """两个官号的提及 ETF：一个有、一个空。其余官号照常回落演示数据。"""
    src = load("etf_mentions")
    donor = next(v for k, v in src.items() if k.endswith("|" + RANGE) and v["own"] and v["peer"])

    own = [dict(donor["own"][0], count=3, posts=2)]
    peer = [dict(donor["peer"][0], count=1, posts=1)]
    issuer = {
        "list": own + peer,
        "own": own,
        "peer": peer,
        "etfCount": 2,
        # total > postCount：出现次数累加口径（ETF_MENTION_RULE），一帖内出现 3 次计 3。
        # 与市场域的评论去重语义相反，这里刻意让它对不上以便被断言钉住。
        "total": 4,
        "postCount": 3,
    }
    # 空态：查过了，区间内没有符合条件的内容 → status=empty，**不是** 0，也不是 null。
    bare = {"list": [], "own": [], "peer": [], "etfCount": 0, "total": 0, "postCount": 0}
    return {f"{ISSUER}|{RANGE}": issuer, f"{BARE}|{RANGE}": bare}


def build_kol():
    """KOL 影响力页的六态样本：同一位 KOL 的 5 篇，每篇只演一态。

    这一页比官号页多一态要演：**操作方向判不出来**。设计源把它落成
    `directionPending=True` 而不是 `direction='hold'` —— 把没判出来的说成「持有观望」，
    是六态里「待确认」存在的全部理由（PRD §3.6）。
    """
    src = load("kol_impact")[RANGE]
    donor = next(p for p in src["posts"] if p["mentioned"] and p["hasSummary"])
    # 「方向待确认」徽章的样式对象由设计源的 dirStyle(null, true) 产出，这里借一份现成的，
    # 不在 Python 里重拼——那等于把展示助手实现两遍。
    pending_dir = next(p["dir"] for p in src["posts"] if p["directionPending"])
    kol = donor["kol"]

    def make(post_id, **over):
        p = dict(donor)
        p["id"] = post_id
        p["direction"] = None
        p["directionLabel"] = ""
        p["directionPending"] = False
        p["hasDir"] = False
        p["dir"] = None
        p.update(over)
        return p

    posts = [
        # ① `0` —— 数过了确实是零，必须仍然渲染成 0（反向断言，防矫枉过正）。
        make("sixstate-kol-zero", likes=0, comments=0, shares=0, engagement=0,
             confidence=0.95, hasSummary=True,
             summary="互动为零的帖子：赞、评、转都确实是 0，不是缺数据。"),

        # ② 暂不可用 —— 三个计数字段都取不到。这一条同时钉住**聚合**：
        #    声量排名里这位 KOL 的评论量合计必须也是「数据暂不可用」，不是把 null 当 0 加进去，
        #    更不是 NaN。少数一篇的合计冒充总数，比直接说不知道更难被发现。
        make("sixstate-kol-null", likes=None, comments=None, shares=None,
             engagement=None, confidence=0.95, hasSummary=True,
             summary="平台计数字段未回传：三个数都应该有值，但当前取不到。"),

        # ③ 待确认（方向）—— 操作类帖子但方向置信度不足。
        make("sixstate-kol-dirpending", likes=31, comments=7, shares=2, engagement=40,
             confidence=0.95, hasSummary=True, directionPending=True, hasDir=True,
             dir=pending_dir, directionLabel="待确认",
             # 摘要里**不能**出现任何一个方向词。断言要看的是方向那一枚徽章渲染成了什么，
             # 摘要正文里随口提一句「不是持有观望」，就足以让反向断言自己撞上自己。
             summary="操作类帖子，方向置信度不足：这一格该说明判不出来，不该硬填一个方向。"),

        # ④ 待确认（类型）—— AI 类型置信度低于 lowConfidence(0.7)。
        #    摘要里同样不能出现「待确认」三个字：KOL 详情页的徽章只写「待确认」不带数字
        #    （影响力页写「待确认 0.42」），摘要里带一句就够让断言自己满足自己了。
        make("sixstate-kol-lowconf", likes=12, comments=3, shares=1, engagement=16,
             confidence=0.42, hasSummary=True,
             summary="类型置信度 0.42，低于阈值：标签照挂，但要标出来 AI 没把握。"),

        # ⑤ 暂无内容 —— 查过了确实没摘要（图片帖），与「暂不可用」不是一回事。
        make("sixstate-kol-nosummary", likes=5, comments=0, shares=0, engagement=5,
             confidence=0.95, hasSummary=False, summary=""),
    ]

    # leaders 是**全量**画像榜，KOL 详情页拿它定「声量排名第一」与上一位／下一位的顺序。
    # 这里手写：把 kolProfile 在 Python 里再实现一遍，正是 ADR-0015 拒绝的那件事
    # （它的唯一实现在 frontend/src/lib/profile.js）。5 篇同一类型，数值一眼可核。
    # comments 是 None 而不是 4+7+3+0=14：其中一篇的评论数取不到，合计就是未知。
    t = donor["postType"]
    leader = {
        "kol": kol,
        "n": len(posts),
        "own": 0, "peer": 0, "both": 0, "ownAny": 0, "peerAny": 0,
        "engagement": None, "comments": None,
        "typeCounts": {t: len(posts)},
        "typeOrder": [t],
        "topType": t, "topTypeLabel": donor["typeLabel"],
        "styleTag": donor["typeLabel"] + "为主",
        "top": posts[0], "posts": posts,
    }
    camp_field = "camp"
    for p in posts:
        c = p[camp_field]
        if c == "own":
            leader["own"] += 1
        elif c == "competitor":
            leader["peer"] += 1
        elif c == "both":
            leader["both"] += 1
    leader["ownAny"] = leader["own"] + leader["both"]
    leader["peerAny"] = leader["peer"] + leader["both"]

    return {
        RANGE: {
            "posts": posts,
            "leaders": [leader],
            "kolActive": 1,
            "range": src["range"],
            "updated": src["updated"],
        }
    }


def build_opinions(kol):
    """KOL 详情页「其他产品观点及操作」表的六态样本。

    这张表只有一个数值位（互动量），所以它只演两态 —— 真零与暂不可用。剩下的态
    在同一页的发帖记录表里演（build_kol），不必在这儿重复一遍。

    `net`（情绪净值）在这张表里也带上了 null 与非 null 两种，但**页面不渲染它**：
    它是 CSV 与将来的态度视图要用的字段，六态护栏够不着，由
    backend/tests/test_kol.py::test_net_is_null_when_the_sample_is_too_thin 钉住。
    """
    src = load("kol_opinions")[f"{kol}|{RANGE}"]
    donor = src[0]

    def make(code, **over):
        r = dict(donor)
        r["code"] = code
        r.update(over)
        return r

    # 顺序是手写的。真实实现按互动量降序排（backend/tests/test_kol.py 钉住），
    # 而互动量未知的那一行该排哪儿是个产品问题，还没有答案 —— fixture 不替它表态，
    # 把 null 行放在末尾只是为了确定性，不代表这就是排序口径。
    return {
        f"{kol}|{RANGE}": [
            make("9999", engagement=143, net=12.5, confidence=0.91,
                 summary="互动量正常的一行，用来跟下面两行对照。"),
            # ① 真零：这条观点确实一个互动都没有。仍然渲染 0。
            make("9998", engagement=0, net=0, confidence=0.91,
                 summary="互动量确实是 0：没人点赞没人评论，不是没取到数。"),
            # ② 暂不可用：互动量应该有值，当前取不到 → 「数据暂不可用」。
            #    net 同时为 null（样本不足），两个 null 含义不同：一个是没采到，
            #    一个是样本不够不下结论。页面上只看得见前者。
            make("9997", engagement=None, net=None, confidence=0.91,
                 summary="互动量未回传：这一格不能显示 0，也不能留空。"),
        ]
    }


def build_kol_mentions():
    """产品监控「产品相关 KOL」表的六态样本 —— 三只产品各演一种「没有主要态度」。

    这一组的要害是**三件事长得很像**：

      3033 有表、但某一行的主要态度取不到 → 那一格「暂不可用」（短徽章）
      3469 整张表取不到（KOL 身份映射没接上） → 「数据暂不可用 — KOL 身份名单或账号映射尚未核验。」
      3442 查过了，名单里没人提过它           → 「暂无相关内容 — 当前区间未发现已识别 KOL……」

    三句话在页面上不可互换：第一句是一格，后两句是整张表；后两句一个是「查不了」、
    一个是「查过了没有」。合并任意两句，读的人都会得出一个我们没得出的结论。
    """
    src = load("kol_mentions")["3033|" + RANGE]
    donor = src["list"][0]

    def row(name, count, attitude, label, excerpt):
        r = dict(donor)
        r["kolName"] = name
        r["kolAccountId"] = "kol-sixstate-" + str(count)
        r["mentionCommentCount"] = count
        r["dominantAttitude"] = attitude
        r["dominantLabel"] = label
        r["representativeExcerpt"] = excerpt
        # 三个数在契约里是同一个数（见 tests/test_evidence.py 的
        # test_kol_mention_rows_carry_their_own_evidence）。造样本时把 count 改小却留着
        # 供体那 13 条证据，页面上就会出现「提及 2 条」配「证据 13 条 →」——六态样本自己
        # 先不自洽，后面读它的人只会怀疑断言。
        r["evidence"] = donor["evidence"][:count]
        r["evidenceCount"] = len(r["evidence"])
        return r

    return {
        "3033|" + RANGE: {
            "status": "ok",
            "scope": src["scope"],
            "list": [
                # ① 样本够：照常给结论。没有这条反向断言，「整列都填暂不可用」也能过。
                row("六态对照 · 提及五条", 5, "positive", "积极",
                    "有效提及五条以上：足够下结论，这一格该显示态度标签。"),
                # ② 样本不足：后端给 null（PRD §5 表 P8「主要态度（<3 条不输出）」）。
                #    页面渲染短徽章「暂不可用」——不是 0、不是空白、更不是随手挑一个方向。
                #    KOL 名和摘要里都不能出现方向词或「样本不足」四个字：这一行整段都会被
                #    反向断言扫过，样本自己带上要拒绝的字，撞的是自己（已经撞过一次）。
                row("六态样本 · 提及两条", 2, None, None,
                    "有效提及不足三条：这一格不输出主要态度，也不能拿一个倾向充数。"),
            ],
        },
        # 整张表取不到：字段应有值，但 KOL 身份名单／账号映射尚未核验。
        "3469|" + RANGE: {"status": "unavailable", "scope": src["scope"], "list": []},
        # 查过了，这个区间名单里没人在评论区提过它。与上面那条的 list 一模一样（都是空的），
        # 只有 status 分得开——这正是这一组端点为什么带 status。
        "3442|" + RANGE: {"status": "empty", "scope": src["scope"], "list": []},
    }


def build_evidence():
    """原文证据侧栏的六态样本（`evidence_d7` 表，参数键 `code|ctxKey|polarity`）。

    fixture 的形状是 `{items, orders}`：items 是生成顺序的原文，orders[n-1] 是取 n 条
    时的次序（为什么不在 Python 里排序，见 providers/demo.py 与 generate.mjs）。手写这
    一份时同样守着它 —— 让 n 真的还是 n 条，而不是一份忽略 n 的常量。

    两只产品：3033 演行内的真零／null，3037 演整份为空。
    """
    src = load("evidence_" + RANGE)
    donor = src["3033|" + RANGE + "|sum|neutral"]["items"][0]

    def item(post_id, excerpt, **over):
        e = dict(donor)
        e["id"] = post_id
        e["excerpt"] = excerpt
        e.update(over)
        return e

    items = [
        # ⓪ 对照卡。它排第一是**有原因的**：六态断言按「展开单帖详情」把抽屉切成一张张卡，
        #    而第一张卡的那一段还粘着它前面的整个页面（页面上别处的「暂不可用」会让下面
        #    几条的反向断言当场撞上自己）。放一张不被断言的卡在最前面，后面三张就各自
        #    干净地占一段。
        item("sixstate-ev-control", "对照卡：字段齐全的一条原帖，用来跟下面三条对照。",
             comments=42, interactions=910),
        # ① `0` —— 数过了确实是零。必须仍然渲染 0（反向断言，防矫枉过正）。
        item("sixstate-ev-zero", "互动为零的原帖：评论与互动都确实是零，不是缺数据。",
             comments=0, interactions=0),
        # ② 暂不可用 —— 两个计数取不到。**短徽章**「暂不可用」，不是长文案。
        item("sixstate-ev-null", "平台计数字段未回传：这两格取不到，不能填零。",
             comments=None, interactions=None),
        # ③ 同一张卡里的另一套文案 —— 作者名取不到时渲染的是长文案「数据暂不可用」。
        #    两套文案并存、不可互换（PRD §3.6 与 §3.1／§5 delta 契约），一张卡上同时出现
        #    正好钉住「没人顺手把它们统一成一句」。
        item("sixstate-ev-noauthor", "作者名未回传：这一格用长文案，与上面那两格不是一套。",
             authorName=None, comments=7, interactions=19),
    ]

    def pack(rows):
        # orders[n-1] = 取 n 条时的次序。手写 fixture 只有这几条，n 超了就给全部。
        return {"items": rows, "orders": [list(range(min(n, len(rows)))) for n in range(1, 13)]}

    return {
        "3033|" + RANGE + "|sum|neutral": pack(items),
        # 空数组 = 「查过了，这个筛选范围内没有可展示的原文证据」→ 侧栏空态。
        # 不是错误、不是 0 条结果的占位卡，更不是「数据暂不可用」。
        "3442|" + RANGE + "|sum|neutral": pack([]),
    }


def build_market():
    """市场域三张表：pool / ranks / benchmark，都只覆盖 d7。

    池被裁到 8 只（MARKET_CODES），不是整份 120 只的拷贝 —— 整份拷贝有 600 KB，
    「每条只演一态、一眼看得出哪一态崩了」这条构造原则当场作废。裁得掉是因为屏幕的
    候选列表读的是 `pool().list` 而不是 MASTER：可见集来自池（铁律 3），池小了列表就短，
    不会去查一个池里没有的产品。

    `own` 那几个合计是**手写**的，不是从下面这份 list 里加出来的：这张表要演的正是
    「合计里混进一个 null 会怎样」，而真正的求和实现（generate.mjs 的 addN）由
    backend/tests/test_market.py 拿完整演示数据钉住。两边各管一头。
    """
    pool = load("pool")[RANGE]
    ranks = load("ranks")[RANGE]
    bench = load("benchmark")
    by_code = {o["code"]: o for o in pool["list"]}

    picked = []
    for code in MARKET_CODES:
        o = dict(by_code[code])
        if code == NO_ACCOUNTS:
            # 活跃账号数应该有值，这只产品的账号口径还没核验过 → null，不是 0。
            o["activeAccounts"] = None
            o["activeByBucket"] = None
        elif code == NO_HEAT:
            o["discussionHeat"] = None
        elif code == NO_ATTITUDE:
            # 真零：数过了，积极消极中性都确实是 0。样本不足随之为真（0 < LOW_SAMPLE），
            # 赞踩比分母为 0 → 占比是结构性的「—」。三态同出一只产品，互不遮蔽。
            o["attitude"] = {"positive": 0, "negative": 0, "neutral": 0, "sampleSufficient": False}
            o["buckets"] = [dict(b, positive=0, negative=0) for b in o["buckets"]]
        picked.append(o)

    keep = set(MARKET_CODES)
    sub = lambda m: {c: v for c, v in m.items() if c in keep}  # noqa: E731

    compliance = sub(pool["complianceCount"])
    # 自家产品的舆情条数在这个场景里确实都是零（榜单不标红点、KPI 的负面数是真零 0）。
    neg = {c: (0 if by_code[c]["ownership"] == "own" else v)
           for c, v in sub(pool["negMentions"]).items()}
    alerts = {c: (0 if by_code[c]["ownership"] == "own" else v)
              for c, v in sub(pool["alerts"]).items()}

    own = {
        "count": sum(1 for o in picked if o["ownership"] == "own"),
        # 合计里有一只的热度不知道（NO_HEAT），合计就是不知道 —— 不是把它当 0 加进去。
        # `sum + null === sum + 0` 得到的那个数看着完全正常，这才是最难被发现的一种撒谎。
        "heat": None,
        "neg": 0,          # 真零：确实一条可归类负面都没有，必须仍然显示 0。
        "pos": sum(o["attitude"]["positive"] for o in picked if o["ownership"] == "own"),
        # 有一只自家产品没做过合规扫描（RISK_NULL），「没扫过」不等于「零条」。
        "risk": None,
        "dHeat": UNAVAILABLE,   # 当期未知 → 环比整体未知
        "dNeg": NIL,            # 当期与基准期都是 0：结构性的「—」，不是不知道
        # 数值位与环比位各说各的：积极内容数是确切的，它的基准期取不到。
        # 这一条钉住两个位置不会互相传染 —— 一个显示数字，另一个显示「暂不可用」。
        "dPos": UNAVAILABLE,
    }

    write("pool", {RANGE: {
        "list": picked,
        "globalMax": max(o["maxBucket"] for o in picked),
        "alerts": alerts,
        "negMentions": neg,
        "baseMentions": sub(pool["baseMentions"]),
        "baseComments": sub(pool["baseComments"]),
        "baseHeat": sub(pool["baseHeat"]),
        "complianceCount": compliance,
        "own": own,
    }})

    # 排名保留演示数据里的相对次序再重新编号，不在这里按评论量重排一遍 —— 名次口径
    # （评论量降序、同分按代码）的实现只有设计源一处，这里只做「取子集」。
    order = sorted(keep, key=lambda c: ranks["map"][c])
    write("ranks", {RANGE: {"map": {c: i + 1 for i, c in enumerate(order)}, "total": len(order)}})

    out = {}
    for code in MARKET_CODES:
        b = dict(bench[f"{code}|{RANGE}"])
        if code == NO_ACCOUNTS:
            b["accounts"] = UNAVAILABLE
        elif code == NO_HEAT:
            b["heat"] = UNAVAILABLE
        elif code == NO_ATTITUDE:
            b["positive"] = NIL
            b["negative"] = NIL
            # 基准期同样是零，否则「当期 0、基准 37、环比 —」自相矛盾。
            b["base"] = dict(b["base"],
                             attitude={"positive": 0, "negative": 0, "neutral": 0,
                                       "sampleSufficient": False},
                             buckets=[dict(x, positive=0, negative=0) for x in b["base"]["buckets"]])
        out[f"{code}|{RANGE}"] = b
    write("benchmark", out)


def build_candles():
    """K 线整份取不到（`NO_ACCOUNTS` 那只产品，d7）。

    演示池里本来就有两只没有行情源的产品（3406／3087），但它们**不在六态池的 8 只
    里** —— 场景层是按键覆盖的，`pool.json` 只给了 d7 一个键，整份 d7 池就被换成这 8 只，
    `?code=3406` 会打开一个池里不存在的产品。所以这一态得搬到池内的产品身上。

    搬的方式是**整份拷贝那只产品的响应**，不是照着分支重写一遍。这份响应里没有任何
    与产品有关的东西：`granularity`／`granLabel`／每根 K 的 `bucket` 全部来自 `buildRange`，
    `currency` 是 null，`note` 是同一句话。下面那条断言就是在钉这件事 —— 哪天它变得与
    产品有关，拷贝就不再成立，这里会先炸。
    """
    src = load("candles")
    donor = src["3406|" + RANGE]
    mine = src[NO_ACCOUNTS + "|" + RANGE]
    assert [b["bucket"] for b in donor["list"]] == [b["bucket"] for b in mine["list"]], \
        "供体与目标产品的桶标签不一致：unavailable 分支已经不是产品无关的了，不能再整份拷贝"
    return {NO_ACCOUNTS + "|" + RANGE: donor}


def build_stages():
    """阶段观点尚未生成（同一只产品，d7）。搬迁理由同 `build_candles`。

    这里**不拷贝供体**，而是拿这只产品自己的响应改状态：`series` 是它自己的热度序列，
    是真数据，换成别人的就是在页面上画另一只产品的热度折线。设计源的 unavailable 分支
    是 `Object.assign(base, {status})`，而 `base` 里根本没有 `threshold`／`unitCount` ——
    没有阶段就没有判定，所以这两个键要**删掉**，不是置零、也不是置 null。

    `status` 之外一个字都不改，正是为了让页面上那两句话形成对照：折线照常画（热度是
    采集来的），色带画不出来（观点是 AI 归纳的，还没跑）。
    """
    src = load("stages")
    mine = dict(src[NO_ACCOUNTS + "|" + RANGE])
    mine["status"] = "unavailable"
    mine["stages"] = []
    mine.pop("threshold", None)
    mine.pop("unitCount", None)
    return {NO_ACCOUNTS + "|" + RANGE: mine}


def main():
    OUT.mkdir(exist_ok=True)
    build_market()
    posts = build_posts()
    write("official_posts", {RANGE: posts})
    write("etf_mentions", build_mentions(posts))
    kol_impact = build_kol()
    write("kol_impact", kol_impact)
    write("kol_opinions", build_opinions(kol_impact[RANGE]["leaders"][0]["kol"]))
    write("kol_mentions", build_kol_mentions())
    write("evidence_" + RANGE, build_evidence())
    write("candles", build_candles())
    write("stages", build_stages())


def write(name, table):
    path = OUT / f"{name}.json"
    path.write_text(
        json.dumps(table, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"{path.name}  {path.stat().st_size:,} B")


if __name__ == "__main__":
    main()
