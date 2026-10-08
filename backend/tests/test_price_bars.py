from datetime import date, datetime

from core.calendar import build
from core.price_bars import aggregate_prices, session_slots


def aggregate(rng, bars):
    return aggregate_prices(
        rng,
        bars,
        currency="HKD",
        source="FMP",
        adjustment="split_adjusted",
    )


def test_hong_kong_sessions_and_hourly_buckets():
    assert session_slots("2026-08-23") == []
    slots = session_slots("2026-08-25")
    assert len(slots) == 11
    assert not any(slot.hour == 12 for slot in slots)
    bars = [{"timestamp": slot, "open": 10, "high": 12, "low": 9, "close": 11} for slot in slots]
    rng = build("d1", date(2026, 8, 25))
    output = aggregate(rng, bars)
    assert len(output["list"]) == 24
    assert output["list"][9]["close"] == 11
    assert output["list"][12]["note"] == "午间休市"
    sparse = aggregate(rng, bars[:-1])
    assert sparse["list"][15]["close"] == 11
    assert sparse["list"][15]["note"] == ""
    missing = aggregate(rng, bars[:-2])
    assert missing["list"][15]["close"] is None
    assert missing["list"][15]["note"] == "价格数据暂不可用"


def test_hourly_bucket_aggregates_the_real_half_hour_bar_when_its_peer_is_missing():
    rng = build("d1", date(2026, 8, 25))
    sparse_bar = {
        "timestamp": datetime(2026, 8, 25, 10, 30),
        "open": 10.2,
        "high": 10.8,
        "low": 10.1,
        "close": 10.7,
    }

    output = aggregate(rng, [sparse_bar])

    hour = output["list"][10]
    assert output["status"] == "ok"
    assert hour == {
        "bucket": "08-25 10:00–11:00",
        "open": 10.2,
        "high": 10.8,
        "low": 10.1,
        "close": 10.7,
        "note": "",
    }


def test_hourly_bucket_orders_real_bars_by_timestamp_before_aggregating():
    rng = build("d1", date(2026, 8, 25))
    bars_in_reverse_time_order = [
        {
            "timestamp": datetime(2026, 8, 25, 10, 30),
            "open": 20,
            "high": 21,
            "low": 19,
            "close": 20.5,
        },
        {
            "timestamp": datetime(2026, 8, 25, 10, 0),
            "open": 10,
            "high": 22,
            "low": 9,
            "close": 11,
        },
    ]

    hour = aggregate(rng, bars_in_reverse_time_order)["list"][10]

    assert hour["open"] == 10
    assert hour["high"] == 22
    assert hour["low"] == 9
    assert hour["close"] == 20.5


def test_month_week_uses_shared_buckets_and_requires_all_sessions():
    rng = build("mtd", date(2026, 8, 25))
    bars = [{"timestamp": datetime.fromisoformat(day), "open": 10, "high": 12, "low": 9, "close": 11}
            for day in rng["dates"] if session_slots(day)]
    output = aggregate(rng, bars)
    assert [row["bucket"] for row in output["list"]] == [row["tip"] for row in rng["buckets"]]
    assert output["missing"] == 0
    assert aggregate(rng, bars[1:])["list"][0]["close"] is None
