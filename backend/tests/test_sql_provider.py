"""`providers/sql.py` —— 真实数据 provider 的读路径。

**不接真库。** 真库在 `%LOCALAPPDATA%\\futu-radar\\radar.db`，4.9 GB，且含真实用户昵称／
IP 归属地／个人简介（ADR-0008）—— 测试不该依赖一个不进 git、每台机器都不一样、还带 PII
的文件。这里用 `radar_db.schema` 在内存里建同一套表，塞十来行编出来的数据。

schema 是共用的那一份，所以「列名对不上」这类错误照样能在这里被抓到。抓不到的是
「dump 里那一列的实际含义和我们以为的不一样」—— 那种事只能靠对真库的实测，模块头
记的那三条就是这么来的。

用编出来的数字有个额外好处：期望值可以手算写在断言旁边，读的人不用信任何一层实现。
"""

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from core.calendar import parse_anchor

# 先导入 provider：它会把仓库根塞进 sys.path，下面的 radar_db 才 import 得到
# （见 providers/sql.py 顶部的 sys.path 守卫）。
from providers.sql import SqlProvider
from radar_db import create_all
from radar_db.schema import comments, feeds, mentions, meta_kv

ANCHOR = "2026-08-25"  # 真实数据的锚点：最近一个完整自然日
MASTER = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "demo" / "master.json").read_text(
        encoding="utf-8"
    )
)
OWN_CODE = "3033"  # 恒生科技指數ETF，CSOP 南方东英
PEER_CODE = "3032"  # 同名竞品，恒生投资
OFFICIAL_FULL = "恒生投資管理有限公司"  # 有 comps ⇒ 发行商官号
KOL_NAME = "孫子的末代傳人"  # master.json 里 active 的 KOL


def _feed(feed_id, day, hour, uid, name, likes, n_comments, shares, browse=None):
    return {
        "feed_id": feed_id,
        "code": OWN_CODE,  # 冗余列，读路径一律走 mentions，这里只是不能为空
        "posted_at": datetime(2026, 8, day, hour, 0),
        "feed_type": 1,
        "author_uid": uid,
        "author_name": name,
        "title": None,
        "content": "正文",
        "like_count": likes,
        "comment_count": n_comments,
        "image_count": 0,
        "share_count": shares,
        "browse_count": browse,
        "raw_json_broken": shares is None,
    }


def _provider(broken_share=True, anchor=ANCHOR):
    """内存库 ＋ 一小撮数据。

    `broken_share=True` 时 f2 的 `share_count` 是 NULL —— 模拟源库 raw_json 被 TEXT 列
    截断的那 0.03% 行。它是本文件里好几条断言的起因，不是随手写的。
    """
    p = SqlProvider("sqlite://")
    create_all(p._engine)

    rows_feeds = [
        # 08-25 09:00 官号发帖，正文同时提到自家与竞品
        _feed(1, 25, 9, "u1", OFFICIAL_FULL, 10, 4, 2),
        # 08-25 09:30 → 同一个小时桶。转发数未知
        _feed(2, 25, 9, "u2", KOL_NAME, 0, 1, None if broken_share else 0, browse=888),
        # 08-25 14:00 竞品讨论区
        _feed(3, 25, 14, "u3", "路人甲", 5, 2, 0),
        # 08-24：基准区间那天
        _feed(4, 24, 11, "u4", "路人乙", 100, 10, 1),
    ]
    rows_comments = [
        {"comment_id": 11, "feed_id": 1, "author_uid": "u9", "like_count": 3},
        {"comment_id": 12, "feed_id": 1, "author_uid": "u1", "like_count": 0},
        {"comment_id": 13, "feed_id": 3, "author_uid": "u9", "like_count": 1},
    ]
    rows_mentions = [
        {"feed_id": 1, "code": OWN_CODE, "source": "anchor", "in_pool": True},
        {"feed_id": 1, "code": PEER_CODE, "source": "body", "in_pool": True},
        # 池外标的：读路径必须滤掉，否则 KeyError 或凭空多出一只产品
        {"feed_id": 1, "code": "0700", "source": "body", "in_pool": False},
        {"feed_id": 2, "code": OWN_CODE, "source": "anchor", "in_pool": True},
        {"feed_id": 3, "code": PEER_CODE, "source": "anchor", "in_pool": True},
        {"feed_id": 4, "code": OWN_CODE, "source": "anchor", "in_pool": True},
    ]
    with p._engine.begin() as conn:
        conn.execute(feeds.insert(), rows_feeds)
        conn.execute(comments.insert(), rows_comments)
        conn.execute(mentions.insert(), rows_mentions)
        if anchor:
            conn.execute(
                meta_kv.insert(),
                [{"k": "anchor", "v": anchor}, {"k": "anchor_ts", "v": f"{anchor} 23:59:59"}],
            )

    # provider 在 __init__ 里就把 meta 读进来了（生产环境是先导库后起服务）。内存库只能
    # 由 provider 自己那个 engine 建，顺序反了，所以这里重放 __init__ 的最后两行。
    p._meta = p._read_meta()
    p._anchor = parse_anchor(p._meta.get("anchor"))
    return p


@pytest.fixture
def provider():
    return _provider()


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
        p = _provider(anchor=None)
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
        """态度／预警／负面提及／合规数全部要 AI 标注（ADR-0010）。

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
        p = _provider(broken_share=False)
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
        """基准区间切一样多的桶，趋势图悬停要按同位比。"""
        b = provider.benchmark(OWN_CODE, "d1")
        assert len(b["buckets"]) == 24
        assert [x["i"] for x in b["buckets"]] == list(range(24))

    def test_unknown_product(self, provider):
        assert provider.benchmark("0700", "d1") is None


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
        assert [x["code"] for x in m["list"]] == [PEER_CODE, OWN_CODE]  # 并列时 code 升序
        assert m["etfCount"] == 2
        assert m["own"] == 1 and m["peer"] == 1
        assert all(x["count"] == x["posts"] for x in m["list"])

    def test_etf_mentions_accepts_either_name_form(self, provider):
        assert provider.etf_mentions_for(OFFICIAL_FULL, "d1")["etfCount"] == 2
        assert provider.etf_mentions_for("查无此号", "d1") is None


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
    p = _provider(anchor=None)
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
