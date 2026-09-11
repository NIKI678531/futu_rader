"""六态边界 —— PRD §3.6 的自动化护栏，本期最硬的一条要求。

## 六态与接口 status 的对应

| 态 | 含义 | 后端字段 | status |
|---|---|---|---|
| `0` | 已取得数据，统计值确实为零 | `0` | `ok` |
| `—` | 字段不适用于该产品或该口径 | 结构性缺席 | `na` |
| 暂不可用 | 应有值，但数据源未提供或未核验 | `null` | `unavailable` |
| 暂无内容 | 范围内查过了，没有符合条件的内容 | `[]` / `{}` | `empty` |
| 样本不足 | 有效产品态度评论 < LOW_SAMPLE(10) | 有值但不出结论 | `low_sample` |
| 待确认 | AI 标注 confidence < lowConfidence(0.7) | 有值但标待确认 | `ok` + 标记 |

## 两条不能松的

1. **数据缺失一律 200。** 404/5xx 描述的是「请求成不成」，六态描述的是「数据可不可得」。
   混用会让前端分不清「没这个产品」和「没这条路由」。
2. **`null` 不许被 `0` 或 `[]` 顶替。** `0` 是「数过了，确实是零」的专用值。拿它当未知，
   产品团队会把「没采到数」读成「市场上没人讨论」—— 这是这个只读工作台最坏的一种失效。

渲染层的对应断言在 frontend/scripts/six-state.mjs：这里管「后端有没有说实话」，
那边管「页面有没有照实说」。两头都得有，缺一头都能悄悄把 null 变成 0。
"""

from urllib.parse import quote

import pytest

from core import envelope


# ── 端点测试模板 ────────────────────────────────────────────────────────
#
# 后续每个新端点照抄这四类，缺哪类补哪类：
#
#   1. 形状     —— 200 + {"status","data"}，data 形状与 PRD §5 该函数返回一致
#   2. 六态     —— 该端点可能出现的缺失态各钉一例，且 null ≠ 0
#   3. 确定性   —— 同参数连调两次，响应逐字节相同
#   4. 口径逐字 —— 热度／去重／基准区间／环比的说明文字与 PRD 第 3 章逐字一致
#
# 现成范例分别见：test_ranges.py（1、3、4）、test_officials.py（1、2、3）、
# test_meta.py（4）。下面这几条是「2」的完整样板。


def posts(client):
    return client.get("/api/v1/officials/posts?range=d7").get_json()["data"]


def one(client, post_id):
    return next(p for p in posts(client) if p["id"] == post_id)


# ── 态 ①：`0` 是诚实的零 ────────────────────────────────────────────────


def test_a_true_zero_stays_zero(sixstate_client):
    """**反向断言。** 「null 不许显示成 0」很容易矫枉过正，把真零也一起抹掉。

    `0` 是「已取得数据且统计值确实为零」的专用值，它必须原样活到前端。
    """
    p = one(sixstate_client, "sixstate-zero")
    assert p["likes"] == 0
    assert p["comments"] == 0
    assert p["shares"] == 0
    assert p["engagement"] == 0
    # 不是 None、不是 "0"、不是 ""，就是整数 0
    for f in ("likes", "comments", "shares", "engagement"):
        assert isinstance(p[f], int), f


def test_zero_valued_payload_is_still_status_ok(sixstate_client):
    """全零 ≠ 没数据。status 仍是 ok。"""
    assert sixstate_client.get("/api/v1/officials/posts?range=d7").get_json()["status"] == "ok"


# ── 态 ②：暂不可用（null）——本文件的红线 ────────────────────────────────


@pytest.mark.parametrize("field", ["likes", "comments", "shares", "engagement"])
def test_null_field_survives_the_wire_as_null(sixstate_client, field):
    """**核心红线。** 字段级 null 必须原样到达前端，不得在任何一层被顶成 0 或 ""。

    这一条挂掉，铁律 2 就没有任何自动化护栏了。
    """
    v = one(sixstate_client, "sixstate-null")[field]
    assert v is None
    assert v != 0          # None != 0 恒真，写出来是为了让失败信息说人话
    assert v != ""


