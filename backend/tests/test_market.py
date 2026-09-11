"""市场域基础组 —— pool / ranks / benchmark（PRD §5）。

这一组是**铁律 3** 的验收现场：「全市场排名由后端算好，前端只过滤显示」。
下面的断言分三类：

1. 排名与色阶标尺来自全市场，不随任何筛选变（`test_rank_*`、`test_global_max_*`）。
2. delta 是内嵌形状不是端点，且缺输入时整体为「暂不可用」（`test_delta_*`）。
3. 同一个数只有一个来源 —— ranks 不带 list、pool 不带 rankMap（`test_*_is_not_duplicated`）。
"""

import pytest

RANGE_KEYS = ["d1", "d2", "d7", "d14", "d30"]


def pool(client, qs="?range=d7"):
    return client.get("/api/v1/pool" + qs).get_json()


def ranks(client, qs="?range=d7"):
    return client.get("/api/v1/ranks" + qs).get_json()


def bench(client, code, qs="?range=d7"):
    return client.get(f"/api/v1/products/{code}/benchmark" + qs).get_json()


@pytest.fixture
def a_code(client):
    """池里的第一只产品。写死代码会让 fixture 一换就红。"""
    return pool(client)["data"]["list"][0]["code"]


# ── 信封与区间参数 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("key", RANGE_KEYS)
def test_all_five_presets_are_available(client, key):
    for body in (pool(client, f"?range={key}"), ranks(client, f"?range={key}")):
        assert set(body) == {"status", "data"}
        assert body["status"] == "ok"


def test_default_range_is_d7(client):
    """不传 ?range= 用 DEFAULT_KEY=d7，与设计源一致。"""
    assert pool(client, "")["data"] == pool(client, "?range=d7")["data"]
    assert ranks(client, "")["data"] == ranks(client, "?range=d7")["data"]


def test_unknown_range_is_404_everywhere(client, a_code):
    """静默回落到 d7 会让前端拿到另一段时间的池，而页面上没有任何迹象说明这件事。"""
    assert client.get("/api/v1/pool?range=d99").status_code == 404
    assert client.get("/api/v1/ranks?range=d99").status_code == 404
    assert client.get(f"/api/v1/products/{a_code}/benchmark?range=d99").status_code == 404


def test_unknown_product_is_404_not_an_empty_benchmark(client):
    """池里没这只产品 → 404。**不是** 200 + 一堆 null。

    200 + null 的意思是「有这只产品，但基准期取不到数」，页面会渲染「数据暂不可用」并
    继续把它当成一只在监控范围内的产品。代码写错和产品不在池里，得长得不一样。
    """
    r = client.get("/api/v1/products/0000/benchmark?range=d7")
    assert r.status_code == 404
    assert "error" in r.get_json()


def test_deterministic(client):
    assert ranks(client) == ranks(client)


# ── 铁律 3：排名与色阶标尺来自全市场 ────────────────────────────────────


def test_ranks_cover_the_whole_active_pool(client):
    """排名基于**完整活跃 ETF 池**（PRD §3.8）：池里每一只都得有名次，且名次是 1..N 的排列。

    少一只就说明有人在某处先筛后排了。
    """
    codes = [o["code"] for o in pool(client)["data"]["list"]]
    rk = ranks(client)["data"]
    assert rk["total"] == len(codes)
    assert set(rk["map"]) == set(codes)
    assert sorted(rk["map"].values()) == list(range(1, len(codes) + 1))


def test_rank_order_follows_comment_volume(client):
    """名次口径是**区间评论量降序**（PRD §3.8），不是热度、不是提及量。"""
    data = pool(client)["data"]
    rk = ranks(client)["data"]["map"]
    by_rank = sorted(data["list"], key=lambda o: rk[o["code"]])
    comments = [o["comments"] for o in by_rank]
    assert comments == sorted(comments, reverse=True)


def test_ranks_do_not_take_filter_parameters(client):
    """排名端点**不接受**板块／范围／搜索参数——接了就迟早有人传。

    PRD §4.1 逐字：「板块、范围、搜索与开关只改变可见范围；排名与色阶标尺始终来自
    全市场」。后端不认识「当前筛了哪个板块」，也不需要认识：多余的查询串被忽略，
    同一区间的排名永远是同一份。
    """
    base = ranks(client, "?range=d7")["data"]
    for noise in ("&sector=hk", "&scope=own", "&q=3033", "&onlyNeg=1"):
        assert ranks(client, "?range=d7" + noise)["data"] == base


