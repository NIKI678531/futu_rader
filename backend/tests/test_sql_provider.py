"""`providers/sql.py` —— 真实数据 provider 的读路径。

**不接真库。** 真库在 `%LOCALAPPDATA%\\futu-radar\\radar.db`，4.9 GB，且含真实用户昵称／
IP 归属地／个人简介（ADR-0008）—— 测试不该依赖一个不进 git、每台机器都不一样、还带 PII
的文件。这里用 `radar_db.schema` 在内存里建同一套表，塞十来行编出来的数据。

schema 是共用的那一份，所以「列名对不上」这类错误照样能在这里被抓到。抓不到的是
「dump 里那一列的实际含义和我们以为的不一样」—— 那种事只能靠对真库的实测，模块头
记的那三条就是这么来的。

用编出来的数字有个额外好处：期望值可以手算写在断言旁边，读的人不用信任何一层实现。
"""

from datetime import date, datetime

import pytest
from sqlalchemy import delete, insert
from radar_db.schema import meta_kv
from sql_fixture import (
    ANCHOR,
    COMMENT_TEXT,
    FEED_TEXT,
    KOL_NAME,
    MASTER,
    OFFICIAL_FULL,
    OWN_CODE,
    PEER_CODE,
    add_annotations,
    add_comments,
    add_evidence,
    make_sql_provider,
)

from providers.sentinel import MISSING

# 另一只自家产品。合规四态里「扫过了、这只没命中」（empty）与「压根没扫过」
# （unavailable）只能靠两只产品分开测：一只命中让扫描算作跑过，另一只才轮得到 empty。
OTHER_OWN_CODE = next(
    p["code"] for p in MASTER["products"] if p["ownership"] == "own" and p["code"] != OWN_CODE
)


@pytest.fixture
def provider():
    return make_sql_provider()


# ── 锚点 ───────────────────────────────────────────────────────────────


class TestAnchor:
    def test_comes_from_the_database_not_the_clock(self, provider):
        """锚点是导入时算出来写进 `meta_kv` 的，不是 `date.today()`。

        今天是 2026-09-10，数据止于 08-26。用系统时间的话每个区间都是空的。
        """
        assert provider.build_range("d1")["to"] == ANCHOR
        assert provider.updated_at == "2026-08-25 23:59"

    def test_an_unprovisioned_database_is_unavailable_not_empty(self):
        """没导过数据 ⇒ 契约函数返回 `None` ⇒ envelope 判成 `unavailable`。

        返回 `[]` 或 0 会让「库还没建」长得和「这个区间真的没人发帖」一模一样。
        """
        p = make_sql_provider(anchor=None)
        assert p.build_range("d7") is None
        assert p.pool("d7") is None
        assert p.ranks("d7") is None
        assert p.official_posts("d7") is None
        assert p.kol_impact("d7") is None
        assert p.daily_for(OWN_CODE) is None

    def test_unknown_range_key_is_none(self, provider):
        assert provider.build_range("d999") is None


# ── 计数：真实字段 ─────────────────────────────────────────────────────


class TestPool:
    def test_counts_are_real(self, provider):
        """自家 3033 在 08-25：手算一遍。

        提及 = f1 + f2 = 2；评论量 = 4 + 1 = 5；
        点赞 = f1 帖赞 10 ＋ f1 评论赞 (3+0) ＋ f2 帖赞 0 = 13（HEAT_NOTE：点赞含两者）；
        活跃账号 = 发帖人 u1、u2 ＋ 评论人 u9、u1 = 3 人。
        """
        item = next(x for x in provider.pool("d1")["list"] if x["code"] == OWN_CODE)
        assert item["mentions"] == 2
        assert item["comments"] == 5
        assert item["likes"] == 13
        assert item["activeAccounts"] == 3

    def test_body_mentions_count_the_same_as_anchor_ones(self, provider):
        """f1 挂在 3033 讨论区，正文里提到 3032 —— 两只产品都要计入。

        这是 ADR-0009 修正的那件事：正文提及只能从 `raw_json.summary.rich_text` 拿到，
        `mentions.source='body'` 就是它的落点。漏掉它，竞品的声量会系统性偏低。
        """
        peer = next(x for x in provider.pool("d1")["list"] if x["code"] == PEER_CODE)
        # f1（正文提及）+ f3（挂载）= 2 篇；评论量 4 + 2 = 6
        assert peer["mentions"] == 2
        assert peer["comments"] == 6
        # 点赞 = f1 的 13 ＋ f3 的 (5 帖赞 + 1 评论赞) = 19；转发 2 + 0 = 2
        # 热度 = 6 + 0.3×19 + 2 = 13.7 → 14
        assert peer["discussionHeat"] == 14
        assert peer["interactions"] == 21

    def test_out_of_pool_mentions_are_dropped(self, provider):
        """`in_pool=False` 的标的（腾讯 0700）不该出现在任何地方。"""
        codes = {x["code"] for x in provider.pool("d1")["list"]}
        assert "0700" not in codes
        assert len(codes) == len(MASTER["products"])

    def test_products_with_no_posts_are_zero_not_missing(self, provider):
        """一条帖子都没有的产品要在列表里，值是 0，桶数和别人一样长。

        窗口内底库是全的，所以这个 0 是查出来的结论 —— 和「不知道」不是一回事。
        桶数不一致会让热力图那一行短一截，看起来像是最近几天没数据。
        """
        pool = provider.pool("d1")
        idle = next(x for x in pool["list"] if x["code"] not in (OWN_CODE, PEER_CODE))
        assert idle["mentions"] == 0
        assert idle["comments"] == 0
        assert idle["discussionHeat"] == 0
        assert len(idle["buckets"]) == 24  # d1 按小时切
        assert len(idle["activeByBucket"]) == 24

    def test_attitude_and_alerts_are_none_until_the_annotation_pipeline_exists(self, provider):
        """态度／预警／负面提及／合规数全部要 AI 标注（ADR-0017）。

        `{positive: 0, negative: 0, neutral: 0}` 会在界面上显示成「情绪中性」——
        那是一个我们并没有做出的判断（铁律 2）。
        """
        pool = provider.pool("d1")
        assert all(x["attitude"] is None for x in pool["list"])
        assert set(pool["alerts"].values()) == {None}
        assert set(pool["negMentions"].values()) == {None}
        assert set(pool["complianceCount"].values()) == {None}
        assert pool["own"]["neg"] is None and pool["own"]["pos"] is None


