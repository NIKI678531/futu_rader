"""市场域叙述组 —— hot-summaries / summary / themes / negative-categories /
competitors / compliance（PRD §5）。

基础组回答「有多少条讨论」，这一组回答「讨论在说什么」。于是断言的重心也不一样：
基础组盯的是数（铁律 3 的排名、色阶），这一组盯的是**状态与措辞**。

这组里最容易出错的一类 bug 长这样：三种不同的「没有内容」（不适用 / 取不到 / 查过了
确实没有）在响应里都是一个空数组，于是页面把它们渲染成同一句话，读的人得出一个我们
从来没得出的结论。所以下面反复出现同一个形状的断言：**空数组区分不了它们，status 才行。**
"""

import pytest

RANGE_KEYS = ["d1", "d2", "d7", "d14", "d30"]

PER_PRODUCT = ["summary", "themes", "negative-categories", "competitors", "compliance"]


def hot(client, qs="?range=d7"):
    return client.get("/api/v1/hot-summaries" + qs).get_json()


def get(client, code, tail, qs="?range=d7"):
    return client.get(f"/api/v1/products/{code}/{tail}" + qs).get_json()


@pytest.fixture
def codes(client):
    return [o["code"] for o in client.get("/api/v1/pool?range=d7").get_json()["data"]["list"]]


@pytest.fixture
def own_code():
    """一只有内容的自家产品：正负主题、负面类别、需合规关注都非空。

    这里写死代码是有意的：需要的是「样样都有」的那一只，靠 pool 的第一条挑不出来。
    """
    return "3033"


@pytest.fixture
def peer_code(client):
    pool = client.get("/api/v1/pool?range=d7").get_json()["data"]["list"]
    return next(o["code"] for o in pool if o["ownership"] == "peer")


# ── 信封与参数 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", RANGE_KEYS)
def test_all_five_presets_are_available(client, key, own_code):
    assert hot(client, f"?range={key}")["status"] == "ok"
    for tail in PER_PRODUCT:
        body = get(client, own_code, tail, f"?range={key}")
        assert set(body) == {"status", "data"}
        assert body["status"] == "ok"


def test_default_range_is_d7(client, own_code):
    assert hot(client, "")["data"] == hot(client, "?range=d7")["data"]
    for tail in PER_PRODUCT:
        assert get(client, own_code, tail, "")["data"] == get(client, own_code, tail)["data"]


def test_unknown_range_is_404_everywhere(client, own_code):
    assert client.get("/api/v1/hot-summaries?range=d99").status_code == 404
    for tail in PER_PRODUCT:
        r = client.get(f"/api/v1/products/{own_code}/{tail}?range=d99")
        assert r.status_code == 404, tail


def test_unknown_product_is_404_not_an_empty_narrative(client):
    """池里没这只产品 → 404。**不是** 200 + 空数组。

    200 + `[]` 的意思是「有这只产品，这段时间没人聊它」，页面会照常渲染「暂无相关内容」，
    而真相是代码写错了。这两件事在屏幕上必须长得不一样。
    """
    for tail in PER_PRODUCT:
        r = client.get(f"/api/v1/products/0000/{tail}?range=d7")
        assert r.status_code == 404, tail
        assert "error" in r.get_json()


def test_deterministic(client, own_code):
    assert get(client, own_code, "themes") == get(client, own_code, "themes")


# ── 热议总结：整池一份 ──────────────────────────────────────────────────


def test_hot_summaries_cover_the_whole_pool(client, codes):
    """榜单每一行都有一句热议总结，所以整池一份、按代码取。

    少一只，那一行就得由前端编一句话或者留白 —— 两种都是在屏幕上现造口径。
    """
    data = hot(client)["data"]
    assert set(data) == set(codes)


def test_hot_summary_text_comes_from_the_backend_in_every_state(client):
    """非 ok 状态下 `text` **已经是那一态的文案**，前端直接渲染。

    让前端按 status 拼文案，等于把 PRD §3.6 的措辞实现成第二份（铁律 1），而这份是
    最容易写歪的一份：三态各一句话，混用任意两句都在说一件没发生的事。
    """
    want = {
        "low_sample": "样本不足，暂无主流观点",
        "unavailable": "数据暂不可用",
    }
    seen = set()
    for v in hot(client)["data"].values():
        assert v["status"] in ("ok", "low_sample", "unavailable")
        seen.add(v["status"])
        if v["status"] == "ok":
            assert v["ok"] is True and v["text"] and v["tone"] in ("pos", "neg", "neu")
        else:
            assert v["ok"] is False
            assert v["text"] == want[v["status"]]
    assert seen == {"ok", "low_sample", "unavailable"}, "演示数据不再覆盖三态"


