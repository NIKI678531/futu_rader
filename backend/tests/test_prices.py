"""价格与阶段组 —— candles / heat-series / stages / daily（PRD §5）。

这一组的断言几乎全都围着**一个 `None`** 转。

产品监控页把 K 线和讨论热度画在同一张图上，横轴共用一套桶。市场每周休两天，所以
7 天视图里必然有两根空 K —— 空 K 的四个价格字段是 `None`。把它当 0 画出来，图上就是
一只 68 港元的 ETF 在周末跌到 0 再弹回来。这不是显示得难看，是一张会被截图发出去的
「崩盘」图（铁律 2 在本组的落点，PRD §5 状态语义逐字点名的第二个字段）。

因此下面有三层护栏：
  - 四个价格字段**同生共死**（不允许出现 open 有值而 close 是 None 的半截 K）；
  - 有值的价格**永远不是 0**（0 在六态里是「数过了确实是零」，价格没有这种事）；
  - 空桶必须带一句 `note` 说明为什么空 —— 而「本来就没开市」和「该有但取不到」是
    **两种不同的空**，前者五句话里的前四句，后者只有「价格数据暂不可用」一句。

阶段组盯的是另一件事：合并是**口径**，在后端做完。前端拿到的 `stages` 已经是合并好
的色带，只负责画。所以这里断言阶段**恰好铺满**序列（不重叠、不留缝、首尾贴边）——
一旦合并逻辑漏掉一段，页面上不会报错，只会有一块没有颜色的空白，看着像「那几天没
讨论」。
"""

import collections

import pytest

RANGE_KEYS = ["d1", "d2", "d7", "d14", "d30"]

# 空桶的五种理由（设计源 candlesFor）。前四种是「市场没开」，第五种是「取不到」。
# 「整周休市」在演示数据里一次都没出现（周桶必然含交易日），但代码里它是可达分支，
# 所以下面用子集断言而不是相等 —— 断言相等会在真实数据某周全休时无端变红。
MARKET_CLOSED = {"休市日", "午间休市", "非交易时段", "整周休市"}
PRICE_UNAVAILABLE = "价格数据暂不可用"
NOTES = MARKET_CLOSED | {PRICE_UNAVAILABLE}

OHLC = ("open", "high", "low", "close")

# 阶段观点里「查过了、样本不够、不下结论」的三句逐字文案（PRD §3.6「样本不足」）。
LOW_SAMPLE_LABEL = "样本不足"
LOW_SAMPLE_SUMMARY = "样本不足，暂无主流观点"
NOT_APPLICABLE = "—"


def get(client, code, tail, qs="?range=d7"):
    return client.get(f"/api/v1/products/{code}/{tail}" + qs)


def body(client, code, tail, qs="?range=d7"):
    return get(client, code, tail, qs).get_json()["data"]


TAILS = ["candles", "heat-series", "stages"]  # 吃 ?range= 的三个


@pytest.fixture
def own_code():
    """一只样样都有的自家产品：K 线正常、阶段观点已生成。"""
    return "3033"


@pytest.fixture
def no_price_code():
    """没有价格数据源的产品 → candles 整份 unavailable。"""
    return "3406"


@pytest.fixture
def no_stage_code():
    """阶段观点尚未生成的产品 → stages 整份 unavailable。"""
    return "3007"


@pytest.fixture
def low_sample_code():
    """一只讨论量常年不够下结论的产品：每个区间都出得了灰段。

    刻意不用 `own_code` —— 3033 是主推产品，五个区间的阶段全都有分类，拿它去测样本
    不足会静默地测了个空列表。
    """
    return "3469"


# ── 信封与参数 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", RANGE_KEYS)
def test_all_five_presets_are_available(client, key, own_code):
    for tail in TAILS:
        assert set(get(client, own_code, tail, f"?range={key}").get_json()) == {"status", "data"}


def test_default_range_is_d7(client, own_code):
    for tail in TAILS:
        assert body(client, own_code, tail, "") == body(client, own_code, tail)


def test_unknown_range_is_404_everywhere(client, own_code):
    for tail in TAILS:
        assert get(client, own_code, tail, "?range=d99").status_code == 404, tail


