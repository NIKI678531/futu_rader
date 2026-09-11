"""产品监控证据组 —— topics / kol-mentions / evidence（PRD §5）。

叙述组盯的是**状态与措辞**，这一组盯的是**对位**：产品监控页上每个结论旁边都有一个
入口，点开是支撑它的原帖。所以这里最贵的一类 bug 不报错、数量对、格式也对，只是
**给错了对象** —— 点「份额稀释」的负面主题，弹出来的是另一个话题的帖子。

`evidenceFor(code, ctxKey, polarity, n)` 四个参数全部参与选取。下面每个参数各有一条
「换了它结果就得变」的断言：任何一个被静默忽略，页面上都没有任何迹象。

另一半重量在 `dominantAttitude`：有效样本 < 3 条时是 None，前端渲染「暂不可用」。
这是铁律 2 在本页的落点 —— 少数几条帖子推不出一个人的倾向，填 0 或者挑个极性都是
在替这个 KOL 说话。
"""

import pytest

RANGE_KEYS = ["d1", "d2", "d7", "d14", "d30"]

PER_PRODUCT = ["topics", "kol-mentions"]


def get(client, code, tail, qs="?range=d7"):
    return client.get(f"/api/v1/products/{code}/{tail}" + qs).get_json()


def ev(client, code, ctx, polarity="neutral", n=None):
    """证据端点。ctx 里有竖线，交给 requests/werkzeug 编码。"""
    qs = f"?ctx={ctx}&polarity={polarity}" + (f"&n={n}" if n is not None else "")
    return client.get(f"/api/v1/products/{code}/evidence" + qs)


@pytest.fixture
def codes(client):
    return [o["code"] for o in client.get("/api/v1/pool?range=d7").get_json()["data"]["list"]]


@pytest.fixture
def own_code():
    """一只样样都有的自家产品：正负主题、热议话题、KOL 提及都非空。"""
    return "3033"


# ── 信封与参数 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", RANGE_KEYS)
def test_all_five_presets_are_available(client, key, own_code):
    for tail in PER_PRODUCT:
        body = get(client, own_code, tail, f"?range={key}")
        assert set(body) == {"status", "data"}
    assert ev(client, own_code, f"{key}|sum").status_code == 200


def test_default_range_is_d7(client, own_code):
    for tail in PER_PRODUCT:
        assert get(client, own_code, tail, "")["data"] == get(client, own_code, tail)["data"]


def test_unknown_range_is_404_everywhere(client, own_code):
    for tail in PER_PRODUCT:
        assert client.get(f"/api/v1/products/{own_code}/{tail}?range=d99").status_code == 404
    assert ev(client, own_code, "d99|sum").status_code == 404


def test_unknown_product_is_404_not_an_empty_sidebar(client):
    """池里没这只产品 → 404，**不是** 200 + 空数组。

    200 + `[]` 的意思是「有这只产品，这个结论下没有原帖」，页面照常渲染「暂无相关
    内容」；而真相是代码写错了，侧栏在为一个不存在的东西作证。
    """
    for tail in PER_PRODUCT:
        r = client.get(f"/api/v1/products/0000/{tail}?range=d7")
        assert r.status_code == 404, tail
        assert "error" in r.get_json()
    assert ev(client, "0000", "d7|sum").status_code == 404


def test_deterministic(client, own_code):
    assert get(client, own_code, "topics") == get(client, own_code, "topics")
    assert ev(client, own_code, "d7|sum").get_json() == ev(client, own_code, "d7|sum").get_json()


# ── 热议话题 ────────────────────────────────────────────────────────────


def test_topics_are_sorted_by_mentions(client, codes):
    """按提及数降序由后端排好：排序口径是口径的一部分，不是渲染细节。"""
    for c in codes:
        m = [t["mentions"] for t in get(client, c, "topics")["data"]]
        assert m == sorted(m, reverse=True), c


def test_topic_shape(client, own_code):
    for t in get(client, own_code, "topics")["data"]:
        assert {"id", "title", "summary", "mentions", "positive", "negative", "neutral",
                "buckets", "evidenceCount", "peak", "split"} <= set(t)
        assert set(t["delta"]) == {"text", "short", "abs", "pct", "dir"}
        assert t["positive"] + t["negative"] + t["neutral"] == t["mentions"]


