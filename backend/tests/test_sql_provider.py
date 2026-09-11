"""`providers/sql.py` —— 真实数据 provider 的读路径。

**不接真库。** 真库在 `%LOCALAPPDATA%\\futu-radar\\radar.db`，4.9 GB，且含真实用户昵称／
IP 归属地／个人简介（ADR-0008）—— 测试不该依赖一个不进 git、每台机器都不一样、还带 PII
的文件。这里用 `radar_db.schema` 在内存里建同一套表，塞十来行编出来的数据。

schema 是共用的那一份，所以「列名对不上」这类错误照样能在这里被抓到。抓不到的是
「dump 里那一列的实际含义和我们以为的不一样」—— 那种事只能靠对真库的实测，模块头
记的那三条就是这么来的。

用编出来的数字有个额外好处：期望值可以手算写在断言旁边，读的人不用信任何一层实现。
"""

from datetime import date

import pytest
from sqlalchemy import delete, insert
from radar_db.schema import meta_kv
from sql_fixture import (
    ANCHOR,
    KOL_NAME,
    MASTER,
    OFFICIAL_FULL,
    OWN_CODE,
    PEER_CODE,
    make_sql_provider,
)

from providers.sentinel import MISSING


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


class TestUnknownSharesPropagate:
    """转发数未知会一路传染到公司级 KPI —— 这是当前设计的**已知后果**，不是 bug。

    源库 raw_json 被 TEXT 列截断的行拿不到 `share_count`。热度公式里有转发项，所以那只
    产品的热度是未知；`own.heat` 是所有自家产品热度之和，于是整张板块总览的标题卡片显示
    「数据暂不可用」—— 哪怕 61 只里只有 1 只缺数。

    传染是对的（把未知当 0 会让热度静默偏低且无人知晓），但代价集中在一个很显眼的位置。
    真库里 d7 窗口 31,054 篇有 9 篇如此，影响 4 只产品。要改口径就改这里的断言。
    """

    def test_the_product_with_the_broken_row_goes_unknown(self, provider):
        item = next(x for x in provider.pool("d1")["list"] if x["code"] == OWN_CODE)
        assert item["shares"] is None
        assert item["discussionHeat"] is None
        assert item["interactions"] is None
        # 但同一只产品的评论量、点赞、活跃账号都还是好的 —— 只污染依赖转发的那几项
        assert item["comments"] == 5

    def test_the_company_wide_headline_goes_unknown_with_it(self, provider):
        own = provider.pool("d1")["own"]
        assert own["heat"] is None
        assert own["dHeat"]["text"] == "数据暂不可用"
        assert own["dHeat"]["abs"] is None and own["dHeat"]["pct"] is None
        assert own["count"] == sum(1 for p in MASTER["products"] if p["ownership"] == "own")

    def test_without_the_broken_row_everything_is_a_number(self):
        """同一批数据，只把那一行的转发数补上 —— 对照组。"""
        p = make_sql_provider(broken_share=False)
        item = next(x for x in p.pool("d1")["list"] if x["code"] == OWN_CODE)
        # 评论 5 + 0.3×13 + 转发 2 = 10.9 → 11
        assert item["discussionHeat"] == 11
        assert item["shares"] == 2
        # 其余自家产品热度都是 0（真的没人发），所以公司级合计就是这 11
        assert p.pool("d1")["own"]["heat"] == 11


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

    def test_unknown_current_value_makes_the_delta_unavailable(self, provider):
        """当前热度未知 ⇒ 环比不是 0%，是「数据暂不可用」。"""
        assert provider.benchmark(OWN_CODE, "d1")["heat"]["text"] == "数据暂不可用"

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
        assert nine["heat"] is None  # f2 转发未知
        assert s[0]["heat"] == 0 and s[0]["mentions"] == 0
        assert all(x["positive"] is None for x in s)

    def test_daily_has_real_counts_and_no_prices(self, provider):
        """价格四项没有数据源 —— 写 None，不拿收盘价占位。"""
        d = provider.daily_for(OWN_CODE)
        assert len(d) == 60
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


class TestAiAndPriceSurfacesAreNone:
    """要 AI 标注或行情源的契约函数，本期一律 `None` → `unavailable` → 「暂不可用」。"""

    @pytest.mark.parametrize(
        "fn,args",
        [
            ("hot_summaries", ("d1",)),
            ("summary_for", (OWN_CODE, "d1")),
            ("themes_for", (OWN_CODE, "d1")),
            ("neg_cats_for", (OWN_CODE, "d1")),
            ("competitors_for", (OWN_CODE, "d1")),
            ("compliance_for", (OWN_CODE, "d1")),
            ("topics_for", (OWN_CODE, "d1")),
            ("kol_mentions_for", (OWN_CODE, "d1")),
            ("evidence_for", (OWN_CODE, "d1", "neg", 5)),
            ("candles_for", (OWN_CODE, "d1")),
            ("stages_for", (OWN_CODE, "d1")),
        ],
    )
    def test_returns_none(self, provider, fn, args):
        assert getattr(provider, fn)(*args) is None


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