def test_rank_of_one_product_is_stable_across_sectors(client):
    """同一只产品的名次在任何筛选组合下都是同一个数字。

    这是「第 12 名」能不能对外引用的全部前提。前端筛选只改变可见范围，名次从这里来。
    """
    rk = ranks(client)["data"]["map"]
    pool_list = pool(client)["data"]["list"]
    hk = [o["code"] for o in pool_list if o["sector"] == "hk"]
    own = [o["code"] for o in pool_list if o["ownership"] == "own"]
    for code in set(hk) & set(own):
        assert rk[code] == ranks(client)["data"]["map"][code]
    # 板块内名次不是 1..len(hk)：它们是全市场名次，中间必然有空档。
    assert sorted(rk[c] for c in hk) != list(range(1, len(hk) + 1))


def test_global_max_is_the_whole_market_peak(client):
    """热力图色阶标尺 = 全池的桶峰值，不是可见集的。

    按可见集重算的话，筛掉头部产品会让剩下的格子集体变深 —— 看的人会以为讨论量涨了。
    """
    data = pool(client)["data"]
    assert data["globalMax"] == max(o["maxBucket"] for o in data["list"])


# ── 同一个数只有一个来源 ────────────────────────────────────────────────


def test_ranks_does_not_ship_the_product_list(client):
    """`/ranks` 只发 map + total。

    设计源的 `ranks().list` 与 `pool().list` 是同一批对象；两处都发，等于同一份 120 只
    产品的观测在线上跑两遍（d2 单份 1.1 MB），而前端一处都没读过它。
    """
    assert set(ranks(client)["data"]) == {"map", "total"}


def test_pool_does_not_ship_a_second_rank_map(client):
    """`/pool` 不带 `rankMap`。

    设计源的 `pool().rankMap` 与 `ranks().map` 逐字相同。铁律 3 的那个数只能有一个来源
    —— 两份一旦不一致，页面上会同时出现两个「第 12 名」，而看的人无从判断哪个对。
    """
    assert "rankMap" not in pool(client)["data"]


# ── delta：内嵌形状，不是端点 ───────────────────────────────────────────


def test_delta_is_not_an_endpoint(client):
    """环比没有自己的端点，它是内嵌在它所描述的那个数旁边的形状。"""
    assert client.get("/api/v1/delta?cur=10&base=8").status_code == 404


DELTA_FIELDS = ["mentions", "comments", "interactions", "likes", "shares", "heat",
                "positive", "negative", "neutral", "accounts"]


@pytest.mark.parametrize("field", DELTA_FIELDS)
def test_benchmark_delta_shape(client, a_code, field):
    """每个环比字段都是 `{text, short, abs, pct, dir}`。

    `text`/`short` 由后端给：PRD §3.1 的长文案「数据暂不可用」与 §3.6 的短徽章
    「暂不可用」**不可互换**，把这个选择留给五个屏各自决定，迟早会有一个选错。
    """
    d = bench(client, a_code)["data"][field]
    assert set(d) == {"text", "short", "abs", "pct", "dir"}
    assert d["dir"] in (-1, 0, 1)


def test_benchmark_carries_the_baseline_observation(client, a_code):
    """`base` 是基准期的完整观测，趋势图要拿它画基准线。"""
    base = bench(client, a_code)["data"]["base"]
    assert base["code"] == a_code
    assert len(base["buckets"]) == len(
        client.get("/api/v1/ranges/d7").get_json()["data"]["buckets"]
    )


def test_benchmark_has_per_bucket_deltas_aligned_with_the_buckets(client, a_code):
    """逐桶 delta 与 `base.buckets` 一一对齐，五条序列的键与图例键一致。

    趋势图悬停要显示「这一桶较基准同位 +12（+25.0%）」。悬停不可能每次打一趟接口，
    所以整段随 benchmark 下发；前端不许自己算这个减法（铁律 1）。
    """
    data = bench(client, a_code)["data"]
    assert len(data["buckets"]) == len(data["base"]["buckets"])
    assert set(data["buckets"][0]) == {"comments", "active", "interactions",
                                       "positive", "negative"}


def test_zero_base_is_new_not_unavailable(client):
    """基准期为 0 时 pct 算不出来，但增量是确切的。

    `pct is None` **不等于**不可用：判不可用要看 `abs is None`。混淆这两者会把
    「新增 5」渲染成「数据暂不可用」，而那两句话对产品团队的意思完全相反。
    """
    for code in [o["code"] for o in pool(client)["data"]["list"]]:
        for d in bench(client, code)["data"].values():
            if isinstance(d, dict) and d.get("abs") is not None and d.get("pct") is None:
                assert d["short"] in ("新增", "—")
                assert d["text"] != "数据暂不可用"
                return
    pytest.skip("演示数据里没有基准期为零的字段")


# ── pool.own：自家产品 KPI 汇总 ─────────────────────────────────────────