class TestUnknownSharesAreDisclosedAsLowerBound:
    """转发数未知的帖子让热度成为**下限**并披露帖数，不再让整只产品的热度变成未知（ADR-0022）。

    源库 raw_json 被 TEXT 列截断的行拿不到 `share_count`（0.09%）。原来的规则是任一帖
    未知 ⇒ 该产品整个窗口的转发／互动／热度全 None，`own.heat` 再把它传染到公司级 KPI：
    合成库上 d30 有 99/120 只产品热度为 None、评论量前十名全灰。

    新口径：`shares` / `interactions` / `discussionHeat` 按已知项算，`heatUnknownPosts`
    说出差了几帖。前者是下限，后者让它是一句能核对的话而不是一个没标注的近似值。
    `heatUnknownPosts == 0` 时三个数就是完整的数 —— 分辨两种情形靠这个键，不靠猜。
    """

    def test_the_product_with_the_broken_row_gets_a_lower_bound_and_the_count(self, provider):
        item = next(x for x in provider.pool("d1")["list"] if x["code"] == OWN_CODE)
        # f1 转发 2、f2 未知 ⇒ 已知和 2、未知 1 帖；互动 = 13 + 2；热度 = 5 + 0.3×13 + 2 = 10.9 → 11
        assert item["shares"] == 2
        assert item["heatUnknownPosts"] == 1
        assert item["interactions"] == 15
        assert item["discussionHeat"] == 11
        assert item["comments"] == 5
        # 桶级同样：09 点桶含 f1 与 f2，其余桶没有未知
        assert item["buckets"][9]["heatUnknownPosts"] == 1
        assert item["buckets"][9]["shares"] == 2
        assert all(b["heatUnknownPosts"] == 0 for i, b in enumerate(item["buckets"]) if i != 9)

    def test_products_without_broken_rows_report_zero_unknown(self, provider):
        peer = next(x for x in provider.pool("d1")["list"] if x["code"] == PEER_CODE)
        assert peer["heatUnknownPosts"] == 0
        assert peer["discussionHeat"] == 14

    def test_the_company_wide_headline_is_a_lower_bound_with_its_own_count(self, provider):
        own = provider.pool("d1")["own"]
        # 3033 的 11 ＋ 其余自家产品 0；未知帖数合计 1。基准期 f4：10 + 0.3×100 + 1 = 41。
        assert own["heat"] == 11
        assert own["heatUnknownPosts"] == 1
        assert own["dHeat"]["text"] == "-30（-73.2%）"
        assert own["count"] == sum(1 for p in MASTER["products"] if p["ownership"] == "own")

    def test_without_the_broken_row_the_numbers_are_the_same_and_the_count_is_zero(self):
        """同一批数据，只把那一行的转发数补成 0 —— 对照组。

        热度一样是 11：下限口径算的就是已知项。差别只在 `heatUnknownPosts`（0 vs 1），
        这正是这个键存在的理由：没有它，两种情形在页面上无法区分。
        """
        p = make_sql_provider(broken_share=False)
        item = next(x for x in p.pool("d1")["list"] if x["code"] == OWN_CODE)
        assert item["discussionHeat"] == 11
        assert item["shares"] == 2
        assert item["heatUnknownPosts"] == 0
        assert p.pool("d1")["own"]["heat"] == 11
        assert p.pool("d1")["own"]["heatUnknownPosts"] == 0


class TestRanks:
    def test_ranked_over_the_whole_market(self, provider):
        """铁律 3：排名底是完整活跃 ETF 池，不接受任何筛选参数。"""
        r = provider.ranks("d1")
        assert r["total"] == len(MASTER["products"])
        assert sorted(r["map"].values()) == list(range(1, len(MASTER["products"]) + 1))
        # 评论量：3032 有 6，3033 有 5，其余 0
        assert r["map"][PEER_CODE] == 1
        assert r["map"][OWN_CODE] == 2

    def test_ties_break_by_code_so_the_order_is_stable(self, provider):
        """并列 0 的那 118 只必须有确定顺序，否则同一只产品的名次会在两次请求间跳。"""
        r = provider.ranks("d1")
        zeros = sorted(c for c in r["map"] if c not in (OWN_CODE, PEER_CODE))
        assert [r["map"][c] for c in zeros] == list(range(3, len(MASTER["products"]) + 1))


class TestBenchmark:
    def test_compares_against_the_adjacent_equal_length_window(self, provider):
        """d1 的基准是 08-24：f4 有 10 条评论。5 vs 10 → -5（-50.0%）。"""
        b = provider.benchmark(OWN_CODE, "d1")
        assert b["comments"]["text"] == "-5（-50.0%）"
        assert b["comments"]["dir"] == -1
        assert b["base"]["comments"] == 10

    def test_heat_delta_compares_lower_bounds_and_discloses_both_sides(self, provider):
        """当期有一帖转发未知 ⇒ 热度环比是两个下限之间的比较，两侧的未知帖数分开说（ADR-0022）。

        当期 11（差 1 帖）vs 基准 41（完整）⇒ -30（-73.2%）。不是「数据暂不可用」——
        那会把整张 KPI 卡因为千分之一的坏行抹掉；也不是不加标注的 -73.2%。
        """
        b = provider.benchmark(OWN_CODE, "d1")
        assert b["heat"]["text"] == "-30（-73.2%）"
        assert b["heatUnknownPosts"] == {"current": 1, "base": 0}
        assert b["shares"]["text"] == "+1（+100.0%）"  # 已知和 2 vs 1
        assert b["base"]["heatUnknownPosts"] == 0

    def test_attitude_deltas_are_unavailable_not_flat(self, provider):
        b = provider.benchmark(OWN_CODE, "d1")
        for k in ("positive", "negative", "neutral"):
            assert b[k]["text"] == "数据暂不可用"

    def test_buckets_align_position_by_position(self, provider):
        """基准区间切一样多的桶，趋势图悬停要按同位比。

        桶里**没有** `i`：对齐靠下标，不靠字段（契约见 `fixtures/generate.mjs:169`，
        那五条序列的键与趋势图图例键一一对应）。这条断言原来查的是 `x["i"]` ——
        那是 sql 侧自己多发的字段，demo 下从来没有过，`test_provider_parity.py`
        建起来当天就把它抓了出来。
        """
        b = provider.benchmark(OWN_CODE, "d1")
        assert len(b["buckets"]) == 24 == len(b["base"]["buckets"])
        for x in b["buckets"]:
            assert set(x) == {"comments", "active", "interactions", "positive", "negative"}

    def test_unknown_product_is_missing_not_none(self, provider):
        """池里没有 0700（那是腾讯，不是 ETF）。

        回 MISSING 而不是 None：端点把它转成 404。回 None 的话页面会显示一屏
        「暂不可用」，看起来像采集掉了数，而真实原因是 URL 里的代码根本不在池里。
        demo provider 查不到 fixture 键时回的也是 MISSING —— 两边必须一致。
        """
        assert provider.benchmark("0700", "d1") is MISSING
        assert provider.heat_series_for("0700", "d1") is MISSING
        assert provider.daily_for("0700") is MISSING
        # 要 AI 的字段同样先验代码：产品在池里才轮到「这个字段暂不可用」。
        assert provider.topics_for("0700", "d1") is MISSING
        assert provider.topics_for(OWN_CODE, "d1") is None


