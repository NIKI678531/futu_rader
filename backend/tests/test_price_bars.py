from datetime import date, datetime

from core.calendar import build
from core.price_bars import aggregate_prices, session_slots


def test_hong_kong_sessions_and_hourly_buckets():
    assert session_slots("2026-08-23") == []
    slots = session_slots("2026-08-25")
    assert len(slots) == 11
    assert not any(slot.hour == 12 for slot in slots)
    bars = [{"timestamp": slot, "open": 10, "high": 12, "low": 9, "close": 11} for slot in slots]
    rng = build("d1", date(2026, 8, 25))
    output = aggregate_prices(rng, bars)
    assert len(output["list"]) == 24
    assert output["list"][9]["close"] == 11
    assert output["list"][12]["note"] == "午间休市"
    missing = aggregate_prices(rng, bars[:-1])
    assert missing["list"][15]["close"] is None
    assert missing["list"][15]["note"] == "价格数据暂不可用"


def test_month_week_uses_shared_buckets_and_requires_all_sessions():
    rng = build("mtd", date(2026, 8, 25))
    bars = [{"timestamp": datetime.fromisoformat(day), "open": 10, "high": 12, "low": 9, "close": 11}
            for day in rng["dates"] if session_slots(day)]
    output = aggregate_prices(rng, bars)
    assert [row["bucket"] for row in output["list"]] == [row["tip"] for row in rng["buckets"]]
    assert output["missing"] == 0
    assert aggregate_prices(rng, bars[1:])["list"][0]["close"] is None