def test_an_empty_topic_list_is_a_200(client, codes):
    """「这段时间没人聊它」是 200 + 空数组，页面渲染「暂无内容」。

    d1 有八成产品是这个样子 —— 一天的窗口里大多数 ETF 一条讨论都没有，这是真实
    形状，不是缺数据，更不该被补成一条占位话题。
    """
    empties = [c for c in codes if not get(client, c, "topics", "?range=d1")["data"]]
    assert empties, "演示数据里 d1 不再有空话题的产品了"
    assert client.get(f"/api/v1/products/{empties[0]}/topics?range=d1").status_code == 200


# ── KOL 提及：<3 条不输出主要态度 ───────────────────────────────────────


def test_kol_mentions_shape_and_scope(client, own_code):
    """范围恒为客户维护的合作 KOL 名单，随数据下发而不是屏幕上写死。"""
    data = get(client, own_code, "kol-mentions")["data"]
    assert set(data) == {"status", "scope", "list"}
    assert data["scope"] == "合作 KOL 名单"
    for k in data["list"]:
        assert {"kolName", "kolType", "kolTypeLabel", "mentionCommentCount",
                "lastMentionedAt", "representativeExcerpt", "evidence"} <= set(k)
        assert k["productCode"] == own_code


def test_dominant_attitude_is_null_below_three_samples(client, codes):
    """有效样本 < 3 条 → `dominantAttitude` 与 `dominantLabel` 都是 None（PRD §5 表 P8）。

    这条断言是双向的：不到 3 条**必须**是 None，到了 3 条**必须**有结论。写成单向
    （「None 的都 < 3」）就漏掉了更常见的那种写法错误 —— 把阈值判断整个漏掉，于是
    每一行都有一个由一两条帖子推出来的态度标签。
    """
    seen = {True: 0, False: 0}
    for c in codes:
        for k in get(client, c, "kol-mentions")["data"]["list"]:
            low = k["mentionCommentCount"] < 3
            assert (k["dominantAttitude"] is None) is low, (c, k["kolName"])
            assert (k["dominantLabel"] is None) is low, (c, k["kolName"])
            seen[low] += 1
    assert seen[True] and seen[False], "演示数据不再同时覆盖样本足与样本不足"


def test_null_attitude_is_never_a_zero_or_an_empty_string(client, codes):
    """铁律 2：暂不可用只能是 `null`。

    `0` 是「已取得数据且确实为零」的专用值，`""` 会在前端 `||` 兜底时静默变成别的
    东西 —— 两者都会让「我们不知道」看起来像一个结论。
    """
    for c in codes:
        for k in get(client, c, "kol-mentions")["data"]["list"]:
            for f in ("dominantAttitude", "dominantLabel"):
                assert k[f] is None or isinstance(k[f], str) and k[f]
                assert k[f] != 0


def test_kol_mentions_status_distinguishes_empty_from_unavailable(client, codes):
    """名单里没人提过它（empty）与 KOL 身份映射没接上（unavailable）都是空列表。

    列表本身区分不了，status 才行 —— 前者是「查过了」，后者是「查不了」。
    """
    seen = {}
    for c in codes:
        data = get(client, c, "kol-mentions")["data"]
        assert data["status"] in ("ok", "empty", "unavailable")
        assert (data["status"] == "ok") == bool(data["list"])
        seen.setdefault(data["status"], c)
    assert set(seen) == {"ok", "empty", "unavailable"}, "演示数据不再覆盖三态"


def test_kol_mention_rows_carry_their_own_evidence(client, own_code):
    """KOL 提及每行自带原帖，**不走** evidence 端点。

    侧栏点开的是「这个 KOL 提到本产品的那几条」，它已经在行里了；再去 evidence 端点
    按 ctxKey 取一遍，取回来的会是另一个口径下的帖子。
    """
    rows = get(client, own_code, "kol-mentions")["data"]["list"]
    assert rows
    for k in rows:
        assert len(k["evidence"]) == k["evidenceCount"] == k["mentionCommentCount"]
        for e in k["evidence"]:
            assert e["authorName"] == k["kolName"]
            assert e["excerpt"] and e["sourceUrl"]


# ── 原文证据：四个参数全链路 ────────────────────────────────────────────


def test_evidence_shape(client, own_code):
    for e in ev(client, own_code, "d7|sum").get_json()["data"]:
        assert set(e) == {"id", "publishedAt", "authorName", "authorType", "excerpt",
                          "productCodes", "comments", "interactions", "sourceUrl"}
        assert e["authorType"] in ("普通散户", "合作 KOL", "官方账号")