def test_unknown_product_is_404_not_a_blank_chart(client):
    """池里没这只产品 → 404，**不是** 200 + 空 K 线。

    200 + 一整排 `None` 的意思是「有这只产品，这段时间没有价格」，页面会照常渲染
    「价格数据暂不可用」并把它当成数据源的问题；而真相是代码传错了产品代码。
    """
    for tail in TAILS:
        r = get(client, "0000", tail)
        assert r.status_code == 404, tail
        assert "error" in r.get_json()
    assert client.get("/api/v1/products/0000/daily").status_code == 404


def test_daily_does_not_take_a_range(client, own_code):
    """`dailyFor(code)` 只有一个参数：它给的是固定 42 天的完整日历，由调用方自己截。

    加一个被忽略的 `?range=` 比不加更糟 —— 传了没反应的参数，调用方只会以为自己筛过
    了。所以这里断言的是「传了也不变」而不是「传了报错」：端点压根不读这个参数。
    """
    plain = client.get(f"/api/v1/products/{own_code}/daily").get_json()["data"]
    assert client.get(f"/api/v1/products/{own_code}/daily?range=d1").get_json()["data"] == plain
    assert len(plain) == 42


def test_daily_covers_the_whole_market_too(client):
    """`'ALL'` 是 `dailyFor` 自己的伪代码（全市场合计），不是某只产品。"""
    r = client.get("/api/v1/products/ALL/daily")
    assert r.status_code == 200
    assert len(r.get_json()["data"]) == 42


def test_deterministic(client, own_code):
    for tail in TAILS:
        assert body(client, own_code, tail) == body(client, own_code, tail)


# ── K 线：空桶就是空桶 ──────────────────────────────────────────────────


@pytest.mark.parametrize("key", RANGE_KEYS)
def test_candles_align_bucket_for_bucket_with_the_range(client, key, own_code):
    """K 线的每一根对着 `buildRange(key).buckets` 的一个桶，顺序一致。

    横轴由 `buildRange` 下发、前端不自行算桶（PRD §5 逐字）。K 线与热度折线画在同一条
    轴上，这里错位一格，图上就是「价格先动、讨论后跟」这种根本不存在的先后关系。
    """
    tips = [b["tip"] for b in client.get(f"/api/v1/ranges/{key}").get_json()["data"]["buckets"]]
    assert [c["bucket"] for c in body(client, own_code, "candles", f"?range={key}")["list"]] == tips


@pytest.mark.parametrize("key", RANGE_KEYS)
def test_ohlc_nulls_travel_together(client, key, own_code):
    """四个价格字段同生共死：要么四个都有值，要么四个都是 `None`。

    半截 K（open 有值、close 是 None）在画布上会被当成一根从某价位掉到 0 的实体柱。
    """
    for c in body(client, own_code, "candles", f"?range={key}")["list"]:
        assert len({c[f] is None for f in OHLC}) == 1, c


def test_a_price_is_never_zero(client, own_code):
    """0 是「数过了确实是零」的专用值（PRD §3.6）。价格没有这种事。

    这条是反过来钉「别把 None 补成 0」：补零的痕迹不会出现在 null 检查里，只会出现在
    这里 —— 一根 open=high=low=close=0 的 K，四个字段照样「同生共死」。
    """
    for key in RANGE_KEYS:
        for c in body(client, own_code, "candles", f"?range={key}")["list"]:
            if c["open"] is not None:
                assert all(c[f] > 0 for f in OHLC), c


def test_every_empty_bucket_says_why_and_every_filled_one_stays_quiet(client, own_code):
    """空桶必带 `note`，有值的桶 `note` 必为空串。

    悬浮提示直接印这句话。空桶不带理由，页面上就是一段没有解释的断线；有值的桶带着
    上一次的理由，读的人会以为那天休市却又有价格。
    """
    for key in RANGE_KEYS:
        for c in body(client, own_code, "candles", f"?range={key}")["list"]:
            assert bool(c["note"]) == (c["open"] is None), c
            assert c["note"] in NOTES or c["note"] == "", c


def test_missing_counts_the_real_empties(client, own_code):
    for key in RANGE_KEYS:
        data = body(client, own_code, "candles", f"?range={key}")
        assert data["missing"] == sum(1 for c in data["list"] if c["open"] is None)