class TestSeries:
    def test_heat_series_follows_the_calendar_buckets(self, provider):
        s = provider.heat_series_for(OWN_CODE, "d1")
        assert len(s) == 24
        nine = s[9]  # f1 09:00 与 f2 09:30 同桶
        # label 是稀疏的（设计源每 6 小时才给一个刻度），轴对齐要看 hour/tip
        assert (nine["hour"], nine["day"]) == (9, ANCHOR)
        assert nine["tip"] == "08-25 09:00–10:00"
        assert nine["mentions"] == 2
        assert nine["comments"] == 5
        assert nine["heat"] == 11  # f2 转发未知 ⇒ 下限，未知帖数随桶下发（ADR-0022）
        assert nine["heatUnknownPosts"] == 1
        assert s[0]["heat"] == 0 and s[0]["mentions"] == 0
        assert s[0]["heatUnknownPosts"] == 0
        assert all(x["positive"] is None for x in s)

    def test_daily_has_real_counts_and_no_prices(self, provider):
        """价格四项没有数据源 —— 写 None，不拿收盘价占位。"""
        d = provider.daily_for(OWN_CODE)
        assert len(d) == 42
        assert d[-1]["iso"] == ANCHOR
        assert (d[-1]["comments"], d[-1]["active"]) == (5, 3)
        assert d[-2]["iso"] == "2026-08-24"
        assert (d[-2]["comments"], d[-2]["active"]) == (10, 1)  # f4
        assert d[0]["comments"] == 0  # 窗口内确实没有 —— 0 是真的
        assert all(x["px"] is None and x["o"] is None for x in d)


# ── 账号域 ─────────────────────────────────────────────────────────────


class TestOfficialPosts:
    def test_matched_by_full_name_because_the_uids_are_synthetic(self, provider):
        """`master.json` 里官号的 `url` 带的 user-id 在真实 `feeds.author_uid` 里一个都不存在。

        那是设计源生成的演示 id。真实数据只能按全称认（命中 15/20）。
        """
        posts = provider.official_posts("d1")
        assert len(posts) == 1
        assert posts[0]["accountFull"] == OFFICIAL_FULL
        assert posts[0]["account"] == "恒生投资"

    def test_issuer_accounts_are_told_apart_by_having_competitor_mappings(self, provider):
        p = provider.official_posts("d1")[0]
        assert p["isIssuer"] is True
        assert p["accountType"] == "发行商官号"

    def test_anchor_mention_wins_the_primary_slot(self, provider):
        """一篇帖子挂在 3033 讨论区、正文提到 3032：主产品是挂载标的。"""
        p = provider.official_posts("d1")[0]
        assert p["code"] == OWN_CODE
        assert [m["code"] for m in p["mentioned"]] == [OWN_CODE, PEER_CODE]
        assert p["campPrimary"] == "own"
        assert p["camp"] == "both"  # 自家 ＋ 竞品都提到了

    def test_countable_fields_are_real(self, provider):
        p = provider.official_posts("d1")[0]
        assert (p["likes"], p["comments"], p["shares"]) == (10, 4, 2)
        assert p["engagement"] == 16
        assert p["t"] == 9  # 距区间起点 9 小时
        assert p["url"] == "https://www.futunn.com/post/1"

    def test_annotation_block_is_none_not_a_default_verdict(self, provider):
        """`postType: "other", confidence: 0` 会让界面显示一个我们没做出的分类。"""
        p = provider.official_posts("d1")[0]
        for k in ("postType", "typeLabel", "confidence", "direction", "summary", "fullText"):
            assert p[k] is None, k

    def test_etf_mentions_use_the_account_domain_caliber(self, provider):
        """账号域「提及 ETF」按出现次数累加 —— 和市场域的评论去重口径语义相反。"""
        m = provider.etf_mentions_for("恒生投资", "d1")
        assert m["etfCount"] == 2
        assert all(x["count"] == x["posts"] for x in m["list"])

    def test_etf_mentions_sorts_own_products_first(self, provider):
        """契约的排序是三级：自家在前 → 提及次数降序 → 代码升序。

        原来只有后两级，于是同一个官号在 demo 与 sql 下芯片顺序不同。逐字比对抓不到
        （它比的是 demo），只有接了真库才看得见 —— 那时候没人会想到去查排序。
        """
        m = provider.etf_mentions_for("恒生投资", "d1")
        # 两只都只被提 1 次，差别只在自家／竞品：3033 是自家，3032 是竞品。
        assert [x["code"] for x in m["list"]] == [OWN_CODE, PEER_CODE]

    def test_etf_mentions_returns_all_six_contract_keys(self, provider):
        """`own`／`peer` 是**两个子列表**，不是两个计数。

        官号动态页那句 `em.own.map(chipEl)` 直接吃它。发计数过去就是
        `em.own.map is not a function` —— 整屏白，而屏级边界会报成「后端服务连不上」。
        """
        m = provider.etf_mentions_for("恒生投资", "d1")
        assert set(m) == {"list", "own", "peer", "etfCount", "total", "postCount"}
        assert [x["code"] for x in m["own"]] == [OWN_CODE]
        assert [x["code"] for x in m["peer"]] == [PEER_CODE]
        assert m["total"] == sum(x["count"] for x in m["list"])
        assert m["postCount"] == 1  # 两只 ETF 都来自同一篇帖子

    def test_etf_mentions_short_name_matches_the_frontend_rule(self, provider):
        """`short` 要按 shortName 的两条正则来，不是只截长度。

        自家产品叫「恒生科技指數ETF」，去掉 ETF 后缀就是「恒生科技」。只截长度的话
        sql 下是另一个值，而页面上那一栏的宽度是按前者设计的。
        """
        m = provider.etf_mentions_for("恒生投资", "d1")
        assert next(x for x in m["list"] if x["code"] == OWN_CODE)["short"] == "恒生科技"

    def test_etf_mentions_accepts_either_name_form(self, provider):
        assert provider.etf_mentions_for(OFFICIAL_FULL, "d1")["etfCount"] == 2

    def test_unknown_account_is_missing_not_none(self, provider):
        """名单里没有这个官号 = 没这个资源 → 404，不是「这个官号暂不可用」。"""
        assert provider.etf_mentions_for("查无此号", "d1") is MISSING


