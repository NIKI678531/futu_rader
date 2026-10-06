from datetime import datetime

from sqlalchemy import create_engine, insert
from sqlalchemy.pool import StaticPool

from core.top_products import top_comment_products
from radar_db.comment_filter import filter_readiness_values, load_comment_filter_config
from radar_db.schema import feed_mentions, feeds, meta_kv, metadata


def test_top_comment_products_requires_the_corresponding_etf_cashtag():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    config = load_comment_filter_config()
    base = {
        "posted_at": datetime(2026, 8, 25, 3),
        "feed_type": 1,
        "like_count": 0,
        "image_count": 0,
        "raw_json_broken": False,
    }
    with engine.begin() as conn:
        conn.execute(
            insert(meta_kv),
            [{"k": key, "v": value} for key, value in filter_readiness_values(config).items()],
        )
        conn.execute(insert(feeds), [
            {**base, "feed_id": 1, "code": "3033", "source_ticker": "03033.HK", "comment_count": 10},
            {**base, "feed_id": 2, "code": "3067", "source_ticker": "03067.HK", "comment_count": 5},
            # A large discussion-section counter without the matching ETF tag
            # must not enter either the leaderboard or the AI selection.
            {**base, "feed_id": 3, "code": "3037", "source_ticker": "03037.HK", "comment_count": 999},
        ])
        conn.execute(insert(feed_mentions), [
            {"feed_id": 1, "raw_ticker": "03033.HK", "market": "HK", "occurrences": 1},
            {"feed_id": 2, "raw_ticker": "03067.HK", "market": "HK", "occurrences": 1},
            {"feed_id": 3, "raw_ticker": "800000.HK", "market": "HK", "occurrences": 1},
        ])

    rows = top_comment_products(engine, "d1", anchor="2026-08-25", limit=2)

    assert rows == [
        {"rank": 1, "code": "3033", "comments": 10},
        {"rank": 2, "code": "3067", "comments": 5},
    ]