def test_evidence_is_sorted_newest_first(client, own_code):
    got = [e["publishedAt"] for e in ev(client, own_code, "d7|sum", n=12).get_json()["data"]]
    assert got == sorted(got, reverse=True)


def test_default_count_is_six_and_the_cap_is_twelve(client, own_code):
    """不传 `n` 取 6 条，上限 12 —— 钳位是口径（PRD §5 `Math.max(1, Math.min(12, count || 6))`），
    不是前端的防御性写法。

    `n=0` 落回默认的 6 条而不是 1 条：`||` 把 0 当没传。负数才走 `Math.max(1, …)`。
    两者不一样，钉住它是因为「0 表示不限」「0 表示一条都不要」都是很自然的误读。
    """
    assert len(ev(client, own_code, "d7|sum").get_json()["data"]) == 6
    assert len(ev(client, own_code, "d7|sum", n=99).get_json()["data"]) == 12
    assert len(ev(client, own_code, "d7|sum", n=0).get_json()["data"]) == 6
    assert len(ev(client, own_code, "d7|sum", n=-3).get_json()["data"]) == 1


def test_n_is_not_silently_ignored(client, own_code):
    """每一个 n 都得到恰好 n 条，且 n 条的那一份是 12 条的子集。

    id 逐字是 `code + ctxKey + i`，i 是生成序号，所以「取 n 条」= 取 i < n 的那几条。
    这条断言同时钉住两件事：数量对，以及取的是**前 n 条**而不是随手截断排序后的结果。
    """
    prefix = own_code + "d7|sum"
    for n in range(1, 13):
        items = ev(client, own_code, "d7|sum", n=n).get_json()["data"]
        assert len(items) == n
        assert {e["id"] for e in items} == {prefix + str(i) for i in range(n)}


def test_ctx_is_not_silently_ignored(client, own_code):
    """换一个面板 id，证据必须整份换掉。

    这是本组最贵的 bug 的正面断言：ctxKey 是「哪一个结论」的地址，忽略它就等于
    每个入口都弹同一批帖子，而页面上完全看不出来。
    """
    themes = get(client, own_code, "themes")["data"]["negative"]
    topics = get(client, own_code, "topics")["data"]
    assert themes and topics
    seen = {}
    for ctx, pol in [("d7|sum", "neutral"),
                     ("d7|" + themes[0]["id"], "negative"),
                     ("d7|" + topics[0]["id"], "neutral")]:
        ids = tuple(e["id"] for e in ev(client, own_code, ctx, pol).get_json()["data"])
        assert all(i.startswith(own_code + ctx) for i in ids)
        seen[ctx] = ids
    assert len(set(seen.values())) == len(seen)


def test_range_inside_ctx_is_not_silently_ignored(client, own_code):
    """ctxKey 自带区间前缀，换区间就是换一段时间的原帖。

    发布时间必须落在该区间内 —— 侧栏显示的是「这段时间里的证据」，混进区间外的帖子
    就是拿别的时间段给当前结论作证。
    """
    prev = None
    for key in RANGE_KEYS:
        rng = client.get(f"/api/v1/ranges/{key}").get_json()["data"]
        items = ev(client, own_code, f"{key}|sum", n=12).get_json()["data"]
        for e in items:
            assert rng["from"] <= e["publishedAt"][:10] <= rng["to"], (key, e["publishedAt"])
        cur = tuple(e["id"] for e in items)
        assert cur != prev
        prev = cur


def test_polarity_is_not_silently_ignored(client, own_code):
    """极性是参数键的一部分，不是配色开关。

    每个面板 id 只对一个极性有意义（总结是中性、负面主题是负面……），所以「极性没被
    忽略」的证据是：同一个 ctxKey 换极性会 **404**。要是它被从键里丢掉了，这里会
    200 并回一批中性原帖 —— 数量对、格式对，只是与「负面主题」这个标题无关。

    另一半是正面：三个极性取自三个不同的原文库，摘要互不重叠。这是「关联竞品」面板
    正／负两个入口唯一的区别，忽略极性它们就变成同一个按钮。
    """
    assert ev(client, own_code, "d7|sum", "neutral").status_code == 200
    for pol in ("positive", "negative"):
        assert ev(client, own_code, "d7|sum", pol).status_code == 404, pol

    themes = get(client, own_code, "themes")["data"]
    banks = {
        "neutral": ev(client, own_code, "d7|sum", "neutral", 12),
        "positive": ev(client, own_code, "d7|" + themes["positive"][0]["id"], "positive", 12),
        "negative": ev(client, own_code, "d7|" + themes["negative"][0]["id"], "negative", 12),
    }
    banks = {k: {e["excerpt"] for e in v.get_json()["data"]} for k, v in banks.items()}
    assert all(banks.values())
    for a, b in [("positive", "negative"), ("positive", "neutral"), ("negative", "neutral")]:
        assert not banks[a] & banks[b], (a, b)


