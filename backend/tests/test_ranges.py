"""GET /api/v1/ranges/<key> —— PRD §5 `buildRange(key)`。

守卫的是「前端不自行算桶」这条（PRD §5 逐字）：桶、基准区间、各处文案都必须在响应里，
少一样前端就得自己补，铁律 1 就破了。
"""

from datetime import date, datetime

import pytest

import core.ranges as ranges_core
from core.range_spec import CustomRangeError, validate_selectable, validate_span
from sql_fixture import make_sql_provider

RANGE_KEYS = ["d0", "d1", "d2", "d7", "d14", "d30"]


def test_all_six_presets_are_available(client):
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


def test_demo_today_uses_the_frozen_current_data_time_not_the_complete_day(client):
    data = client.get("/api/v1/ranges/d0").get_json()["data"]
    assert data["to"] == "2026-09-02"
    assert data["benchTo"] == "2026-09-01"
    assert data["partial"] is True
    assert data["dataThrough"] == "2026-09-02T09:00:00 HKT"


def test_sql_today_never_reuses_the_latest_complete_day(sql_client, monkeypatch):
    """当天没有新事实时必须不可用，不能把昨日数据再展示一遍。"""
    monkeypatch.setenv("RADAR_ANCHOR_MODE", "system")
    monkeypatch.setattr(
        "radar_db.clock.now",
        lambda tz=None: datetime(2026, 8, 25, 16, 31, tzinfo=tz),
    )

    today_range = sql_client.get("/api/v1/ranges/d0").get_json()["data"]
    yesterday_range = sql_client.get("/api/v1/ranges/d1").get_json()["data"]
    today_pool = sql_client.get("/api/v1/pool?range=d0").get_json()
    yesterday_pool = sql_client.get("/api/v1/pool?range=d1").get_json()

    assert today_range["to"] == "2026-08-26"
    assert yesterday_range["to"] == "2026-08-25"
    assert today_range["to"] != yesterday_range["to"]
    assert today_pool == {"status": "unavailable", "data": None}
    assert yesterday_pool["status"] == "ok"


def test_custom_range_uses_inclusive_hkt_days_and_an_equal_adjacent_benchmark(client):
    response = client.get("/api/v1/ranges/custom?from=2026-08-20&to=2026-08-25")
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["key"] == "custom"
    assert data["label"] == "自选日期"
    assert data["from"] == "2026-08-20"
    assert data["to"] == "2026-08-25"
    assert data["days"] == 6
    assert data["dates"] == [f"2026-08-{day:02d}" for day in range(20, 26)]
    assert data["benchFrom"] == "2026-08-14"
    assert data["benchTo"] == "2026-08-19"


def test_short_historical_range_is_valid_when_its_facts_are_available():
    # Regression: 08-03 ～ 08-10 is eight days.  Its age relative to the
    # current anchor must not make the Apply button/API reject it.
    validate_selectable(
        date(2026, 8, 3),
        date(2026, 8, 10),
        date(2026, 7, 1),
        date(2026, 10, 26),
    )


def test_market_and_account_custom_span_policies_are_distinct():
    validate_span(date(2026, 1, 1), date(2026, 1, 31), "market")
    with pytest.raises(CustomRangeError, match="31"):
        validate_span(date(2026, 1, 1), date(2026, 2, 1), "market")

    validate_span(date(2024, 2, 29), date(2025, 2, 28), "accounts")
    with pytest.raises(CustomRangeError, match="自然年"):
        validate_span(date(2024, 2, 29), date(2025, 3, 1), "accounts")


def test_sql_provider_loads_retained_historical_content_beyond_the_latest_30_days():
    provider = make_sql_provider(anchor="2026-10-26")
    key = "custom:2026-08-24:2026-08-25"

    rng = provider.build_range(key)
    pool = provider.pool(key)

    assert rng["from"] == "2026-08-24" and rng["to"] == "2026-08-25"
    assert pool is not None
    assert any(item["comments"] > 0 for item in pool["list"])


def test_consecutive_custom_intervals_keep_compliance_caches_isolated():
    provider = make_sql_provider()
    first = provider.build_range("custom:2026-08-24:2026-08-24")
    second = provider.build_range("custom:2026-08-24:2026-08-25")

    provider._compliance_scan(first)
    provider._compliance_scan(second)

    assert ("compliance", "custom", "2026-08-24", "2026-08-24") in provider._cache
    assert ("compliance", "custom", "2026-08-24", "2026-08-25") in provider._cache


@pytest.mark.parametrize(
    "query",
    [
        "from=2026-08-20",
        "from=2026-08-25&to=2026-08-20",
        "from=2026-08-02&to=2026-08-03",
        "from=2026-08-03&to=2026-09-02",
        "from=2026-02-30&to=2026-03-01",
    ],
)
def test_custom_range_rejects_missing_reversed_out_of_window_and_invalid_dates(client, query):
    response = client.get("/api/v1/ranges/custom?" + query)
    assert response.status_code == 400


def test_custom_range_query_is_supported_by_every_range_sensitive_endpoint(sql_client):
    query = "?range=custom&from=2026-08-20&to=2026-08-25"
    response = sql_client.get("/api/v1/pool" + query)
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert isinstance(data["list"], list)
    assert "complianceCount" in data

    # Regression: the frontend used to short-circuit all of these for custom
    # dates.  Keep the public HTTP tuple wired through every AI-backed surface,
    # even when an individual product legitimately returns unavailable/empty.
    for path in (
        "/api/v1/hot-summaries",
        "/api/v1/products/3033/summary",
        "/api/v1/products/3033/market-outlook",
        "/api/v1/products/3033/themes",
        "/api/v1/products/3033/negative-categories",
        "/api/v1/products/3033/competitors",
        "/api/v1/products/3033/topics",
        "/api/v1/products/3033/stages",
    ):
        endpoint = sql_client.get(path + query)
        assert endpoint.status_code == 200, path
        assert endpoint.get_json()["status"] in {
            "ok", "empty", "unavailable", "low_sample", "na",
        }, path


def test_internal_custom_cache_token_is_not_accepted_as_a_public_query(sql_client):
    response = sql_client.get(
        "/api/v1/pool?range=custom:2026-08-20:2026-08-25"
    )
    assert response.status_code == 400


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


def test_unavailable_range_logs_actionable_diagnostics(client, monkeypatch, caplog):
    """provider 返回 None 时，线上日志必须说明资源、provider 与排查方向。"""

    class UnavailableProvider:
        def build_range(self, key):
            return None

    monkeypatch.setattr(ranges_core, "get_provider", lambda: UnavailableProvider())

    with caplog.at_level("WARNING", logger="core.ranges"):
        response = client.get("/api/v1/ranges/d7")

    assert response.get_json() == {"status": "unavailable", "data": None}
    message = "\n".join(caplog.messages)
    assert "range_unavailable" in message
    assert "key=d7" in message
    assert "provider=UnavailableProvider" in message
    assert "possible_causes=" in message


def test_sql_range_log_identifies_a_missing_anchor(client, monkeypatch, caplog):
    provider = make_sql_provider(anchor=None)
    monkeypatch.setattr(ranges_core, "get_provider", lambda: provider)

    caplog.clear()
    with caplog.at_level("WARNING", logger="core.ranges"):
        response = client.get("/api/v1/ranges/d7")

    assert response.get_json() == {"status": "unavailable", "data": None}
    message = "\n".join(caplog.messages)
    assert "provider=SqlProvider" in message
    assert "reason=anchor_missing" in message
    assert "database=sqlite:///" in message
    assert "anchor=<missing>" in message
    assert "import_or_sync_not_completed" in message
