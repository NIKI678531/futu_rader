"""SQL price reads follow the currently verified market-data instrument."""

from datetime import datetime

import pytest
from sqlalchemy import insert

from conftest import _client
from radar_db.schema import price_bars, price_instruments, price_syncs
from sql_fixture import OWN_CODE, make_sql_provider


def _bar(provider, interval, timestamp, *, open_, high, low, close):
    return {
        "code": OWN_CODE,
        "provider": provider,
        "interval": interval,
        "timestamp": timestamp,
        "adjustment": "split_adjusted",
        "session_date": timestamp.date().isoformat(),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 100,
        "fetched_at": datetime(2026, 8, 26, 0, 0),
    }


def _seed_instrument(provider, active_provider):
    with provider._engine.begin() as conn:
        conn.execute(
            insert(price_instruments),
            {
                "code": OWN_CODE,
                "provider": active_provider,
                "symbol": "3033.HK",
                "currency": "HKD",
                "exchange": "HKSE",
                "name": "Hang Seng TECH Index ETF",
                "timezone": "Asia/Hong_Kong",
                "verified_at": datetime(2026, 8, 26, 0, 0),
            },
        )


@pytest.mark.parametrize(
    ("active_provider", "expected_source", "expected_ohlc"),
    [
        ("fmp", "FMP", (10.0, 14.0, 9.0, 13.0)),
        ("yfinance", "Yahoo Finance", (20.0, 24.0, 19.0, 23.0)),
    ],
)
def test_candles_api_reads_the_active_provider_and_reports_its_source(
    active_provider, expected_source, expected_ohlc
):
    """A stale provider's bars must neither drive the K line nor name its source."""
    provider = make_sql_provider()
    _seed_instrument(provider, active_provider)
    with provider._engine.begin() as conn:
        conn.execute(
            insert(price_bars),
            [
                # FMP keeps two 30-minute facts and the API aggregates them to 1h.
                _bar("fmp", "30min", datetime(2026, 8, 25, 10, 0),
                     open_=10, high=12, low=9, close=11),
                _bar("fmp", "30min", datetime(2026, 8, 25, 10, 30),
                     open_=11, high=14, low=10, close=13),
                # Yahoo Finance is normalized to the same canonical half-hours.
                _bar("yfinance", "30min", datetime(2026, 8, 25, 10, 0),
                     open_=20, high=22, low=19, close=21),
                _bar("yfinance", "30min", datetime(2026, 8, 25, 10, 30),
                     open_=21, high=24, low=20, close=23),
            ],
        )
        conn.execute(
            insert(price_syncs),
            {
                "code": OWN_CODE,
                "interval": "30min",
                "date_from": "2026-08-25",
                "date_to": "2026-08-25",
                "status": "ok",
                "reason": None,
                "row_count": 2,
                "updated_at": datetime(2026, 8, 26, 0, 0),
            },
        )

    with _client(provider) as client:
        response = client.get(f"/api/v1/products/{OWN_CODE}/candles?range=d1")

    assert response.status_code == 200
    data = response.get_json()["data"]
    hour = next(row for row in data["list"] if row["bucket"].startswith("08-25 10:00"))
    assert data["source"] == expected_source
    assert data["adjustment"] == "split_adjusted"
    assert data["reason"] is None
    assert data["granularity"] == "1h"
    assert hour["bucket"] == "08-25 10:00–11:00"
    assert (hour["open"], hour["high"], hour["low"], hour["close"]) == expected_ohlc
    assert hour["note"] == ""


def test_daily_prices_read_the_active_yahoo_finance_instrument():
    """The 42-day daily series uses the same active provider as candles."""
    provider = make_sql_provider()
    _seed_instrument(provider, "yfinance")
    timestamp = datetime(2026, 8, 25)
    with provider._engine.begin() as conn:
        conn.execute(
            insert(price_bars),
            [
                _bar("fmp", "1d", timestamp, open_=10, high=12, low=9, close=11),
                _bar("yfinance", "1d", timestamp, open_=20, high=24, low=19, close=23),
            ],
        )

    day = next(row for row in provider.daily_for(OWN_CODE) if row["iso"] == "2026-08-25")

    assert (day["o"], day["h"], day["l"], day["c"], day["px"]) == (
        20.0,
        24.0,
        19.0,
        23.0,
        23.0,
    )


def test_daily_granularity_candles_report_yahoo_finance_source():
    """Longer ranges keep provenance when the stored interval changes to 1d."""
    provider = make_sql_provider()
    _seed_instrument(provider, "yfinance")
    timestamp = datetime(2026, 8, 25)
    with provider._engine.begin() as conn:
        conn.execute(
            insert(price_bars),
            [
                _bar("fmp", "1d", timestamp, open_=10, high=12, low=9, close=11),
                _bar("yfinance", "1d", timestamp, open_=20, high=24, low=19, close=23),
            ],
        )

    with _client(provider) as client:
        response = client.get(f"/api/v1/products/{OWN_CODE}/candles?range=d7")

    data = response.get_json()["data"]
    day = next(row for row in data["list"] if row["bucket"].startswith("08-25"))
    assert data["source"] == "Yahoo Finance"
    assert data["adjustment"] == "split_adjusted"
    assert data["granularity"] == "1d"
    assert (day["open"], day["high"], day["low"], day["close"], day["note"]) == (
        20.0,
        24.0,
        19.0,
        23.0,
        "",
    )


def test_unverified_instrument_keeps_the_sync_failure_reason_without_claiming_a_source():
    provider = make_sql_provider()
    with provider._engine.begin() as conn:
        conn.execute(
            insert(price_syncs),
            {
                "code": OWN_CODE,
                "interval": "30min",
                "date_from": "2026-08-25",
                "date_to": "2026-08-25",
                "status": "unavailable",
                "reason": "instrument_not_verified",
                "row_count": 0,
                "updated_at": datetime(2026, 8, 26, 0, 0),
            },
        )

    data = provider.candles_for(OWN_CODE, "d1")

    assert data["status"] == "unavailable"
    assert data["reason"] == "instrument_not_verified"
    assert data["source"] is None
    assert data["adjustment"] is None
    assert data["currency"] is None