def test_unavailable_hot_summary_is_not_a_zero_sample_sentence(client):
    """取不到的那句话是「数据暂不可用」，不是「基于 0 条有效态度样本」。

    `sample` 确实是 0，但它是占位，不是一次「我们查过了，一条都没有」的结论。
    ok 为 False 时那个数不该出现在任何一句给人看的话里（铁律 2）。
    """
    bad = [v for v in hot(client)["data"].values()
           if v["status"] == "unavailable" and "0" in v["text"]]
    assert not bad


# ── 当前舆情总结 ────────────────────────────────────────────────────────


def test_summary_low_sample_says_so_in_the_text(client, codes):
    """样本不足时正文自己写明阈值，且**不输出倾向结论**（PRD §3.5）。

    阈值 10 只有一处定义（/meta thresholds.lowSample）。这里断言的是那个数确实出现在
    这句话里 —— 两处各写一个数，改了一处就会有人读到「低于 10 条」却按 5 条判。
    """
    threshold = str(client.get("/api/v1/meta").get_json()["data"]["thresholds"]["lowSample"])
    lows = [get(client, c, "summary")["data"] for c in codes]
    lows = [s for s in lows if s["low"]]
    assert lows, "演示数据里没有样本不足的产品了"
    for s in lows:
        assert s["sample"] < int(threshold)
        assert f"低于 {threshold} 条的判定阈值" in s["text"]
        assert "不输出整体倾向结论" in s["text"]


def test_summary_shape(client, own_code):
    s = get(client, own_code, "summary")["data"]
    assert set(s) == {"text", "sample", "low"}
    assert isinstance(s["low"], bool) and isinstance(s["sample"], int)


# ── 正负主题：一次给两个极性 ────────────────────────────────────────────


def test_themes_ship_both_polarities_in_one_response(client, own_code):
    """`{positive, negative}` 一次给全。

    设计源签名是 `themesFor(code, range, polarity)`，但两处调用点都是正负各取一次。
    同步 Suspense 下一次未命中就是一次串行往返，切成两个端点等于白挨一次（ADR-0005）。
    """
    data = get(client, own_code, "themes")["data"]
    assert set(data) == {"positive", "negative"}
    assert all(t["polarity"] == "positive" for t in data["positive"])
    assert all(t["polarity"] == "negative" for t in data["negative"])


def test_themes_ignore_a_polarity_query_parameter(client, own_code):
    """不吃 `?polarity=` —— 接了就迟早有人传，然后另一半悄悄没了。"""
    base = get(client, own_code, "themes")["data"]
    assert get(client, own_code, "themes", "?range=d7&polarity=positive")["data"] == base


def test_themes_are_sorted_by_mentions(client, own_code):
    """按提及数降序由后端排好：排序口径是口径的一部分，不是渲染细节。"""
    for items in get(client, own_code, "themes")["data"].values():
        m = [t["mentions"] for t in items]
        assert m == sorted(m, reverse=True)


def test_theme_deltas_are_the_embedded_shape(client, own_code):
    for items in get(client, own_code, "themes")["data"].values():
        for t in items:
            assert set(t["delta"]) == {"text", "short", "abs", "pct", "dir"}
            assert {"id", "title", "summary", "share", "confidence",
                    "evidenceCount", "buckets"} <= set(t)


def test_an_empty_theme_list_is_a_200(client, codes):
    """「这段时间没有可归类的主题」是 200 + 空数组，页面渲染「暂无内容」。

    空不是错误，也不该被补成一条占位主题。
    """
    empties = [c for c in codes if not get(client, c, "themes")["data"]["positive"]]
    assert empties, "演示数据里没有空主题的产品了"
    assert client.get(f"/api/v1/products/{empties[0]}/themes?range=d7").status_code == 200


# ── 负面舆情类别 ────────────────────────────────────────────────────────


def test_negative_category_shares_do_not_have_to_add_up(client, own_code):
    """`shareOfNegative` 的分母是**消极总数**，各类别之和小于 100% 是口径本身的形状。

    可归类、可行动的那部分 ≠ 全部消极观点。有人看到「加起来不到 100%」去把余数补成
    「其他」，就等于凭空造了一个类别；把分母改成「已归类之和」则会让每一类都虚高。
    """
    cats = get(client, own_code, "negative-categories")["data"]
    assert cats
    total = sum(c["shareOfNegative"] for c in cats)
    assert 0 < total < 100