def test_null_field_does_not_turn_the_response_into_an_error(sixstate_client):
    """字段缺失是数据的事，不是请求的事：仍然 200，仍然 status=ok。"""
    r = sixstate_client.get("/api/v1/officials/posts?range=d7")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_null_payload_is_unavailable_not_empty():
    """`None` → unavailable；`[]` → empty。两者语义不同，不能互相顶替。

    「应该有但没取到」和「查过了确实没有」在页面上是两套文案，混了就说不清了。
    """
    assert envelope.status_of(None) == envelope.UNAVAILABLE
    assert envelope.status_of([]) == envelope.EMPTY
    assert envelope.status_of({}) == envelope.EMPTY
    assert envelope.status_of(0) == envelope.OK          # 零是数据，不是缺失
    assert envelope.status_of([0]) == envelope.OK


# ── 态 ③：暂无内容（空集）────────────────────────────────────────────────


def test_empty_collection_is_empty_not_unavailable(sixstate_client):
    """区间内一条 ETF 都没提 → 空数组 + status=empty。

    **不是** `etfCount: null`（那是「没查」），**也不是**丢个 404。
    """
    body = sixstate_client.get("/api/v1/officials/易方达/etf-mentions?range=d7").get_json()
    data = body["data"]
    assert data["list"] == []
    assert data["etfCount"] == 0
    assert data["postCount"] == 0
    # 集合本身非空（有 list/own/peer 这些键），所以信封是 ok；「空」体现在 list 上，
    # 由页面渲染成「— 区间内未提及 ETF」。
    assert body["status"] == "ok"


def test_no_summary_is_empty_content_not_a_missing_field(sixstate_client):
    """图片帖没摘要是「查过了没有」（hasSummary=False），不是「摘要取不到」。"""
    p = one(sixstate_client, "sixstate-nosummary")
    assert p["hasSummary"] is False
    assert p["summary"] == ""


# ── 态 ④：「—」结构性不适用 ──────────────────────────────────────────────


def test_structurally_not_applicable_is_an_empty_mention_list(sixstate_client):
    """没挂载也没提到任何 ETF → mentioned 为空、camp 为 none。

    页面据此渲染「— 区间内未提及 ETF」，而不是「0 只 · 0 次」：这个位置上的 0 会被读成
    「提了 0 只」，可字段压根不适用。
    """
    p = one(sixstate_client, "sixstate-nomention")
    assert p["mentioned"] == []
    assert p["camp"] == "none"


# ── 态 ⑤：待确认（低置信度）──────────────────────────────────────────────


def test_low_confidence_annotation_is_flagged(sixstate_client):
    """AI 标注置信度低于阈值 → 标签照挂，但标「待确认」（PRD §3.6）。"""
    low = one(sixstate_client, "sixstate-pending")
    assert low["confidence"] < 0.7
    assert low["typeLabel"]  # 类型照给，不因为没把握就留空


def test_confident_annotation_is_not_flagged(sixstate_client):
    """反面：达标的标注不该被标「待确认」，否则这个徽章就没信息量了。"""
    assert one(sixstate_client, "sixstate-confident")["confidence"] >= 0.7


def test_low_confidence_threshold_comes_from_meta(client):
    """阈值是口径，前端不得硬编码 0.7（铁律 1）。"""
    th = client.get("/api/v1/meta").get_json()["data"]["thresholds"]
    assert th["lowConfidence"] == 0.7


# ── 态 ⑥：样本不足 ──────────────────────────────────────────────────────


def test_low_sample_threshold_comes_from_meta(client):
    """有效产品态度评论 < 10 条不输出倾向结论（PRD §3.5）。阈值同样由后端下发。

    渲染侧的断言在产品监控页落地（工单 11）——态度模块此刻还没接 API。
    """
    th = client.get("/api/v1/meta").get_json()["data"]["thresholds"]
    assert th["lowSample"] == 10


def test_status_legend_is_verbatim_and_covers_all_six(client):
    """六态图例逐字来自后端。前端照抄一份就会和 PRD 慢慢漂开。"""
    legend = client.get("/api/v1/meta").get_json()["data"]["statusLegend"]
    assert [x["key"] for x in legend] == ["0", "—", "暂不可用", "暂无内容", "样本不足", "待确认"]
    assert legend[0]["text"] == "已取得数据，且统计值确实为零。"
    assert legend[1]["text"] == "字段不适用于该产品或该口径。"
    assert legend[2]["text"] == "字段应有值，但当前数据源未提供或尚未核验。"
    assert legend[3]["text"] == "范围内已完成检查，没有符合条件的内容。"


