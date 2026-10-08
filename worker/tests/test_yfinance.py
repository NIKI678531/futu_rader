from datetime import date

import pandas as pd
import pytest

from market_data.fmp import MarketDataError
from market_data.yahoo import YahooFinanceClient


def test_instrument_requires_the_exact_hong_kong_etf_identity():
    metadata = {
        "7747.HK": {
            "symbol": "7747.HK",
            "currency": "HKD",
            "exchangeName": "HKG",
            "fullExchangeName": "HKSE",
            "instrumentType": "ETF",
            "exchangeTimezoneName": "Asia/Hong_Kong",
            "longName": "CSOP Samsung Electronics Daily (2x) Leveraged Product",
        },
    }

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def get_history_metadata(self):
            return metadata.get(self.symbol, {})

    client = YahooFinanceClient(ticker_factory=Ticker)
    assert client.instrument("7747") == {
        "symbol": "7747.HK",
        "currency": "HKD",
        "exchange": "HKSE",
        "name": "CSOP Samsung Electronics Daily (2x) Leveraged Product",
        "timezone": "Asia/Hong_Kong",
    }

    metadata["7747.HK"]["currency"] = "USD"
    with pytest.raises(MarketDataError, match="instrument_not_verified"):
        client.instrument("7747")


def test_instrument_primes_requested_history_before_retrying_missing_metadata():
    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol
            self.primed = False
            self.history_kwargs = None

        def get_history_metadata(self):
            if not self.primed:
                raise RuntimeError("no current five-day quote")
            return {
                "symbol": self.symbol,
                "currency": "HKD",
                "exchangeName": "HKG",
                "instrumentType": "ETF",
                "exchangeTimezoneName": "Asia/Hong_Kong",
            }

        def history(self, **kwargs):
            self.history_kwargs = kwargs
            self.primed = True
            return pd.DataFrame(
                {"Close": [1]},
                index=pd.DatetimeIndex(["2026-08-25 00:00:00+08:00"]),
            )

    client = YahooFinanceClient(ticker_factory=Ticker)
    instrument = client.instrument(
        "3007", start=date(2026, 8, 1), end=date(2026, 8, 25)
    )

    assert instrument["symbol"] == "3007.HK"
    assert client._tickers["3007.HK"].history_kwargs == {
        "start": date(2026, 8, 1),
        "end": date(2026, 8, 26),
        "interval": "1d",
        "auto_adjust": False,
        "actions": False,
        "repair": False,
        "prepost": False,
        "timeout": 45,
    }


def test_intraday_bars_keep_only_exact_hk_exchange_half_hour_slots():
    timestamps = pd.DatetimeIndex(
        [
            "2026-08-25 09:30:00+08:00",
            "2026-08-25 10:00:00+08:00",
            "2026-08-25 10:30:00+08:00",
            "2026-08-25 11:00:00+08:00",
            "2026-08-25 11:30:00+08:00",
            "2026-08-25 13:00:00+08:00",
            "2026-08-25 13:30:00+08:00",
            "2026-08-25 14:00:00+08:00",
            "2026-08-25 14:30:00+08:00",
            "2026-08-25 15:00:00+08:00",
            "2026-08-25 15:30:00+08:00",
            # Yahoo also exposes the closing-auction print.  It is not one of
            # the regular XHKG half-hour slots consumed by the UI.
            "2026-08-25 16:00:00+08:00",
        ]
    )
    frame = pd.DataFrame(
        {
            "Open": [70 + i for i in range(len(timestamps))],
            "High": [72 + i for i in range(len(timestamps))],
            "Low": [69 + i for i in range(len(timestamps))],
            "Close": [71 + i for i in range(len(timestamps))],
            "Volume": [100 + i for i in range(len(timestamps))],
        },
        index=timestamps,
    )

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol
            self.history_kwargs = None

        def get_history_metadata(self):
            return {
                "symbol": self.symbol,
                "currency": "HKD",
                "exchangeName": "HKG",
                "instrumentType": "ETF",
                "exchangeTimezoneName": "Asia/Hong_Kong",
            }

        def history(self, **kwargs):
            self.history_kwargs = kwargs
            return frame

    client = YahooFinanceClient(ticker_factory=Ticker)
    instrument = client.instrument("7747")
    bars = client.bars(
        instrument["symbol"], "30min", date(2026, 8, 25), date(2026, 8, 25)
    )

    assert [row["timestamp"].strftime("%H:%M") for row in bars] == [
        "09:30", "10:00", "10:30", "11:00", "11:30",
        "13:00", "13:30", "14:00", "14:30", "15:00", "15:30",
    ]
    assert all(row["timestamp"].tzinfo is None for row in bars)
    assert all(row["session_date"] == "2026-08-25" for row in bars)
    assert client._tickers["7747.HK"].history_kwargs == {
        "start": date(2026, 8, 25),
        "end": date(2026, 8, 26),
        "interval": "30m",
        "auto_adjust": False,
        "actions": False,
        "repair": False,
        "prepost": False,
        "timeout": 45,
    }