class TestKolImpact:
    def test_only_active_kols_are_matched(self, provider):
        r = provider.kol_impact("d1")
        assert r["kolActive"] == 1
        assert len(r["posts"]) == 1
        assert r["posts"][0]["kol"] == KOL_NAME
        assert r["posts"][0]["views"] == 888  # browse_count 是真的

    def test_engagement_is_unknown_when_shares_are(self, provider):
        """互动数 = 赞 + 评 + 转。转发未知 ⇒ 互动未知，leaders 的合计也跟着未知。"""
        r = provider.kol_impact("d1")
        assert r["posts"][0]["engagement"] is None
        assert r["leaders"][0]["engagement"] is None
        assert r["leaders"][0]["comments"] == 1  # 能数的照数

    def test_leader_profile_counts_camps_but_not_types(self, provider):
        leader = provider.kol_impact("d1")["leaders"][0]
        assert leader["n"] == 1
        assert leader["own"] == 1 and leader["peer"] == 0
        # 类型分布来自 AI 类型标注
        assert leader["typeCounts"] is None and leader["topType"] is None
        assert leader["styleTag"] is None

    def test_opinions_need_annotations(self, provider):
        """空列表会被读成「这位 KOL 对别的产品没有观点」。"""
        assert provider.kol_opinions(KOL_NAME, "d1") is None


# ── 现行结论：哪一行标注算数（ADR-0019 §1） ────────────────────────────


class TestCurrentAnnotations:
    """`_current_annotations()` —— 发布规则的唯一实现处。

    四条断言逐条对着 ADR-0019 实施清单第 1 项的验收栏：pending 可读；rejected 不可读；
    链末双行取最新；rejected 链末 ⇒ 无结论。

    测的是**内部**方法而不是某个契约函数：这条规则被态度、帖子三件套、证据、合规四处
    读取共用，挂在其中任何一个下面，另外三处的回归就没人认领了。契约函数那一层另有
    自己的断言，查的是「这个字段回填对了没有」，不是「哪一行算数」。
    """

    def test_pending_is_readable_with_nobody_approving_it(self, provider):
        """① 这就是本 ADR 的全部内容：模型写完即上线，没有人工门槛。

        `needs_review` 一并断言 —— 它是**模型自己举手**，不是「等人看」，同样可读
        （ADR-0019 §1 末句）。两者的差别只在页面上那一枚徽章（§2），不在可见性。
        """
        p = add_annotations(
            provider,
            [
                {"annotation_id": 1, "target_id": 11, "value": "positive"},
                {
                    "annotation_id": 2,
                    "target_id": 12,
                    "value": "negative",
                    "review_state": "needs_review",
                },
            ],
        )
        cur = p._current_annotations("attitude", "comment")
        assert cur[(11, OWN_CODE)]["value"] == "positive"
        assert cur[(11, OWN_CODE)]["review_state"] == "pending"
        assert cur[(12, OWN_CODE)]["value"] == "negative"
        assert cur[(12, OWN_CODE)]["review_state"] == "needs_review"

    def test_rejected_is_not_readable(self, provider):
        """② `--reject` 是这条管线仅剩的下线通道。

        取消批准门槛之后，「人看过并否决」是唯一还会改变可见性的人工动作。它要是不
        生效，一条标错的结论就再没有办法从页面上拿下来 —— 除非改库。
        """
        p = add_annotations(
            provider,
            [
                {"annotation_id": 1, "target_id": 11, "review_state": "rejected"},
                {"annotation_id": 2, "target_id": 12, "review_state": "approved"},
            ],
        )
        cur = p._current_annotations("attitude", "comment")
        assert (11, OWN_CODE) not in cur
        assert (12, OWN_CODE) in cur, "approved 当然照读 —— 被挡住的只有 rejected 一态"

    def test_the_newest_row_wins_when_two_sit_at_the_chain_end(self, provider):
        """③ 同一单元有两行都没被 supersede ⇒ 取 `created_at` 最新的那条。

        不是假想：重跑保护认的是 `(…, input_hash, run_id)` 唯一键，同一单元换一版
        prompt（输入指纹跟着变）就会在链末多出一行，谁也没 supersede 谁。不定序的话
        同一条评论在两次请求里会给出两种态度，而两次都「有据可查」。

        这里故意让**新的那行 id 更小**：按插入顺序取最后一条、或者按 id 取最大的，
        两种写法在这条数据上都会挑错。
        """
        p = add_annotations(
            provider,
            [
                {
                    "annotation_id": 1,
                    "target_id": 11,
                    "value": "negative",
                    "created_at": datetime(2026, 8, 25, 18, 0),
                    "input_hash": "prompt-v2",
                },
                {
                    "annotation_id": 2,
                    "target_id": 11,
                    "value": "positive",
                    "created_at": datetime(2026, 8, 25, 12, 0),
                    "input_hash": "prompt-v1",
                },
            ],
        )
        cur = p._current_annotations("attitude", "comment")
        assert cur[(11, OWN_CODE)]["value"] == "negative"
        assert cur[(11, OWN_CODE)]["annotation_id"] == 1

    def test_a_rejected_chain_end_leaves_the_unit_with_no_verdict(self, provider):
        """④ 链末被否决 ⇒ 这个单元当前没有结论，**不回退**到被它取代的那一行。

        回退听着体贴，实际是把一条人已经看过、判定为错的旧结论重新推上页面 —— 而否决
        这个动作在界面上没有任何痕迹，下一个人只会看到它自己回来了。页面上多一条
        「暂不可用」是可解释的，多一条已被否决的结论不是。

        规则 ① 与 ② 相乘就是这个结果，实现里没有第三条分支：rejected 的新行被 ② 滤掉，
        旧行被 ① 滤掉（它已经被 supersede 了），这个单元一行不剩。
        """
        p = add_annotations(
            provider,
            [
                {"annotation_id": 1, "target_id": 11, "value": "positive"},
                {
                    "annotation_id": 2,
                    "target_id": 11,
                    "value": "negative",
                    "review_state": "rejected",
                    "supersedes_id": 1,
                    "input_hash": "rerun",
                },
            ],
        )
        assert p._current_annotations("attitude", "comment") == {}

    def test_feed_and_comment_ids_live_in_separate_namespaces(self, provider):
        """`target_type` 是必需参数，不是可有可无的收窄。

        `annotations.target_id` 在 `feed` 与 `comment` 两个命名空间里各自从 1 开始编号。
        不区分就会拿一篇帖子的标注去回填一条恰好同号的评论 —— 查出来是有值的，值是
        别人的。
        """
        p = add_annotations(
            provider,
            [
                {
                    "annotation_id": 1,
                    "target_id": 1,  # feed 1，不是 comment 1（评论编号从 11 起）
                    "target_type": "feed",
                    "subject_code": "",  # 帖子级标注写 NO_SUBJECT
                    "kind": "post_type",
                    "value": "product",
                },
            ],
        )
        assert p._current_annotations("post_type", "feed")[(1, "")]["value"] == "product"
        assert p._current_annotations("post_type", "comment") == {}


