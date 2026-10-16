from datetime import datetime

from sqlalchemy import create_engine, insert
from sqlalchemy.pool import StaticPool

from core.data_clock import analysis_range_anchor
from core.top_products import analysis_products, top_comment_products
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
        {"rank": 1, "code": "3033", "comments": 1},
        {"rank": 2, "code": "3067", "comments": 1},
    ]


def _analysis_engine(monkeypatch, *, filter_ready=True):
    import core.top_products as top_products

    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata.create_all(engine)
    config = load_comment_filter_config()
    base = {
        "posted_at": datetime(2026, 8, 25, 3), "feed_type": 1, "like_count": 0,
        "image_count": 0, "raw_json_broken": False,
    }
    products = [
        {"code": "3033", "ownership": "own"},
        {"code": "3067", "ownership": "own"},
        {"code": "3037", "ownership": "own"},
        {"code": "2800", "ownership": "peer"},
        {"code": "2828", "ownership": "peer"},
    ]
    monkeypatch.setattr(top_products, "load_products", lambda **_kwargs: products)
    with engine.begin() as conn:
        conn.execute(insert(meta_kv).values(k="anchor", v="2026-08-25"))
        if filter_ready:
            conn.execute(
                insert(meta_kv),
                [{"k": key, "v": value} for key, value in filter_readiness_values(config).items()],
            )
        conn.execute(insert(feeds), [
            {**base, "feed_id": 1, "code": "3033", "source_ticker": "03033.HK", "comment_count": 4},
            {**base, "feed_id": 2, "code": "3067", "source_ticker": "03067.HK", "comment_count": 9},
            {**base, "feed_id": 3, "code": "2828", "source_ticker": "02828.HK", "comment_count": 2},
        ])
        conn.execute(insert(feed_mentions), [
            {"feed_id": 1, "raw_ticker": "03033.HK", "market": "HK", "occurrences": 1},
            {"feed_id": 2, "raw_ticker": "03067.HK", "market": "HK", "occurrences": 1},
            {"feed_id": 3, "raw_ticker": "02828.HK", "market": "HK", "occurrences": 1},
        ])
    return engine


def test_analysis_products_defaults_to_every_product_own_first(monkeypatch):
    from core.top_products import analysis_products

    rows = analysis_products(_analysis_engine(monkeypatch), "d1")

    assert [row["code"] for row in rows] == ["3033", "3067", "3037", "2828", "2800"]
    assert [row["comments"] for row in rows] == [1, 1, 0, 1, 0]


def test_analysis_products_limits_only_own_products(monkeypatch):
    from core.top_products import analysis_products

    rows = analysis_products(_analysis_engine(monkeypatch), "d1", own_limit=1)

    assert [(row["code"], row["ownership"]) for row in rows] == [
        ("3033", "own"), ("2828", "peer"), ("2800", "peer"),
    ]


def test_unlimited_selection_does_not_need_comment_counts(monkeypatch):
    from core.top_products import parse_own_limit

    rows = analysis_products(_analysis_engine(monkeypatch, filter_ready=False), "d1")

    assert [row["code"] for row in rows] == ["3033", "3037", "3067", "2800", "2828"]
    assert {row["comments"] for row in rows} == {None}
    assert parse_own_limit("") is None and parse_own_limit("0") is None
    assert parse_own_limit(" 15 ") == 15


def test_d0_selection_uses_the_latest_observed_hkt_data_day(monkeypatch):
    engine = _analysis_engine(monkeypatch)
    with engine.begin() as conn:
        conn.execute(insert(feeds), {
            "feed_id": 4,
            "code": "3033",
            "source_ticker": "03033.HK",
            # 2026-08-26 04:00 HKT
            "posted_at": datetime(2026, 8, 25, 20),
            "feed_type": 1,
            "like_count": 0,
            "comment_count": 6,
            "image_count": 0,
            "raw_json_broken": False,
        })
        conn.execute(insert(feed_mentions), {
            "feed_id": 4,
            "raw_ticker": "03033.HK",
            "market": "HK",
            "occurrences": 1,
        })

    assert analysis_range_anchor(engine, "d0") == datetime(2026, 8, 26).date()
    rows = analysis_products(engine, "d0")
    assert rows[0]["code"] == "3033"
    assert rows[0]["comments"] == 1


def test_d0_without_a_new_data_day_does_not_publish_fake_zero_counts(monkeypatch):
    rows = analysis_products(_analysis_engine(monkeypatch), "d0")
    assert rows
    assert {row["comments"] for row in rows} == {None}