def test_own_rollup_covers_only_csop_products(client):
    """顶部两张卡逐字写着「仅统计 CSOP 自家 N 只」，且**不随榜单筛选变化**。"""
    data = pool(client)["data"]
    own = [o for o in data["list"] if o["ownership"] == "own"]
    assert data["own"]["count"] == len(own)
    assert data["own"]["heat"] == sum(o["discussionHeat"] for o in own)


@pytest.mark.parametrize("field", ["dHeat", "dNeg", "dPos"])
def test_own_rollup_deltas_are_computed_by_the_backend(client, field):
    """三张卡的环比由后端算好。设计源在屏幕里调了三次 `delta()` —— 那是 PRD 第 3 章的
    全局口径，铁律 1 要求只实现一份。"""
    d = pool(client)["data"]["own"][field]
    assert set(d) == {"text", "short", "abs", "pct", "dir"}


def test_own_risk_does_not_swallow_an_unscanned_product(client):
    """合计里混进一个 null，合计就是 null —— **不是**把它当 0 加进去。

    演示数据里有一只自家产品的合规扫描是 unavailable（设计源 `RISK_UNAVAILABLE`）。
    设计源那句 `P.complianceCount[o.code] || 0` 把「没扫过」读成「零条」，于是
    「其中需合规关注 27 条」看着像个完整的事实，实际少数了一只。这正是铁律 2 说的撒谎。
    """
    data = pool(client)["data"]
    counts = data["complianceCount"]
    own = [o["code"] for o in data["list"] if o["ownership"] == "own"]
    assert any(counts[c] is None for c in own), "演示数据里没有 unavailable 的自家产品了"
    assert data["own"]["risk"] is None


def test_peer_products_have_no_compliance_count_at_all(client):
    """同业产品不纳入需合规关注识别（PRD §4.1），字段结构性不适用 → null。

    与「自家产品但没扫过」在线上长得一样（都是 null），语义不同（na vs unavailable）；
    区分它们的是 `complianceFor` 的 status，那是工单 09 的事。这里只钉一件：
    **两者都不是 0。**
    """
    data = pool(client)["data"]
    peers = [o["code"] for o in data["list"] if o["ownership"] != "own"]
    assert peers and all(data["complianceCount"][c] is None for c in peers)


# ── observe 是池里的元素，不是端点 ──────────────────────────────────────


def test_observe_has_no_endpoint_because_it_is_an_element_of_the_pool(client, a_code):
    """`observe(code, range)` 的返回就是 `pool().list` 里那一个元素。

    开一个 `/observe/{code}`，产品监控页那句「按当前筛选列出候选产品」会退化成 120 次
    往返，而池本来就是整份下发的。这不是「视图内聚合」（ADR-0015），是取集合里的元素。
    """
    assert client.get(f"/api/v1/observe/{a_code}?range=d7").status_code == 404
    o = next(o for o in pool(client)["data"]["list"] if o["code"] == a_code)
    assert {"buckets", "comments", "discussionHeat", "attitude", "activeAccounts",
            "maxBucket"} <= set(o)


@pytest.mark.parametrize(
    "field",
    ["code", "name", "sector", "sectorName", "struct", "issuer", "ownership",
     "listingDate", "isNew", "south", "buckets", "mentions", "comments", "interactions",
     "likes", "shares", "discussionHeat", "activeAccounts", "activeByBucket", "attitude",
     "maxBucket", "updatedAt"],
)
def test_observation_carries_every_field_the_screens_need(client, field):
    """PRD §5：响应形状与函数返回一致。缺任何一项，前端就得自己算或自己编。"""
    assert field in pool(client)["data"]["list"][0]


def test_only_peers_carry_a_counterpart_code(client):
    """`ownCode`（对位自家产品）只有竞品才有，自家产品身上**结构性不存在**这个字段。

    JSON 里它是「键不在」而不是 `null`：设计源只在竞品分支上写这个键。竞品对位关系
    是「传播关系」区块的输入（PRD §4.2），自家产品对位自己没有意义。
    """
    pool_list = pool(client)["data"]["list"]
    peers = [o for o in pool_list if o["ownership"] == "peer"]
    owns = [o for o in pool_list if o["ownership"] == "own"]
    assert peers and all("ownCode" in o for o in peers)
    assert owns and not any("ownCode" in o for o in owns)


def test_pool_order_puts_csop_products_first(client):
    """池的顺序是 ORDER：自家 61 只在前、竞品 59 只在后。

    下拉、热力图都按这个顺序渲染，所以它是数据的一部分，不能靠前端排。
    """
    owns = [o["ownership"] for o in pool(client)["data"]["list"]]
    assert owns[0] == "own"
    assert owns.index("peer") == owns.count("own")