def test_a_normal_week_has_empty_buckets_and_still_says_status_ok(client, own_code):
    """`status == 'ok'` 不等于「每根 K 都有值」。

    7 天视图必然含一个周末。要是有人把「有空桶」当成「这只产品没有价格」，整只产品
    的价格面板会在每周一集体消失。
    """
    data = body(client, own_code, "candles")
    assert data["status"] == "ok"
    assert data["missing"] == 2
    assert {c["note"] for c in data["list"] if c["note"]} == {"休市日"}


def test_market_closed_is_not_the_same_empty_as_price_unavailable(
    client, own_code, no_price_code
):
    """两种空必须分得开 —— 这是本组最要害的一条。

    「休市日」是市场没开，本来就没有价格，图上断一格是**对的**；「价格数据暂不可用」
    是该有价格但我们取不到，页面要挂徽章告诉用户这只产品的价格不可信。把后者的文案
    用到前者身上，等于每个周末都在报一次数据故障。
    """
    ok = body(client, own_code, "candles")
    assert all(c["note"] != PRICE_UNAVAILABLE for c in ok["list"])
    assert ok["currency"] == "HKD"

    gone = body(client, no_price_code, "candles")
    assert gone["status"] == "unavailable"
    assert gone["missing"] == len(gone["list"]) > 0
    assert {c["note"] for c in gone["list"]} == {PRICE_UNAVAILABLE}
    # 币种也取不到。给它填 'HKD' 是在替一只没有行情源的产品认领一个计价货币。
    assert gone["currency"] is None


@pytest.mark.parametrize("key", RANGE_KEYS)
def test_price_unavailable_is_a_property_of_the_product_not_the_range(
    client, key, no_price_code
):
    """没有行情源是产品的属性，换个区间不会突然有价格。"""
    assert body(client, no_price_code, "candles", f"?range={key}")["status"] == "unavailable"


# ── 热度序列：与 K 线共用横轴，但周视图按天 ────────────────────────────


@pytest.mark.parametrize("key", RANGE_KEYS)
def test_heat_series_length_follows_the_granularity(client, key, own_code):
    """小时／日粒度逐桶，**周粒度逐日**。

    d30 的 K 线是 5 根周 K，热度折线却是 30 个点 —— 讨论量按周聚合会把一次三天的热议
    抹成一条平线，而这一面板的全部意义就是看热度什么时候起来的。这不是 bug，是两条
    线在同一张图上各按各的粒度画（设计源 heatSeriesFor）。
    """
    rng = client.get(f"/api/v1/ranges/{key}").get_json()["data"]
    expect = rng["days"] if rng["gran"] == "week" else len(rng["buckets"])
    assert len(body(client, own_code, "heat-series", f"?range={key}")) == expect


def test_heat_series_carries_the_attitude_split(client, own_code):
    """每个点带正／负／中性拆分：阶段观点的样本判定从这里来。"""
    p = body(client, own_code, "heat-series")[0]
    assert {"i", "day", "hour", "label", "tip", "heat", "mentions",
            "comments", "positive", "negative", "neutral"} == set(p)


# ── 阶段观点：合并是口径，不是前端的分组 ──────────────────────────────


@pytest.mark.parametrize("key", RANGE_KEYS)
def test_stages_embed_the_same_series_the_heat_endpoint_returns(client, key, own_code):
    """`stages.series` 与 `/heat-series` 是**同一份数**，逐字节相等。

    内嵌不是冗余：热度折线与阶段色带画在同一套几何上，分两次取数就是白挨一次串行
    往返。这条断言钉的是「两个出口不会发散」—— 演示期同一次计算导出两份，真实期
    `stagesFor` 的实现本身就调 `heatSeriesFor`。
    """
    got = body(client, own_code, "stages", f"?range={key}")
    assert got["series"] == body(client, own_code, "heat-series", f"?range={key}")


def test_stage_threshold_rides_along_with_the_response(client, own_code):
    """判定阈值随响应下发，因为它**取决于粒度**：半日 5、整日 10。

    /meta 上那两个数是它的取值来源，不是判定本身。前端要是照着 /meta 的 lowSample=10
    去判半日阶段，小时视图里几乎每一段都会被判成样本不足，整页阶段观点塌成一片灰。
    """
    day = body(client, own_code, "stages", "?range=d7")
    hour = body(client, own_code, "stages", "?range=d1")
    assert (day["granularity"], day["threshold"]) == ("day", 10)
    assert (hour["granularity"], hour["threshold"]) == ("half_day", 5)

    t = client.get("/api/v1/meta").get_json()["data"]["thresholds"]
    assert (day["threshold"], hour["threshold"]) == (t["lowSample"], t["stageHalfDay"])