# ── 账号域第二页：KOL 影响力 ────────────────────────────────────────────
#
# 这一页比官号页多演一态：**操作方向判不出来**。它单独存在的理由就是不许拿
# 「持有观望」去顶替 —— 那是在把「模型没结论」写成「KOL 明确表示不动」，
# 而产品团队会把后者当成真实态度读。


def kol_one(sixstate_client, post_id):
    posts = sixstate_client.get("/api/v1/kol/impact?range=d7").get_json()["data"]["posts"]
    return next(p for p in posts if p["id"] == post_id)


def test_direction_pending_does_not_fabricate_a_direction(sixstate_client):
    """PRD §4.3 逐字：「判不出方向的操作类帖子标『方向待确认』」。

    `direction` 必须是 None。任何一个具体方向值（尤其是 'hold'）都是编造出来的结论。
    """
    p = kol_one(sixstate_client, "sixstate-kol-dirpending")
    assert p["directionPending"] is True
    assert p["direction"] is None
    assert p["hasDir"] is True                 # 徽章照挂，只是内容是「方向待确认」
    assert p["dir"]["k"] == "pending"
    assert p["dir"]["label"] == "方向待确认"


@pytest.mark.parametrize("field", ["likes", "comments", "shares", "engagement"])
def test_kol_null_counts_survive_the_wire_as_null(sixstate_client, field):
    assert kol_one(sixstate_client, "sixstate-kol-null")[field] is None


def test_kol_true_zero_stays_zero(sixstate_client):
    p = kol_one(sixstate_client, "sixstate-kol-zero")
    assert [p["likes"], p["comments"], p["shares"], p["engagement"]] == [0, 0, 0, 0]


def test_leader_totals_are_null_when_any_post_count_is_null(sixstate_client):
    """**合计对 null 有传染性。** 有一篇的评论数取不到，这位 KOL 的合计就是未知。

    把 null 当 0 加进去，得到的是一个看着完全正常、只是偏小的数字 —— 比渲染成 NaN
    坏得多，因为根本看不出来。KOL 详情页直接读这份 leaders（工单 07），所以后端下发的
    全量画像必须先守住这条；前端按可见集重算的那一份守在 lib/profile.js（ADR-0015）。
    """
    data = sixstate_client.get("/api/v1/kol/impact?range=d7").get_json()["data"]
    leader = data["leaders"][0]
    assert leader["comments"] is None
    assert leader["engagement"] is None
    assert leader["n"] == 5          # 篇数照常有值：那是数得出来的


# ── 账号域第三页：KOL 详情 ──────────────────────────────────────────────
#
# 发帖记录用的是上面那份 kol_impact 样本（同一位 KOL、同样 5 篇），这里只补它多出来的
# 那张表：「其他产品观点及操作」。它只有互动量一个数值位，但那一格上两种含义都要能演。


def opinions(sixstate_client):
    kol = sixstate_client.get("/api/v1/kol/impact?range=d7").get_json()["data"]["leaders"][0]["kol"]
    body = sixstate_client.get(f"/api/v1/kol/{quote(kol)}/opinions?range=d7").get_json()
    return {r["code"]: r for r in body["data"]}


def test_opinion_true_zero_stays_zero(sixstate_client):
    assert opinions(sixstate_client)["9998"]["engagement"] == 0


def test_opinion_null_engagement_survives_the_wire_as_null(sixstate_client):
    assert opinions(sixstate_client)["9997"]["engagement"] is None


def test_two_nulls_in_one_row_mean_different_things(sixstate_client):
    """`engagement=None` 与 `net=None` 都是 null，但不是同一件事。

    前者是「平台计数没采到」（PRD §3.6 暂不可用），后者是「有效态度评论不足阈值，
    不下结论」（样本不足）。页面上只看得见前者 —— `net` 此刻没有渲染位，六态护栏
    够不着它，所以由这里钉住。两者都不许变成 0：`engagement=0` 是「没人互动」，
    `net=0` 是「褒贬正好抵消」，都是结论，我们一个都没得出。
    """
    rows = opinions(sixstate_client)
    assert rows["9997"]["engagement"] is None and rows["9997"]["net"] is None
    assert rows["9998"]["engagement"] == 0 and rows["9998"]["net"] == 0
    assert rows["9999"]["engagement"] == 143 and rows["9999"]["net"] == 12.5


