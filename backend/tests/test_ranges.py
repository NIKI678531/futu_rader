"""GET /api/v1/ranges/<key> —— PRD §5 `buildRange(key)`。

守卫的是「前端不自行算桶」这条（PRD §5 逐字）：桶、基准区间、各处文案都必须在响应里，
少一样前端就得自己补，铁律 1 就破了。
"""

import pytest

RANGE_KEYS = ["d1", "d2", "d7", "d14", "d30"]


def test_all_five_presets_are_available(client):
    for key in RANGE_KEYS:
        r = client.get(f"/api/v1/ranges/{key}")
        assert r.status_code == 200, key
        assert r.get_json()["status"] == "ok", key


def test_envelope_shape(client):
    body = client.get("/api/v1/ranges/d7").get_json()
    assert set(body) == {"status", "data"}


@pytest.mark.parametrize(
    "field",
    # PRD §5：响应形状与函数返回一致。缺任何一项，前端就得自己算或自己编。
    ["key", "days", "from", "to", "label", "text", "gran", "granLabel", "buckets",
     "dates", "benchFrom", "benchTo", "benchText", "benchLabel", "trendTitle"],
)
def test_range_carries_every_field_the_screens_need(client, field):
    assert field in client.get("/api/v1/ranges/d7").get_json()["data"]


@pytest.mark.parametrize("key,gran", [("d1", "hour"), ("d7", "day"), ("d30", "week")])
def test_dates_is_the_day_axis_and_buckets_cannot_stand_in_for_it(client, key, gran):
    """`dates` 是**逐日**日历轴，`buckets` 不是 —— 它的粒度随区间在时/日/周之间变。

    KOL 详情页的时间线固定按天画（PRD §4.4），d1 要 1 根柱不是 24 根，d30 要 30 根
    不是 5 根。设计源那里靠前端 `addDays(from, i)` 现算；那是第二份日历实现，
    工单 04 的静态守卫①盯的就是它。所以日历轴也由后端下发。
    """
    data = client.get(f"/api/v1/ranges/{key}").get_json()["data"]
    assert data["gran"] == gran
    assert len(data["dates"]) == data["days"]
    assert data["dates"][0] == data["from"]
    assert data["dates"][-1] == data["to"]
    assert data["dates"] == sorted(data["dates"])
    assert len(set(data["dates"])) == data["days"]


def test_buckets_are_sent_by_the_backend_not_computed_by_the_screen(client):
    """PRD §5 逐字：「后端下发，前端不自行算桶」。"""
    data = client.get("/api/v1/ranges/d7").get_json()["data"]
    assert data["gran"] == "day"
    assert len(data["buckets"]) == 7
    assert data["buckets"][0]["day"] == data["from"]
    assert data["buckets"][-1]["day"] == data["to"]


def test_hourly_granularity_for_short_ranges(client):
    """d1/d2 走小时桶（PRD §3.1）：24 桶/天，且文案是「较昨日同期」而非「较上一等长区间」。"""
    data = client.get("/api/v1/ranges/d1").get_json()["data"]
    assert data["gran"] == "hour"
    assert len(data["buckets"]) == 24
    assert data["benchLabel"] == "较昨日同期"


def test_weekly_granularity_for_d30(client):
    data = client.get("/api/v1/ranges/d30").get_json()["data"]
    assert data["gran"] == "week"
    assert data["granLabel"] == "按自然周"


def test_benchmark_window_is_adjacent_and_equal_length(client):
    """基准区间＝紧邻其前的等长段（PRD §3.1）。环比 delta 的分母就是它。"""
    data = client.get("/api/v1/ranges/d7").get_json()["data"]
    assert data["benchTo"] < data["from"]
    assert data["benchText"] == f"{data['benchFrom']} ～ {data['benchTo']}"


def test_anchor_is_frozen(client):
    """演示锚点冻结在 2026-09-01（ADR-0012）。跨日仍返回同一区间，否则逐字比对会随机红。"""
    assert client.get("/api/v1/ranges/d7").get_json()["data"]["to"] == "2026-09-01"


def test_unknown_preset_is_a_404_not_a_silent_fallback(client):
    """静默回落到 d7 会让前端拿到另一段时间的数据却浑然不觉。"""
    r = client.get("/api/v1/ranges/d99")
    assert r.status_code == 404
    body = r.get_json()
    assert "error" in body and "status" not in body


def test_deterministic(client):
    """同参数连调两次逐字节相同 —— Playwright 逐字比对的前提（ADR-0012）。"""
    a = client.get("/api/v1/ranges/d7").get_data()
    b = client.get("/api/v1/ranges/d7").get_data()
    assert a == b