def test_small_yahoo_ohlc_envelope_rounding_is_repaired_but_corruption_fails():
    frame = pd.DataFrame(
        {
            "Open": [318.4],
            "High": [318.4],
            "Low": [318.4],
            "Close": [318.5],
            "Volume": [50],
        },
        index=pd.DatetimeIndex(["2026-08-25 00:00:00+08:00"]),
    )

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def get_history_metadata(self):
            return {
                "symbol": self.symbol,
                "currency": "HKD",
                "exchangeName": "HKG",
                "instrumentType": "ETF",
                "exchangeTimezoneName": "Asia/Hong_Kong",
            }

        def history(self, **_kwargs):
            return frame

    client = YahooFinanceClient(ticker_factory=Ticker)
    symbol = client.instrument("3007")["symbol"]
    repaired = client.bars(symbol, "1d", date(2026, 8, 25), date(2026, 8, 25))
    assert float(repaired[0]["open"]) == float(repaired[0]["low"]) == 318.4
    assert float(repaired[0]["close"]) == float(repaired[0]["high"]) == 318.5

    frame.loc[:, "High"] = 300
    with pytest.raises(MarketDataError, match="all_bars_invalid"):
        client.bars(symbol, "1d", date(2026, 8, 25), date(2026, 8, 25))


def test_one_corrupt_yahoo_bar_is_quarantined_without_losing_valid_rows():
    frame = pd.DataFrame(
        {
            "Open": [100, 100],
            "High": [102, 50],
            "Low": [99, 99],
            "Close": [101, 101],
            "Volume": [1000, 1000],
        },
        index=pd.DatetimeIndex(
            ["2026-08-24 00:00:00+08:00", "2026-08-25 00:00:00+08:00"]
        ),
    )

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def get_history_metadata(self):
            return {
                "symbol": self.symbol,
                "currency": "HKD",
                "exchangeName": "HKG",
                "instrumentType": "ETF",
                "exchangeTimezoneName": "Asia/Hong_Kong",
            }

        def history(self, **_kwargs):
            return frame

    client = YahooFinanceClient(ticker_factory=Ticker)
    symbol = client.instrument("3007")["symbol"]
    bars = client.bars(symbol, "1d", date(2026, 8, 24), date(2026, 8, 25))

    assert len(bars) == 1
    assert bars[0]["session_date"] == "2026-08-24"
    assert float(bars[0]["close"]) == 101


def test_conflicting_duplicate_yahoo_timestamps_still_fail_closed():
    frame = pd.DataFrame(
        {
            "Open": [100, 110],
            "High": [102, 112],
            "Low": [99, 109],
            "Close": [101, 111],
        },
        index=pd.DatetimeIndex(
            ["2026-08-25 09:30:00+08:00", "2026-08-25 09:30:00+08:00"]
        ),
    )

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        def get_history_metadata(self):
            return {
                "symbol": self.symbol,
                "currency": "HKD",
                "exchangeName": "HKG",
                "instrumentType": "ETF",
                "exchangeTimezoneName": "Asia/Hong_Kong",
            }

        def history(self, **_kwargs):
            return frame

    client = YahooFinanceClient(ticker_factory=Ticker)
    symbol = client.instrument("7747")["symbol"]
    with pytest.raises(MarketDataError, match="conflicting_bars"):
        client.bars(symbol, "30min", date(2026, 8, 25), date(2026, 8, 25))