def test_day_axis_is_sent_by_the_backend(sixstate_client):
    """KOL 详情的发帖时间线按天排，日历轴来自 /ranges（`dates`），不是屏幕现算。

    设计源在屏里 `addDays(range.from, i)`，那是演示生成器泄漏进屏幕代码
    （ADR-0004、前端静态守卫①）。桶顶不上：d1 的桶是 24 个小时桶，柱子只有 1 根。
    """
    data = sixstate_client.get("/api/v1/ranges/d7").get_json()["data"]
    assert data["dates"] == [
        "2026-08-26", "2026-08-27", "2026-08-28",
        "2026-08-29", "2026-08-30", "2026-08-31", "2026-09-01",
    ]


# ── 市场域：价格与阶段的两种「整份取不到」 ──────────────────────────────

NO_PRICE = "3469"  # 六态池里承担价格／阶段两态的那只产品（fixtures/make_sixstate.py）


def test_price_unavailable_is_not_a_chart_of_zeros(sixstate_client):
    """没有行情源 → 每根 K 四个价格字段都是 `None`，**一个 0 都不许有**。

    这是铁律 2 在本页代价最高的落点：0 画出来是一根掉到零的 K 线，一只 68 港元的 ETF
    在图上崩盘再弹回来。它不会报错、不会缺格，只会是一张看着很有信息量的错图。
    """
    data = sixstate_client.get(f"/api/v1/products/{NO_PRICE}/candles?range=d7").get_json()["data"]
    assert data["status"] == "unavailable"
    assert data["currency"] is None
    assert data["missing"] == len(data["list"]) > 0
    for c in data["list"]:
        assert (c["open"], c["high"], c["low"], c["close"]) == (None, None, None, None)
        assert c["note"] == "价格数据暂不可用"


def test_stages_unavailable_still_ships_the_heat_line(sixstate_client):
    """阶段观点没跑出来，热度折线照常给。

    两件事分开失败：热度是采集来的，观点是 AI 归纳的。一起吞掉，页面会退化成
    「这只产品没有讨论」，而它明明有 —— 折线上就有高度。
    """
    data = sixstate_client.get(f"/api/v1/products/{NO_PRICE}/stages?range=d7").get_json()["data"]
    assert data["status"] == "unavailable"
    assert data["stages"] == []
    assert any(p["mentions"] > 0 for p in data["series"])
    # 没有阶段就没有判定：下发一个判不了任何东西的阈值，前端只会以为自己该拿它去判。
    assert "threshold" not in data and "unitCount" not in data


def test_the_other_products_keep_their_prices(sixstate_client):
    """场景只换了这一只产品的价格。反向断言：整个场景一片「不可用」也能骗过上面两条。"""
    ok = sixstate_client.get("/api/v1/products/3033/candles?range=d7").get_json()["data"]
    assert ok["status"] == "ok" and ok["currency"] == "HKD"
    assert any(c["open"] for c in ok["list"])


# ── 场景本身的确定性 ────────────────────────────────────────────────────


def test_sixstate_scenario_is_deterministic(sixstate_client):
    a = sixstate_client.get("/api/v1/officials/posts?range=d7").get_data()
    b = sixstate_client.get("/api/v1/officials/posts?range=d7").get_data()
    assert a == b


def test_scenario_falls_back_to_demo_for_untouched_keys(sixstate_client):
    """场景只覆盖它显式给出的键，其余照常走演示数据。

    否则构造一个六态样本就得把整份 fixture 复制一遍，复制出来的那份还会慢慢过期。
    """
    assert len(sixstate_client.get("/api/v1/officials/posts?range=d30").get_json()["data"]) > 6


def test_default_scenario_is_untouched_demo_data(client):
    """不设 DEMO_SCENARIO 时行为与从前完全一致——六态 fixture 不许漏进常规演示态。"""
    ids = [p["id"] for p in posts(client)]
    assert not any(i.startswith("sixstate-") for i in ids)
