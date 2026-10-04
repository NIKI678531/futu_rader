from datetime import date, datetime

from sqlalchemy import insert

from core.calendar import build
from providers.sql import SqlProvider
from radar_db import create_all
from radar_db.schema import feed_mentions, feeds, mentions
from radar_db.time_windows import hkt_range_utc_naive, utc_naive_to_hkt


def test_hkt_inclusive_date_range_returns_utc_naive_half_open_bounds():
    lo, hi = hkt_range_utc_naive(date(2026, 8, 25), "2026-08-25")

    assert lo == datetime(2026, 8, 24, 16)
    assert hi == datetime(2026, 8, 25, 16)
    assert lo.tzinfo is None and hi.tzinfo is None
    assert utc_naive_to_hkt(lo) == datetime(2026, 8, 25)


def test_sql_scan_uses_hkt_midnight_and_hkt_hour_buckets():
    provider = SqlProvider("sqlite://")
    create_all(provider._engine)
    timestamps = (
        datetime(2026, 8, 24, 15, 59, 59),  # 08-24 23:59:59 HKT: out
        datetime(2026, 8, 24, 16, 0, 0),    # 08-25 00:00:00 HKT: in
        datetime(2026, 8, 25, 15, 59, 59),  # 08-25 23:59:59 HKT: in
        datetime(2026, 8, 25, 16, 0, 0),    # 08-26 00:00:00 HKT: out
    )
    with provider._engine.begin() as conn:
        conn.execute(insert(feeds), [
            {
                "feed_id": i,
                "code": "3033",
                "source_ticker": "03033.HK",
                "posted_at": posted_at,
                "feed_type": 1,
                "author_uid": f"u{i}",
                "like_count": 0,
                "comment_count": i,
                "image_count": 0,
                "share_count": 0,
                "raw_json_broken": False,
            }
            for i, posted_at in enumerate(timestamps, 1)
        ])
        conn.execute(insert(mentions), [
            {"feed_id": i, "code": "3033", "source": "anchor", "in_pool": True}
            for i in range(1, 5)
        ])
        conn.execute(insert(feed_mentions), [
            {"feed_id": i, "raw_ticker": "03033.HK", "market": "HK", "occurrences": 1}
            for i in range(1, 5)
        ])

    rng = build("d1", date(2026, 8, 25))
    observation = provider._scan(rng["from"], rng["to"], rng)["3033"]

    assert observation["comments"] == 5
    assert observation["buckets"][0]["comments"] == 2
    assert observation["buckets"][23]["comments"] == 3
