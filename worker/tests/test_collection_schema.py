"""在线采集控制面 schema 的方言中立约束。"""

from datetime import date, datetime
import os
import sys

import pytest
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from radar_db import create_all, make_engine  # noqa: E402
from radar_db.schema import (  # noqa: E402
    ai_daily_budget,
    collector_checkpoints,
    feed_counter_observations,
    feeds,
    ingestion_runs,
)


@pytest.fixture()
def engine():
    engine = make_engine("sqlite://")
    create_all(engine)
    return engine


def _feed(**overrides):
    values = {
        "feed_id": 1,
        "code": "3033",
        "posted_at": datetime(2026, 8, 25, 10),
        "feed_type": 1,
        "like_count": 2,
        "comment_count": 3,
        "image_count": 0,
    }
    values.update(overrides)
    return values


def test_feed_coverage_defaults_to_unknown_and_rejects_unknown_enum(engine):
    with engine.begin() as conn:
        conn.execute(insert(feeds).values(**_feed()))
        assert conn.exec_driver_sql(
            "SELECT comment_coverage_status FROM feeds WHERE feed_id = 1"
        ).scalar_one() == "unknown"

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(feeds).values(**_feed(feed_id=2, comment_coverage_status="full")))


def test_source_run_is_idempotent_and_status_is_constrained(engine):
    now = datetime(2026, 9, 25, 10)
    row = {
        "run_id": "sync-1",
        "source": "market_insight",
        "source_run_id": "scheduled__2026-09-25T08:30:00+08:00",
        "source_kind": "all",
        "status": "succeeded",
        "started_at": now,
        "finished_at": now,
    }
    with engine.begin() as conn:
        conn.execute(insert(ingestion_runs).values(**row))

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(ingestion_runs).values(**{**row, "run_id": "sync-2"}))

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(ingestion_runs).values(
            **{**row, "run_id": "sync-3", "source_run_id": "another", "status": "done"}
        ))


def test_checkpoint_requires_timestamp_and_id_to_advance_together(engine):
    with engine.begin() as conn:
        conn.execute(insert(collector_checkpoints).values(
            source="market_insight",
            stream="feeds",
            partition_key="radar-products-v1",
            cursor_at=None,
            cursor_id=None,
            updated_at=datetime(2026, 9, 25, 10),
            config_hash="a" * 64,
        ))

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(collector_checkpoints).values(
            source="market_insight",
            stream="users",
            partition_key="radar-products-v1",
            cursor_at=datetime(2026, 9, 25, 10),
            cursor_id=None,
            updated_at=datetime(2026, 9, 25, 10),
            config_hash="a" * 64,
        ))


def test_daily_budget_is_global_per_date_and_cannot_exceed_limit(engine):
    row = {
        "budget_date": date(2026, 9, 25),
        "policy_hash": "a" * 64,
        "request_limit": 500,
        "requests_used": 499,
        "status": "open",
        "updated_at": datetime(2026, 9, 25, 10),
    }
    with engine.begin() as conn:
        conn.execute(insert(ai_daily_budget).values(**row))

    # Changing policy on the same date must not create a second 500-request allowance.
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(ai_daily_budget).values(**{**row, "policy_hash": "b" * 64}))

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(ai_daily_budget).values(
            **{**row, "budget_date": date(2026, 9, 26), "requests_used": 501}
        ))


def test_counter_observations_are_append_only_by_observation_time(engine):
    observed_at = datetime(2026, 9, 25, 10)
    row = {
        "feed_id": 1,
        "observed_at": observed_at,
        "like_count": 10,
        "comment_count": None,
        "is_settled": False,
    }
    with engine.begin() as conn:
        conn.execute(insert(feed_counter_observations).values(**row))

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(feed_counter_observations).values(**row))

    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(insert(feed_counter_observations).values(
            **{**row, "observed_at": datetime(2026, 9, 25, 11), "like_count": -1}
        ))