def test_stages_tile_the_series_exactly(client, own_code):
    """阶段首尾贴边、逐段相接，既不重叠也不留缝。

    色带按 `idxFrom`/`idxTo` 定位在折线上。漏掉一段不会报错，只会在图上留一块没有颜色
    的空白，看着像「那几天没人讨论」—— 而那几天的折线明明是有高度的。
    """
    for key in RANGE_KEYS:
        data = body(client, own_code, "stages", f"?range={key}")
        stages = data["stages"]
        assert stages, key
        assert stages[0]["idxFrom"] == 0
        assert stages[-1]["idxTo"] == len(data["series"]) - 1
        for a, b in zip(stages, stages[1:]):
            assert b["idxFrom"] == a["idxTo"] + 1, (key, a["label"], b["label"])
        assert sum(s["unitCount"] for s in stages) == data["unitCount"]


def test_adjacent_stages_share_a_category_only_after_absorbing_something(client):
    """相邻同类只在**吸并过孤立时段**之后才允许出现。

    合并的第一步是「相邻且分类相同归为一段」，所以正常情况下相邻两段的分类必然不同。
    但 14 天及以上视图还有第二步：单日孤立观点并入相邻阶段。一段 A ｜ 孤立 ｜ 一段 A
    被吸并之后，就会留下两段挨着的 A —— 它们中间隔着一段被吞掉的日子，不是没合并干净。
    这条断言把这个例外**限定**在 `absorbed > 0` 上：哪天第一步真的漏了，它照样会红。
    """
    seen = 0
    for code in ("7226", "3034", "3454", "3096"):
        for key in ("d14", "d30"):
            stages = body(client, code, "stages", f"?range={key}")["stages"]
            for a, b in zip(stages, stages[1:]):
                if a["category"] is not None and a["category"] == b["category"]:
                    assert a["absorbed"] or b["absorbed"], (code, key, a["label"])
                    seen += 1
    assert seen, "样本里一次相邻同类都没有，这条断言已经保护不到任何东西了"


def test_low_sample_is_not_unavailable(client, low_sample_code):
    """样本不足的那一段：分类、情绪都是 `None`，但页面上写的是「样本不足」。

    六态里这是两格：「暂不可用」是字段应有值而取不到，「样本不足」是查过了、只是不够
    下结论。同一个 `None` 在这两种场合下的说法不能互换 —— 前者要人去查数据源，后者是
    正常业务状态。这也是为什么下面那条断言整份响应里不许出现「暂不可用」四个字。
    """
    lows = [
        s
        for key in RANGE_KEYS
        for s in body(client, low_sample_code, "stages", f"?range={key}")["stages"]
        if s["category"] is None
    ]
    assert lows, "演示数据里一段样本不足都没有，这条断言保护不到任何东西"
    for s in lows:
        assert s["sentiment"] is None
        assert s["categoryLabel"] == LOW_SAMPLE_LABEL
        assert s["sentimentLabel"] == NOT_APPLICABLE
        assert s["summary"] == LOW_SAMPLE_SUMMARY


def test_a_stage_with_a_category_always_has_a_sentiment(client, own_code):
    """反过来也成立：出得了分类就出得了情绪，两者同生共死。

    只有分类没有情绪，表格里会出现「减仓离场 / 暂不可用」这种半截结论。
    """
    for key in RANGE_KEYS:
        for s in body(client, own_code, "stages", f"?range={key}")["stages"]:
            assert (s["category"] is None) == (s["sentiment"] is None), s["label"]


def test_stage_text_never_borrows_the_unavailable_wording(client, own_code):
    """三套缺数文案不可互换（CLAUDE.md 铁律 2）：阶段观点只会说「样本不足」。

    「暂不可用」「暂无相关内容」出现在阶段文本里，就是有人把「不够下结论」渲染成了
    「数据没到」。
    """
    for key in RANGE_KEYS:
        raw = get(client, own_code, "stages", f"?range={key}").get_data(as_text=True)
        assert "暂不可用" not in raw
        assert "暂无相关内容" not in raw