class TestAiAndPriceSurfacesAreNone:
    """**库里一行标注都没有时**，要 AI 或行情源的契约函数是缺失态。

    这个类原来的断言是「一律 None」，而且无条件。在 ADR-0017 §4「只读 approved /
    corrected」的年代那是条恒真断言：模型写下的全是 `pending`，读取端一条也拿不到。
    ADR-0019 取消那道门槛之后它就从护栏变成了枷锁 —— 任何一处回填生效都会让它变红，
    而红的是对的那一边。第 8 项要的就是把它改成「无标注时为 None、有标注时为真值」。

    「有标注时」那一半在下面几个类里。留在这里的三组，红起来各是一件不同的事：

    1. `test_the_panels_without_a_writer_are_none` —— 对应的 kind 还没有写入方。
       它红了说明**新接了一条写入链路**，该补的是那个面板的真值断言，不是删这一条。
    2. `test_price_surfaces_have_no_source_at_all` —— 行情不在这个库里，和标注无关。
       ADR-0019 第 8 项明写「行情断言不变」：放开标注门槛不等于放开这两条。
    3. `test_the_annotation_driven_two_start_out_missing` —— 随标注走的那两个在
       空库上的样子。它们的真值断言在 `TestEvidence*` / `TestCompliance*`。
    """

    @pytest.mark.parametrize(
        "fn,args",
        [
            ("summary_for", (OWN_CODE, "d1")),
            ("themes_for", (OWN_CODE, "d1")),
            ("neg_cats_for", (OWN_CODE, "d1")),
            ("competitors_for", (OWN_CODE, "d1")),
            ("topics_for", (OWN_CODE, "d1")),
            ("kol_mentions_for", (OWN_CODE, "d1")),
        ],
    )
    def test_the_panels_are_none_before_any_attitude_annotation(self, provider, fn, args):
        """ADR-0020 接通了这几个面板的读路径，但「这只产品这个区间一条态度标注都没有」
        仍然是整块 None（暂不可用）——不是空列表，空列表是在说「标过了，什么都没有」。
        有标注时的真值断言在 `TestLayerBReadPath`。"""
        assert getattr(provider, fn)(*args) is None

    def test_hot_summaries_is_per_code_unavailable_before_annotation(self, provider):
        """热议总结整池一份：没标注的产品逐只给 `unavailable`，不是整块 None ——
        榜单每一行都要一个状态可渲染（PRD §4.1 S8）。"""
        hs = provider.hot_summaries("d1")
        assert set(hs) == {p["code"] for p in provider._products}
        assert all(v == {"status": "unavailable", "text": "数据暂不可用", "sample": None, "ok": False}
                   for v in hs.values())

    @pytest.mark.parametrize(
        "fn,args", [("candles_for", (OWN_CODE, "d1")), ("stages_for", (OWN_CODE, "d1"))]
    )
    def test_price_surfaces_have_no_source_at_all(self, provider, fn, args):
        """K 线与阶段观点要行情数据，瘦库里没有这张表，标注跑成什么样都不会让它们有值。"""
        assert getattr(provider, fn)(*args) is None

    def test_the_annotation_driven_two_start_out_missing(self, provider):
        """证据与合规：**没标注时**仍是缺失态，各自的形状不同。

        `evidence_for` 整块 `None`（「这只产品在这个区间一条态度标注都没有」）；
        `compliance_for` 有壳，因为面板按 `status` 分叉渲染 —— 而这里的 `status`
        本身就在说「这个区间的合规识别没跑过」。空列表配 `ok` 才是撒谎。
        """
        assert provider.evidence_for(OWN_CODE, "d1|sum", "negative", 5) is None
        assert provider.compliance_for(OWN_CODE, "d1") == {"status": "unavailable", "list": []}


# ── 标注落地之后（ADR-0019 第 2–5 项） ─────────────────────────────────


