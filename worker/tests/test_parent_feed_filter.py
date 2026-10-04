"""Shared parent-feed filter interface and schema contract."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import pytest
from sqlalchemy import create_engine, insert, inspect, select

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from radar_db.comment_filter import (  # noqa: E402
    COMMENT_FILTER_DIGEST_META_KEY,
    COMMENT_FILTER_READY_META_KEY,
    COMMENT_FILTER_VERSION_META_KEY,
    CashtagMention,
    CommentFilterConfigError,
    FilterPolicy,
    extract_feed_mentions,
    filter_readiness_values,
    is_filter_ready,
    load_comment_filter_config,
    normalize_source_ticker,
    qualifies_feed,
    qualifying_feed_predicate,
)
from radar_db.schema import feed_mentions, feeds, metadata  # noqa: E402


def test_extracts_only_strict_parent_cashtags_and_counts_occurrences():
    mentions = extract_feed_mentions(
        "$03037.HK$ / $产品名 (03037.hk)$ / $產品（07226.HK）$",
        "$NVDA.US$ $BRK.B.US$ $.IXIC.US$ $50、普通文本$ $03037$",
    )

    assert mentions == (
        CashtagMention("03037.HK", "HK", 2),
        CashtagMention("07226.HK", "HK", 1),
        CashtagMention("NVDA.US", "US", 1),
        CashtagMention("BRK.B.US", "US", 1),
        CashtagMention(".IXIC.US", "US", 1),
    )


def test_feed_mentions_preserve_first_raw_spelling_while_counting_case_insensitively():
    mentions = extract_feed_mentions(
        "$nvda.us$ / $名称 (NVDA.US)$",
        None,
    )

    assert mentions == (CashtagMention("nvda.us", "US", 2),)
    assert qualifies_feed("NVDA.US", mentions, FilterPolicy("exact"))


def test_title_and_content_are_one_reference_compatible_parsing_stream():
    # opinion-radar concatenates the two parent fields before pairing dollar
    # delimiters.  A dangling title delimiter therefore consumes the opening
    # delimiter in content instead of exposing a false standalone cashtag.
    assert extract_feed_mentions("headline $dangling", "$800000.HK$") == ()


def test_cashtag_mention_rejects_inconsistent_or_nonpositive_facts():
    with pytest.raises(ValueError, match="does not match"):
        CashtagMention("03037.HK", "US", 1)
    with pytest.raises(ValueError, match="positive"):
        CashtagMention("03037.HK", "HK", 0)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("03037.hk", "03037.HK"),
        ("HK.03037", "03037.HK"),
        ("US.NVDA", "NVDA.US"),
        ("us.BRK.B", "BRK.B.US"),
        ("", None),
        (None, None),
        ("03037", None),
    ],
)
def test_normalize_source_ticker(raw, expected):
    assert normalize_source_ticker(raw) == expected


def test_default_config_is_exact_and_3037_excludes_hsi():
    config = load_comment_filter_config()

    assert config.version == "parent-feed-v1"
    assert len(config.digest) == 64
    assert config.policy_for("3033") == FilterPolicy("exact")
    assert config.policy_for("3037") == FilterPolicy("exclude", ("800000.HK",))


def test_config_normalizes_deduplicates_and_hashes_semantics(tmp_path):
    first = {
        "version": "v1",
        "default": {"mode": "exact", "exclude_tickers": []},
        "products": {
            "3037": {
                "mode": "EXCLUDE",
                "exclude_tickers": ["nvda.us", "NVDA.US", "hk.800000"],
            }
        },
    }
    second = {
        "products": {
            "3037": {
                "exclude_tickers": ["NVDA.US", "800000.HK"],
                "mode": "exclude",
            }
        },
        "default": {"exclude_tickers": [], "mode": "exact"},
        "version": "v1",
    }
    paths = []
    for index, payload in enumerate((first, second)):
        path = tmp_path / f"config-{index}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths.append(path)

    left, right = (load_comment_filter_config(path) for path in paths)
    assert left.policy_for("3037").exclude_tickers == ("800000.HK", "NVDA.US")
    assert left.digest == right.digest


@pytest.mark.parametrize(
    "payload",
    [
        {"version": "v1", "default": {"mode": "other"}, "products": {}},
        {
            "version": "v1",
            "default": {"mode": "exact", "exclude_tickers": ["NVDA.US"]},
            "products": {},
        },
        {
            "version": "v1",
            "default": {"mode": "exact"},
            "products": {"3037": {"mode": "exclude", "exclude_tickers": ["NVDA"]}},
        },
        {"version": "v1", "default": {"mode": "exact"}, "products": {}, "extra": 1},
        {
            "version": "v1",
            "default": {"mode": "exact"},
            "products": {"3037": {"mode": "exact"}, " 3037 ": {"mode": "exact"}},
        },
    ],
)
def test_invalid_config_fails_fast(tmp_path, payload):
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(CommentFilterConfigError):
        load_comment_filter_config(path)


def test_pure_qualification_exact_exclude_self_priority_and_fail_closed():
    exact = FilterPolicy("exact")
    exclude = FilterPolicy("exclude", ("800000.hk",))
    open_policy = FilterPolicy("exclude")

    assert qualifies_feed("03037.HK", ["03037.hk"], exact)
    assert not qualifies_feed("03037.HK", ["800000.HK"], exact)
    assert not qualifies_feed(None, ["03037.HK"], exact)
    assert not qualifies_feed("", [], open_policy)
    assert not qualifies_feed("03037.HK", ["800000.HK"], exclude)
    assert qualifies_feed("03037.HK", ["03037.HK", "800000.HK"], exclude)
    assert qualifies_feed("03037.HK", [], exclude)
    assert qualifies_feed("03037.HK", ["800000.HK"], open_policy)
    assert not qualifies_feed(
        "garbage", [], open_policy, source_code="3037"
    )
    assert not qualifies_feed(
        "09999.HK", [], open_policy, source_code="3037"
    )


def test_sql_predicate_matches_pure_rules_and_never_crosses_feed_ids():
    engine = create_engine("sqlite:///:memory:", future=True)
    metadata.create_all(engine, tables=[feeds, feed_mentions])
    rows = [
        _feed(1, "03037.HK"),
        _feed(2, "03037.HK"),
        _feed(3, "03037.HK"),
        _feed(4, None),
        _feed(5, "garbage"),
        _feed(6, "09999.HK"),
    ]
    with engine.begin() as conn:
        conn.execute(insert(feeds), rows)
        conn.execute(
            insert(feed_mentions),
            [
                {"feed_id": 1, "raw_ticker": "03037.hk", "market": "HK", "occurrences": 1},
                {"feed_id": 1, "raw_ticker": "800000.HK", "market": "HK", "occurrences": 1},
                {"feed_id": 2, "raw_ticker": "800000.HK", "market": "HK", "occurrences": 1},
            ],
        )

        exact_ids = conn.execute(
            select(feeds.c.feed_id).where(
                qualifying_feed_predicate(feeds, FilterPolicy("exact"))
            )
        ).scalars().all()
        exclude_ids = conn.execute(
            select(feeds.c.feed_id).where(
                qualifying_feed_predicate(
                    feeds,
                    FilterPolicy("exclude", ("800000.HK",)),
                )
            )
        ).scalars().all()
        open_ids = conn.execute(
            select(feeds.c.feed_id).where(
                qualifying_feed_predicate(feeds, FilterPolicy("exclude"))
            )
        ).scalars().all()
        feed_alias = feeds.alias("parent_feed")
        aliased_ids = conn.execute(
            select(feed_alias.c.feed_id).where(
                qualifying_feed_predicate(feed_alias, FilterPolicy("exact"))
            )
        ).scalars().all()

    assert exact_ids == [1]
    assert exclude_ids == [1, 3]
    assert open_ids == [1, 2, 3]
    assert aliased_ids == [1]


def test_schema_has_parent_filter_columns_key_and_index():
    assert feeds.c.source_ticker.nullable
    assert [column.name for column in feed_mentions.primary_key.columns] == [
        "feed_id",
        "raw_ticker",
    ]
    assert {index.name for index in feed_mentions.indexes} == {
        "ix_feed_mentions_ticker_feed"
    }
    assert [column.name for column in next(iter(feed_mentions.indexes)).columns] == [
        "raw_ticker",
        "feed_id",
    ]


def test_readiness_is_bound_to_version_and_digest():
    config = load_comment_filter_config()
    values = filter_readiness_values(config)

    assert values[COMMENT_FILTER_READY_META_KEY] == "1"
    assert values[COMMENT_FILTER_VERSION_META_KEY] == config.version
    assert values[COMMENT_FILTER_DIGEST_META_KEY] == config.digest
    assert is_filter_ready(values, config)
    assert not is_filter_ready({**values, COMMENT_FILTER_DIGEST_META_KEY: "stale"}, config)
    assert not is_filter_ready({}, config)


def _feed(feed_id: int, source_ticker: str | None) -> dict:
    return {
        "feed_id": feed_id,
        "code": "3037",
        "source_ticker": source_ticker,
        "posted_at": datetime(2026, 8, 25),
        "feed_type": 1,
        "like_count": 0,
        "comment_count": 0,
        "image_count": 0,
        "raw_json_broken": False,
    }