def test_stages_unavailable_keeps_the_series_and_drops_the_bands(client, no_stage_code):
    """阶段观点尚未生成 → `stages` 为空数组，`series` 照常给。

    折线画得出来（热度是采集来的），色带画不出来（观点是 AI 归纳的，还没跑）。这两件
    事分开失败，所以响应里也分开表达 —— 把 series 一起吞掉，页面会退化成「这只产品
    没有讨论」，而它明明有。

    `threshold` / `unitCount` 在这一态**整个键都不存在**：没有阶段就没有判定，下发一个
    判不了任何东西的阈值只会让前端以为自己该拿它去判点什么。
    """
    for key in RANGE_KEYS:
        data = body(client, no_stage_code, "stages", f"?range={key}")
        assert data["status"] == "unavailable"
        assert data["stages"] == []
        assert len(data["series"]) > 0
        assert "threshold" not in data and "unitCount" not in data


def test_empty_and_low_sample_differ_only_in_whether_anyone_talked(client):
    """`empty` 是区间内根本没讨论，`low_sample` 是有讨论但不够下结论。

    两态的 `stages` 长得几乎一模一样（都是分类为 None 的灰段），区别全在 `series` 上：
    `empty` 的每个点 mentions 都是 0，`low_sample` 至少有一个点非零。页面据此说两句
    不同的话 —— 「没有讨论，不输出阶段观点」和「样本不足，暂无主流观点」。合并这两态
    等于对一只真有人在聊的产品说「没人聊」。
    """
    empty = body(client, "3442", "stages", "?range=d1")
    low = body(client, "3469", "stages", "?range=d1")
    assert (empty["status"], low["status"]) == ("empty", "low_sample")
    assert all(p["mentions"] == 0 for p in empty["series"])
    assert any(p["mentions"] > 0 for p in low["series"])
    # 灰段本身分不出这两态来：两边都是 category=None、都写「样本不足」。
    assert all(s["category"] is None for s in empty["stages"] + low["stages"])


def test_stage_rule_travels_with_the_response(client, own_code):
    """口径原文随响应下发，与 /meta 上那份是同一句。

    面板说明位直接印它。前端要是自己抄一份常量，改口径就得改两处，而漏掉的那一处
    不会有任何测试变红 —— 除了这一条。
    """
    rule = client.get("/api/v1/meta").get_json()["data"]["rules"]["stage"]
    for key in RANGE_KEYS:
        assert body(client, own_code, "stages", f"?range={key}")["rule"] == rule


def test_all_four_stage_statuses_have_live_samples(client):
    """四态各有活样本，否则上面那些按状态分支的断言只是在测一条路径。

    **走全池**，不是前几只：`unavailable` 只有一只产品（3007，池里第 83 位），截前 40 只
    这条断言就会红，而把它写成 `codes[:40] + ['3007']` 是在假装扫过了。d1 一个区间就
    凑齐四态（小时粒度下大量产品当天没有讨论 → empty）。
    """
    codes = [o["code"] for o in client.get("/api/v1/pool?range=d7").get_json()["data"]["list"]]
    seen = collections.Counter(
        body(client, code, "stages", "?range=d1")["status"] for code in codes
    )
    assert {"ok", "low_sample", "empty", "unavailable"} <= set(seen), seen


# ── 日度序列 ────────────────────────────────────────────────────────────


def test_daily_rows_are_complete(client, own_code):
    """42 天全给，字段齐全。

    第一期没有屏幕读它（趋势图走 `/heat-series` 内嵌在 stages 里的那份），它存在是因为
    PRD §5 要求 24 个契约函数 1:1 对应端点。演示期它的价格与 `candlesFor` 不同源 ——
    没有行情源的产品在这里照样有价格数字。接真实库时两者必须并到同一张行情表上，
    否则同一只产品的价格在两个端点上会对不上。
    """
    rows = body(client, own_code, "daily", "")
    assert len(rows) == 42
    assert {"date", "iso", "comments", "active", "px", "o", "h", "l", "c"} == set(rows[0])
    assert [r["iso"] for r in rows] == sorted(r["iso"] for r in rows)