def test_negative_categories_carry_lifecycle_and_severity_labels(client, own_code):
    """生命周期与严重度的**中文标签**随数据下发，不由屏幕按枚举现拼。"""
    for c in get(client, own_code, "negative-categories")["data"]:
        assert c["lifecycle"] in ("rising", "fading", "steady", "new")
        assert c["severity"] in ("high", "medium", "low")
        assert c["lifecycleLabel"] and c["severityLabel"]
        assert set(c["delta"]) == {"text", "short", "abs", "pct", "dir"}


# ── 关联竞品 ────────────────────────────────────────────────────────────


def test_competitor_relations_keep_the_confirmed_distinction(client, codes):
    """`confirmed`（客户维护的固定对位）与 `auto_candidate`（AI 依据同板块同结构识别）
    必须一直分得开 —— 页面上后者要显示「待确认」（PRD §3.6 六态之一）。

    把 auto_candidate 当 confirmed 显示，等于替客户确认了一段他们没确认过的对位关系。
    """
    seen = set()
    for c in codes:
        data = get(client, c, "competitors")["data"]
        assert data["status"] in ("ok", "empty", "unavailable")
        for x in data["list"]:
            assert x["relation"] in ("confirmed", "auto_candidate")
            assert x["reason"]
            seen.add(x["relation"])
    assert seen == {"confirmed", "auto_candidate"}, "演示数据不再覆盖两种对位关系"


def test_competitor_status_distinguishes_empty_from_unavailable(client, codes):
    """没有对位竞品（empty）与传播关系数据取不到（unavailable）都是空列表。

    列表本身区分不了，status 才行 —— 这就是这一组端点为什么带 status。
    """
    for c in codes:
        data = get(client, c, "competitors")["data"]
        assert set(data) == {"status", "list"}
        assert (data["status"] == "ok") == bool(data["list"])


# ── 需合规关注 ──────────────────────────────────────────────────────────


def test_compliance_is_na_for_peer_products(client, peer_code):
    """同业产品不纳入需合规关注识别（PRD §4.1）：`na`，字段结构性不适用。

    不是「查了没有」（empty），更不是 0 条。
    """
    data = get(client, peer_code, "compliance")["data"]
    assert data["status"] == "na"
    assert data["list"] == []


def test_compliance_covers_all_four_states(client, codes):
    """ok / empty / unavailable / na 在演示数据里都要有活样本。

    四者在页面上是四句不同的话。少一态没人测，写歪了也不会红。
    """
    seen = {}
    for c in codes:
        data = get(client, c, "compliance")["data"]
        assert data["status"] in ("ok", "empty", "unavailable", "na")
        seen.setdefault(data["status"], c)
    assert set(seen) == {"ok", "empty", "unavailable", "na"}


def test_the_three_empty_states_are_only_told_apart_by_status(client, codes):
    """empty / unavailable / na 的 `list` 一模一样（都是空的）。

    这条断言是反着写的：它证明**光看列表分不出来**。所以谁把 status 从响应里省掉，
    或者前端只判 `list.length === 0`，三句话就会塌成一句。
    """
    empties = {}
    for c in codes:
        data = get(client, c, "compliance")["data"]
        if data["status"] != "ok":
            empties.setdefault(data["status"], data["list"])
    assert set(empties) == {"empty", "unavailable", "na"}
    assert all(v == [] for v in empties.values())


def test_compliance_items_never_conclude(client, codes):
    """每条恒为 `ai_pending`「AI 识别 · 待人工确认」（PRD §4.1／§4.2 逐字）。

    AI 只识别信号并保留原文与命中依据，不判定言论真伪、不判定产品是否违规。
    出现任何别的 reviewState，就是有人让机器下了结论。
    """
    n = 0
    for c in codes:
        for x in get(client, c, "compliance")["data"]["list"]:
            assert x["reviewState"] == "ai_pending"
            assert x["reviewLabel"] == "AI 识别 · 待人工确认"
            assert x["excerpt"] and x["detectionRationale"] and x["sourceUrl"]
            n += 1
    assert n


def test_compliance_count_in_the_pool_agrees_with_the_list(client, codes):
    """池里的 `complianceCount` 与这里的列表长度是同一件事的两种取法。

    榜单红标（工单 08）与抽屉里的清单（这一张）必须对得上：有 3 条就是 3 条，
    取不到就是 null 而不是 0（铁律 2）。两处各算各的，页面上会同时出现两个数。
    """
    counts = client.get("/api/v1/pool?range=d7").get_json()["data"]["complianceCount"]
    for c in codes:
        data = get(client, c, "compliance")["data"]
        if data["status"] in ("ok", "empty"):
            assert counts[c] == len(data["list"])
        else:
            assert counts[c] is None, f"{c} 是 {data['status']}，计数必须是 null"