class TestAttitudeAggregation:
    """评论态度聚合（第 2 项）。按判定单元数正／负／中，`sampleSufficient` 走 `core/`。"""

    def test_an_annotated_product_gets_real_counts(self, provider):
        """验收栏逐字：「有标注的产品 `attitude` 不再为 `None`；没标注的仍为 `None`
        （不是 0）」。同一次 `pool()` 里三种情况都在，因为它们是同一个判据的三面。
        """
        p = add_annotations(
            provider,
            [
                {"annotation_id": 1, "target_id": 11, "value": "negative"},
                {"annotation_id": 2, "target_id": 12, "value": "positive"},
                # c13 在 f3（竞品讨论区），标的是竞品
                {
                    "annotation_id": 3,
                    "target_id": 13,
                    "subject_code": PEER_CODE,
                    "value": "neutral",
                },
            ],
        )
        by_code = {x["code"]: x for x in p.pool("d1")["list"]}

        own = by_code[OWN_CODE]["attitude"]
        assert (own["positive"], own["negative"], own["neutral"]) == (1, 1, 0)
        peer = by_code[PEER_CODE]["attitude"]
        assert (peer["positive"], peer["negative"], peer["neutral"]) == (0, 0, 1)

        idle = next(x for x in p.pool("d1")["list"] if x["code"] not in (OWN_CODE, PEER_CODE))
        assert idle["attitude"] is None, "没标过的产品是「不知道」，不是三个 0"

    def test_attitude_follows_the_subject_code_not_the_mentions(self, provider):
        """归属看 `annotations.subject_code`，**不按 `mentions` 摊开**。

        f1 的正文同时提到 3033 与 3032，c11 挂在 f1 下。模型判的是「这条评论对 3033
        是负面的」—— 它对 3032 什么都没说。摊过去就是替它表一次没表过的态，而页面上
        那条负面结论会带着完整的证据链，看不出是我们自己加的。
        """
        p = add_annotations(
            provider, [{"annotation_id": 1, "target_id": 11, "value": "negative"}]
        )
        by_code = {x["code"]: x for x in p.pool("d1")["list"]}
        assert by_code[OWN_CODE]["attitude"]["negative"] == 1
        assert by_code[PEER_CODE]["attitude"] is None

    def test_empty_buckets_of_an_annotated_product_are_zero_not_unknown(self, provider):
        """判据在**产品**这一层，不在桶上。

        标注过的产品，空桶里那个 0 是数出来的结论（这一桶确实没有被标过态度的评论）；
        没标过的产品整列 `None`。桶级判据会让一只标注覆盖率 5% 的产品的趋势图上
        95% 的桶显示「暂不可用」，那条线根本画不出来。
        """
        p = add_annotations(
            provider, [{"annotation_id": 1, "target_id": 11, "value": "negative"}]
        )
        pool = p.pool("d1")
        own = next(x for x in pool["list"] if x["code"] == OWN_CODE)
        assert own["buckets"][9]["negative"] == 1, "c11 挂在 f1（08-25 09:00）下"
        assert own["buckets"][0]["negative"] == 0
        idle = next(x for x in pool["list"] if x["code"] not in (OWN_CODE, PEER_CODE))
        assert idle["buckets"][9]["negative"] is None

    def test_sample_sufficiency_comes_from_the_low_sample_threshold(self, provider):
        """`sampleSufficient` ＝ `(正 + 负) >= 10`（PRD §3.5），判定在
        `core/attitude.py`（铁律 1），`providers/` 不许再写一遍这个数。

        分母里没有中性：这里十条正面刚好够，加进中性凑数就是让一只产品靠十条
        「今天除权」撑起一句主流倾向。
        """
        add_comments(provider, [{"comment_id": 100 + i} for i in range(10)])
        p = add_annotations(
            provider,
            [
                {"annotation_id": i + 1, "target_id": 100 + i, "value": "positive"}
                for i in range(10)
            ],
        )
        own = next(x for x in p.pool("d1")["list"] if x["code"] == OWN_CODE)
        assert own["attitude"]["positive"] == 10
        assert own["attitude"]["sampleSufficient"] is True

        few = add_annotations(
            make_sql_provider(), [{"annotation_id": 1, "target_id": 11, "value": "positive"}]
        )
        own = next(x for x in few.pool("d1")["list"] if x["code"] == OWN_CODE)
        assert own["attitude"]["sampleSufficient"] is False, "一条 ≠ 够，也 ≠ 不知道"

    def test_the_benchmark_window_reads_its_own_annotations(self, provider):
        """环比比的是两个区间各自的态度，所以基准区间也要走同一条读路径。

        c11 在当前区间（08-25）、c14 在基准区间（08-24）。两头都标过，态度环比才
        是一个数；少一头就是「数据暂不可用」—— 那也是对的，但测不出这条链路。
        """
        p = add_annotations(
            provider,
            [
                {"annotation_id": 1, "target_id": 11, "value": "positive"},
                {"annotation_id": 2, "target_id": 12, "value": "positive"},
                {"annotation_id": 3, "target_id": 14, "value": "positive"},
            ],
        )
        b = p.benchmark(OWN_CODE, "d1")
        assert b["base"]["attitude"]["positive"] == 1
        assert b["positive"]["text"] == "+1（+100.0%）"

    def test_the_company_headline_stays_unknown_while_any_product_is_unannotated(self, provider):
        """板块总览顶部那张「积极内容」卡：61 只自家产品里有一只没标过 ⇒ 合计未知。

        `_add_all` 的 None 传染。把没标过的当 0 加进去，那个合计看着完全正常 ——
        而它系统性偏低，低多少取决于标注覆盖率，页面上没有任何迹象。
        """
        p = add_annotations(
            provider, [{"annotation_id": 1, "target_id": 11, "value": "positive"}]
        )
        assert p.pool("d1")["own"]["pos"] is None
        assert p.pool("d1")["own"]["dPos"]["text"] == "数据暂不可用"


class TestPostAnnotations:
    """帖子三件套（第 3 项）：`post_type` / `direction` / `summary` 回填官号与 KOL 帖子。"""

    TRIPLE = [
        {"annotation_id": 1, "target_id": 1, "target_type": "feed", "subject_code": "",
         "kind": "post_type", "value": "promo"},
        {"annotation_id": 2, "target_id": 1, "target_type": "feed", "subject_code": "",
         "kind": "summary", "value": "官号解释了费率与跟踪误差。"},
        {"annotation_id": 3, "target_id": 1, "target_type": "feed", "subject_code": "",
         "kind": "direction", "value": "add"},
    ]

    def test_the_official_post_shows_its_real_type_and_summary(self, provider):
        p = add_annotations(provider, self.TRIPLE)
        post = p.official_posts("d1")[0]
        assert (post["postType"], post["typeLabel"]) == ("promo", "产品推介")
        assert post["hasSummary"] is True
        assert post["summary"] == "官号解释了费率与跟踪误差。"
        assert (post["direction"], post["directionLabel"]) == ("add", "加仓")
        assert post["directionPending"] is False
        assert post["dir"]["label"] == "加仓"
        # 校准置信度整列为 NULL（ADR-0017 §4）。照发 None，不伪造一个数 —— 前端的
        # 0.7 阈值在校准概率存在之前不生效（ADR-0019 §2）。
        assert post["confidence"] is None

    def test_the_badge_state_rides_along_so_the_front_end_can_label_it(self, provider):
        """`reviewState` 是徽章的唯一判据（ADR-0019 §2）。它必须**发出来**：
        前端认的是 `needs_review` 这个字符串，不是置信度，也不是有没有摘要。"""
        rows = [dict(r) for r in self.TRIPLE]
        rows[0]["review_state"] = "needs_review"
        p = add_annotations(provider, rows)
        assert p.official_posts("d1")[0]["reviewState"] == "needs_review"

    def test_an_unannotated_post_keeps_the_whole_block_missing(self, provider):
        """f1 标了、f2（KOL 那篇）没标 —— 同一次请求里两种状态并存。

        `reviewState` 是 `None` 而不是 `pending`：`pending` 会让前端给一篇**从没跑过
        标注**的帖子挂上「AI 生成 · 可追溯原文」，而原文里一个字的判定依据都没有。
        """
        p = add_annotations(provider, self.TRIPLE)
        kol_post = p.kol_impact("d1")["posts"][0]
        assert kol_post["url"].endswith("/2"), "标的是 f1，这里查的是 f2"
        for k in ("postType", "typeLabel", "summary", "direction", "fullText", "reviewState"):
            assert kol_post[k] is None, k

    def test_the_kol_post_reads_the_same_way(self, provider):
        """同一条读路径喂两个页面（`_post_ai`）。f2 是 KOL 那篇。"""
        p = add_annotations(
            provider,
            [
                {"annotation_id": 1, "target_id": 2, "target_type": "feed",
                 "subject_code": "", "kind": "post_type", "value": "market"},
            ],
        )
        post = p.kol_impact("d1")["posts"][0]
        assert (post["postType"], post["typeLabel"]) == ("market", "行情解读")
        # 只写了类型 ⇒ 方向与摘要是 None（还没标），不是 False（一个确切的「没有」）
        assert post["summary"] is None and post["hasSummary"] is None
        assert post["direction"] is None and post["hasDir"] is None

    def test_an_unknown_post_type_raises_instead_of_falling_back_to_other(self, provider):
        """库里写进了一个枚举外的类型 ⇒ KeyError，不悄悄显示成「其他」。

        「其他」是模型可以做出的一个判断，拿它兜底就是替模型作了一个它没作的判断，
        而页面上和真的判成「其他」一模一样。
        """
        p = add_annotations(
            provider,
            [
                {"annotation_id": 1, "target_id": 1, "target_type": "feed",
                 "subject_code": "", "kind": "post_type", "value": "tiktok_dance"},
            ],
        )
        with pytest.raises(KeyError):
            p.official_posts("d1")


