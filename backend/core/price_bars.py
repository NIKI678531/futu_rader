from datetime import datetime, timedelta
from functools import lru_cache

import exchange_calendars


@lru_cache(maxsize=1)
def exchange_calendar():
    return exchange_calendars.get_calendar("XHKG", start="2000-01-01", end="2035-12-31")


def session_slots(day):
    schedule = exchange_calendar().schedule.loc[day:day]
    if schedule.empty:
        return []
    row = schedule.iloc[0]
    intervals = [(row["open"], row["break_start"]), (row["break_end"], row["close"])]
    if row["break_start"] is None or str(row["break_start"]) == "NaT":
        intervals = [(row["open"], row["close"])]
    result = []
    for start, end in intervals:
        cursor = start.tz_convert("Asia/Hong_Kong").to_pydatetime().replace(tzinfo=None)
        finish = end.tz_convert("Asia/Hong_Kong").to_pydatetime().replace(tzinfo=None)
        while cursor < finish:
            result.append(cursor)
            cursor += timedelta(minutes=30)
    return result


def aggregate_prices(rng, bars, currency="HKD", reason=None):
    granularity = {"hour": "1h", "day": "1d", "week": "1w"}[rng["gran"]]
    index = {row["timestamp"]: row for row in bars}
    output = []
    for bucket in rng["buckets"]:
        first = datetime.fromisoformat(bucket["day"])
        expected = []
        if rng["gran"] == "hour":
            slots = session_slots(bucket["day"])
            expected = [slot for slot in slots if slot.hour == bucket["hour"]]
            note = ("休市日" if not slots else "午间休市" if bucket["hour"] == 12 else "非交易时段")
        else:
            days = 1 if rng["gran"] == "day" else int(bucket["span"])
            for offset in range(days):
                day = first + timedelta(days=offset)
                if session_slots(day.date().isoformat()):
                    expected.append(day)
            note = "休市日" if rng["gran"] == "day" else "整周休市"
        values = {field: None for field in ("open", "high", "low", "close")}
        if expected:
            if all(timestamp in index for timestamp in expected):
                rows = [index[timestamp] for timestamp in expected]
                values = {"open": float(rows[0]["open"]), "high": float(max(row["high"] for row in rows)),
                          "low": float(min(row["low"] for row in rows)), "close": float(rows[-1]["close"])}
                note = ""
            else:
                note = "价格数据暂不可用"
        output.append({"bucket": bucket["tip"], **values, "note": note})
    available = sum(row["close"] is not None for row in output)
    return {
        "status": "ok" if available else "unavailable", "granularity": granularity,
        "granLabel": {"1h": "小时 K", "1d": "日 K", "1w": "周 K"}[granularity],
        "currency": currency, "list": output, "missing": len(output) - available,
        "source": "FMP", "adjustment": "split_adjusted", "reason": reason,
    }