def test_unknown_ctx_or_polarity_is_404_not_a_handful_of_posts(client, own_code):
    """认不出来的入口 → 404 → 屏级错误条。**不回落**到「随便给几条」。

    回落的代价不是少看几条，是看到一批**与结论无关**的原帖却以为它们是证据。
    """
    assert ev(client, own_code, "d7|no-such-panel").status_code == 404
    assert ev(client, own_code, "sum").status_code == 404          # 缺区间前缀
    assert ev(client, own_code, "d7|sum", "sideways").status_code == 404
    assert ev(client, own_code, "d7|sum", "").status_code == 404


def test_a_non_integer_count_is_a_400_not_a_default(client, own_code):
    """`?n=abc` 是请求写错了（400），不是「没这个资源」（404），也不是悄悄给 6 条。"""
    r = client.get(f"/api/v1/products/{own_code}/evidence?ctx=d7|sum&polarity=neutral&n=abc")
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_competitor_evidence_lives_under_the_competitor_code(client, own_code):
    """关联竞品面板取的是**竞品自己**的原文，所以 code 是竞品代码。

    传成本产品代码同样能取到一份（`sum` 之外的面板 id 也许恰好存在），拿回来的却是
    自家产品的帖子 —— 数量对、格式对、说的是另一只 ETF。
    """
    comps = get(client, own_code, "competitors")["data"]["list"]
    assert comps
    c = comps[0]["code"]
    ctx = "d7|" + c + "positive"
    ok = ev(client, c, ctx, "positive")
    assert ok.status_code == 200
    assert all(e["id"].startswith(c + ctx) for e in ok.get_json()["data"])
    assert ev(client, own_code, ctx, "positive").status_code == 404


def test_every_panel_on_the_product_page_has_an_entry(client, own_code):
    """产品监控页的入口全部可取：总结、正负主题、热议话题、关联竞品、阶段观点。

    少一种，页面上就有一个点开来是错误条的按钮。这条断言是「面板 id 枚举」的现场
    验收 —— 侧栏的入口是有限的、可枚举的，取不到就该 404 而不是给一份别的。

    阶段那一路的 ctxKey 是屏幕现拼的 `'stage-' + n + '-' + st.from`，也就是说**阶段合并
    的结果直接变成了证据的地址**：合并口径一变，阶段序号和起始日跟着变，昨天还能打开的
    那个入口今天就 404 了。所以这里不写死几个 id，而是拿当期 `stagesFor` 的输出现拼 ——
    这条断言测的正是「这两处对得上」。极性同理：`st.sentiment` 为 None 时屏幕退回
    'neutral'（`st.sentiment || 'neutral'`），样本不足的阶段照样打得开侧栏。
    """
    key = "d7"
    themes = get(client, own_code, "themes")["data"]
    n = 0
    for pol in ("positive", "negative"):
        for t in themes[pol]:
            assert ev(client, own_code, f"{key}|{t['id']}", pol).status_code == 200
            n += 1
    for t in get(client, own_code, "topics")["data"]:
        assert ev(client, own_code, f"{key}|{t['id']}").status_code == 200
        n += 1
    for c in get(client, own_code, "competitors")["data"]["list"]:
        for pol in ("positive", "negative"):
            assert ev(client, c["code"], f"{key}|{c['code']}{pol}", pol).status_code == 200
            n += 1
    for st in get(client, own_code, "stages")["data"]["stages"]:
        ctx = f"{key}|stage-{st['n']}-{st['from']}"
        assert ev(client, own_code, ctx, st["sentiment"] or "neutral").status_code == 200, ctx
        n += 1
    assert n > 10