class TestEvidenceLocatesTheOriginalText:
    """证据（第 4 项）：`evidenceIdx` / `typeEvidence` / `evidenceFor`。

    验收栏逐字：「证据侧栏引文能在原文里逐字定位」。所以这里的断言都拿原文现算偏移，
    不写死数字 —— 写死的那个数会在有人改一个字之后静默错位，而错位的症状是「判定
    依据」高亮到半句不相干的话上，不报错。
    """

    def test_the_type_evidence_points_at_the_sentence_it_came_from(self, provider):
        p = add_annotations(
            provider,
            [
                {"annotation_id": 1, "target_id": 1, "target_type": "feed",
                 "subject_code": "", "kind": "post_type", "value": "promo"},
            ],
        )
        start = FEED_TEXT.index("费率")  # 第二句的句首
        add_evidence(
            p,
            [
                {"evidence_id": 1, "annotation_id": 1, "source_target_type": "feed",
                 "source_target_id": 1, "start_offset": start,
                 "end_offset": start + 6, "quote_text": FEED_TEXT[start : start + 6]},
            ],
        )
        post = p.official_posts("d1")[0]
        assert post["fullText"] == ["这只 ETF 我今天加了一手。", "费率比同类低，打算长期拿着。"]
        assert post["evidenceIdx"] == 1
        assert post["typeEvidence"] == post["fullText"][1]
        assert post["typeEvidence"] in FEED_TEXT, "逐字定位：引文必须在原文里找得到"

    def test_no_evidence_row_means_index_minus_one_not_zero(self, provider):
        """定位不到 ⇒ `-1` ＋ 空串（设计源 `radar-data.js:1244` 逐字），不是第 0 句。

        退成第 0 句会让侧栏高亮一句**可能根本不相干**的话，而徽章那边照样写着
        「可追溯原文」—— 那是一句指不到原文的承诺（ADR-0019 §2）。
        """
        p = add_annotations(
            provider,
            [
                {"annotation_id": 1, "target_id": 1, "target_type": "feed",
                 "subject_code": "", "kind": "post_type", "value": "promo"},
            ],
        )
        post = p.official_posts("d1")[0]
        assert post["evidenceIdx"] == -1
        assert post["typeEvidence"] == ""
        assert post["fullText"], "原文照发 —— 缺的是「哪一句」，不是原文"

    def test_evidence_for_returns_the_comments_behind_the_verdict(self, provider):
        p = add_annotations(
            provider,
            [
                {"annotation_id": 1, "target_id": 11, "value": "negative"},
                {"annotation_id": 2, "target_id": 12, "value": "positive"},
            ],
        )
        items = p.evidence_for(OWN_CODE, "d1|sum", "negative", 5)
        assert [x["id"] for x in items] == [f"ev-11-{OWN_CODE}"]
        assert items[0]["excerpt"] == COMMENT_TEXT[11]
        assert items[0]["authorType"] == "普通散户"
        assert items[0]["productCodes"] == [OWN_CODE]
        assert items[0]["sourceUrl"] == "https://www.futunn.com/post/1"

    def test_a_polarity_with_no_hits_is_empty_not_missing(self, provider):
        """标过了、这个极性一条都没有 ⇒ 空列表（「暂无相关内容」）。

        与整块 `None`（「暂不可用」）不可互换：前者是查过的结论，后者是没查过。
        """
        p = add_annotations(
            provider, [{"annotation_id": 1, "target_id": 11, "value": "negative"}]
        )
        assert p.evidence_for(OWN_CODE, "d1|sum", "positive", 5) == []
        assert p.evidence_for(PEER_CODE, "d1|sum", "negative", 5) is None

    def test_a_comment_judged_on_two_products_carries_both_codes(self, provider):
        """同一条评论可以同时评价两只 ETF（runbook §10.1），证据卡要把它们都带上。"""
        p = add_annotations(
            provider,
            [
                {"annotation_id": 1, "target_id": 11, "value": "negative"},
                {"annotation_id": 2, "target_id": 11, "subject_code": PEER_CODE,
                 "value": "negative"},
            ],
        )
        items = p.evidence_for(OWN_CODE, "d1|sum", "negative", 5)
        assert items[0]["productCodes"] == [OWN_CODE, PEER_CODE]


class TestComplianceFourStates:
    """需合规关注（第 5 项）。PRD §4.2 P10 的四态，一个都不能合并。"""

    HIT = {
        "annotation_id": 1,
        "target_id": 11,
        "kind": "compliance",
        "value": {"risk_tags": ["regulatory_complaint"], "rationale": "评论里提到要去投诉。"},
    }

    def test_a_peer_product_is_na_not_empty(self, provider):
        """同业产品不纳入识别 —— 字段**不适用**，不是「查了没查到」。"""
        p = add_annotations(provider, [self.HIT])
        assert p.compliance_for(PEER_CODE, "d1") == {"status": "na", "list": []}

    def test_nothing_scanned_in_the_window_is_unavailable(self, provider):
        """库里没有覆盖率记录，「扫没扫过」只能从「这个区间有没有 compliance 行」推。

        区间内一条都没有 ⇒ 对任何产品都不能说「查过了，没有」。这个近似偏在保守
        一侧，是有意的。
        """
        assert provider.compliance_for(OWN_CODE, "d1")["status"] == "unavailable"

    def test_other_products_annotations_do_not_imply_this_product_was_scanned(self, provider):
        p = add_annotations(provider, [self.HIT])
        assert p.compliance_for(OTHER_OWN_CODE, "d1") == {"status": "unavailable", "list": []}

    def test_v2_empty_tags_are_not_risk_items(self, provider):
        p = add_annotations(provider, [{**self.HIT, "value": {"tags": [], "rationale": None}}])
        assert p.compliance_for(OWN_CODE, "d1") == {"status": "empty", "list": []}
        assert p.pool("d1")["complianceCount"][OWN_CODE] == 0

    def test_v2_tags_keep_the_actual_risk_label(self, provider):
        p = add_annotations(provider, [{**self.HIT, "value": {"tags": ["regulatory_complaint"],
                                                           "rationale": "明确投诉意图"}}])
        hit = p.compliance_for(OWN_CODE, "d1")["list"][0]
        assert hit["riskTags"] == ["regulatory_complaint"]
        assert hit["riskLabels"] == ["监管举报"]

    def test_a_hit_comes_back_with_its_rationale_and_a_locatable_quote(self, provider):
        p = add_annotations(provider, [self.HIT])
        start = COMMENT_TEXT[11].index("不太划算")
        add_evidence(
            p,
            [
                {"evidence_id": 1, "annotation_id": 1, "source_target_id": 11,
                 "start_offset": start, "end_offset": start + 4,
                 "quote_text": COMMENT_TEXT[11][start : start + 4]},
            ],
        )
        r = p.compliance_for(OWN_CODE, "d1")
        assert r["status"] == "ok" and len(r["list"]) == 1
        hit = r["list"][0]
        assert hit["riskTags"] == ["regulatory_complaint"]
        assert hit["riskLabels"] == ["监管举报"]
        assert hit["detectionRationale"] == "评论里提到要去投诉。"
        assert hit["excerpt"] == "不太划算"
        assert hit["excerpt"] in COMMENT_TEXT[11], "引文要能在原文里逐字找到"
        assert hit["sourceKind"] == "评论"

    def test_the_badge_is_constant_no_matter_what_the_review_state_says(self, provider):
        """「AI 识别 · 待人工确认」**恒定**（PRD §4.2 P10 逐字，ADR-0019 §2 表末行）。

        合规是唯一一类结论，不论模型多有把握都要人再看一眼 —— 所以这枚徽章不随
        `review_state` 变。被 `approved` 过的那一条也照样写「待人工确认」。
        """
        p = add_annotations(provider, [{**self.HIT, "review_state": "approved"}])
        hit = p.compliance_for(OWN_CODE, "d1")["list"][0]
        assert (hit["reviewState"], hit["reviewLabel"]) == ("ai_pending", "AI 识别 · 待人工确认")

    def test_a_post_level_hit_says_so(self, provider):
        """合规命中可以落在帖子上，不只是评论 —— 两个命名空间各查一次。"""
        p = add_annotations(
            provider,
            [
                {"annotation_id": 1, "target_id": 1, "target_type": "feed",
                 "kind": "compliance",
                 "value": {"risk_tags": ["mobilization"], "rationale": "号召大家一起去。"}},
            ],
        )
        hit = p.compliance_for(OWN_CODE, "d1")["list"][0]
        assert hit["sourceKind"] == "帖子"
        assert hit["riskLabels"] == ["煽动扩散"]

    def test_the_pool_counts_hits_per_product(self, provider):
        """`pool().complianceCount`：`ok` 给条数、`empty` 给 0、`na` 与 `unavailable`
        给 **None**。设计源那句 `|| 0` 把「没扫过」读成「零条」，板块总览顶部就会多
        报一个它并不知道的数。"""
        p = add_annotations(provider, [self.HIT])
        pool = p.pool("d1")
        assert pool["complianceCount"][OWN_CODE] == 1
        assert pool["complianceCount"][OTHER_OWN_CODE] is None
        assert pool["complianceCount"][PEER_CODE] is None, "同业产品不适用，不是零条"
        assert pool["own"]["risk"] is None

    def test_the_pool_total_goes_unknown_when_nothing_was_scanned(self, provider):
        """一只都没扫过 ⇒ 合计未知，不是 0。"""
        pool = provider.pool("d1")
        assert set(pool["complianceCount"].values()) == {None}
        assert pool["own"]["risk"] is None


# ── 主数据 ─────────────────────────────────────────────────────────────


def test_master_is_served_even_with_an_empty_database():
    """产品池是客户维护的主数据，不依赖有没有帖子。"""
    p = make_sql_provider(anchor=None)
    m = p.master()
    assert len(m["products"]) == 120
    assert len(m["officials"]) == 20
    assert len(m["kols"]) == 32


def test_range_key_coverage(provider):
    """五个预设都能算，桶数按 PRD §3.1 的粒度规则。"""
    assert len(provider.build_range("d1")["buckets"]) == 24  # 小时
    assert len(provider.build_range("d2")["buckets"]) == 48
    assert len(provider.build_range("d7")["buckets"]) == 7  # 日
    assert len(provider.build_range("d14")["buckets"]) == 14
    assert len(provider.build_range("d30")["buckets"]) == 5  # 周


def test_date(provider):
    """锚点是 2026-08-25，不是今天 —— 防止有人把 `date.today()` 加回来。"""
    assert provider.build_range("d1")["from"] == ANCHOR
    assert date.fromisoformat(ANCHOR) < date(2026, 9, 10)


# ── 库在进程活着的时候变了 ─────────────────────────────────────────────


class TestRefresh:
    """`refresh()` —— provider 是进程单例，它必须能发现脚下的库被重写了。

    这不是性能问题。compose 把 backend 和 worker 一起拉起来，库那时是空的：
    没有这一下，`__init__` 读到的空 meta 会被沿用到进程结束，导入跑完之后页面
    依旧整屏「暂不可用」，直到有人重启容器。
    """

    def test_an_empty_database_recovers_once_the_import_lands(self):
        p = make_sql_provider(anchor=None)
        assert p.build_range("d7") is None, "没锚点 ⇒ 区间未知，不拿今天兜底"

        with p._engine.begin() as conn:
            conn.execute(insert(meta_kv), [{"k": "anchor", "v": ANCHOR}])
        assert p.refresh() is True
        assert p.build_range("d7")["to"] == ANCHOR, "导入跑完，页面自己好起来"

    def test_a_new_etl_generation_drops_the_scan_cache(self, provider):
        provider.pool("d1")
        assert provider._cache, "扫描结果本来就该缓存 —— 一次全池扫描按秒计"

        with provider._engine.begin() as conn:
            conn.execute(insert(meta_kv), [{"k": "etl_generation", "v": "feeds=9"}])
        assert provider.refresh() is True
        assert provider._cache == {}

    def test_an_unchanged_database_keeps_the_cache(self, provider):
        provider.pool("d1")
        before = dict(provider._cache)
        assert provider.refresh() is False
        assert provider._cache == before, (
            "meta 没变就一行不动：缓存是这个 provider 唯一的性能来源，"
            "不能因为一次探测就白丢"
        )

    def test_the_anchor_moves_with_the_meta_row(self, provider):
        assert provider.build_range("d1")["to"] == ANCHOR
        with provider._engine.begin() as conn:
            conn.execute(delete(meta_kv).where(meta_kv.c.k == "anchor"))
            conn.execute(insert(meta_kv), [{"k": "anchor", "v": "2026-08-26"}])
        assert provider.refresh() is True
        assert provider.build_range("d1")["to"] == "2026-08-26"


def test_demo_provider_also_answers_refresh():
    """`get_provider()` 每次取用都调 `refresh()`，两个 provider 都得有这个方法 ——
    在接缝处写 `isinstance(p, SqlProvider)` 会把实现细节漏回上层（ADR-0001）。"""
    from providers.demo import DemoProvider

    assert DemoProvider().refresh() is False
