from datetime import date

import pytest
from sqlalchemy import select

from jobs import sync_prices
from jobs.sync_prices import sync
from market_data.fmp import MarketDataError, normalize_bars
from radar_db import make_engine, create_all
from radar_db.schema import price_bars, price_instruments, price_syncs


def test_sync_is_idempotent_and_failure_preserves_prices(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "prices.db").as_posix())
    create_all(engine)

    class Client:
        fail = False
        calls = 0

        def instrument(self, code):
            return {"symbol": "3033.HK", "exchange": "HKSE", "currency": "HKD", "name": "ETF", "timezone": "Asia/Hong_Kong"}

        def bars(self, symbol, interval, start, end):
            self.calls += 1
            if self.fail:
                raise MarketDataError("permission_denied")
            return normalize_bars([{"date": "2026-08-25", "open": 10, "high": 12, "low": 9, "close": 11}],
                                  interval, start, end, symbol)

    client = Client()
    start = end = date(2026, 8, 25)
    sync(engine, client, ["3033"], start, end)
    sync(engine, client, ["3033"], start, end)
    assert client.calls == 2
    client.fail = True
    sync(engine, client, ["3033"], start, end, force=True)
    with engine.connect() as conn:
        assert len(conn.execute(select(price_bars)).all()) == 2
        assert {row.status for row in conn.execute(select(price_syncs))} == {"unavailable"}


def test_force_intraday_refetches_current_bars_but_reuses_daily_history(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "intraday-refresh.db").as_posix())
    create_all(engine)

    class Client:
        provider = "fmp"
        adjustment = "split_adjusted"
        intraday_interval = "30min"

        def __init__(self):
            self.intervals = []

        def instrument(self, _code):
            return {
                "symbol": "3033.HK", "exchange": "HKSE", "currency": "HKD",
                "name": "ETF", "timezone": "Asia/Hong_Kong",
            }

        def bars(self, symbol, interval, start, end):
            self.intervals.append(interval)
            timestamp = end.isoformat() if interval == "1d" else f"{end.isoformat()}T09:30:00"
            return normalize_bars(
                [{"date": timestamp, "open": 10, "high": 12, "low": 9, "close": 11}],
                interval, start, end, symbol,
            )

    client = Client()
    day = date(2026, 8, 26)
    sync(engine, client, ["3033"], day, day, intraday_start=day)
    client.intervals.clear()

    rows = sync(
        engine,
        client,
        ["3033"],
        day,
        day,
        intraday_start=day,
        force_intraday=True,
    )

    assert client.intervals == ["30min"]
    assert {(row["interval"], row["status"]) for row in rows} == {
        ("1d", "reused"),
        ("30min", "ok"),
    }


def test_sync_falls_back_to_yfinance_only_when_fmp_cannot_verify_instrument(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "fallback.db").as_posix())
    create_all(engine)

    class Fmp:
        provider = "fmp"
        adjustment = "split_adjusted"
        intraday_interval = "30min"

        def instrument(self, _code):
            raise MarketDataError("instrument_not_verified")

        def bars(self, *_args):
            raise AssertionError("FMP bars must not be requested without a verified instrument")

    class Yahoo:
        provider = "yfinance"
        adjustment = "split_adjusted"
        intraday_interval = "30min"
        instrument_calls = 0
        intervals = []

        def instrument(self, code, *, start, end):
            self.instrument_calls += 1
            assert code == "7747"
            assert (start, end) == (date(2026, 8, 25), date(2026, 8, 25))
            return {
                "symbol": "7747.HK",
                "exchange": "HKSE",
                "currency": "HKD",
                "name": "CSOP Samsung Electronics Daily (2x) Leveraged Product",
                "timezone": "Asia/Hong_Kong",
            }

        def bars(self, symbol, interval, start, end):
            assert symbol == "7747.HK"
            self.intervals.append(interval)
            timestamp = "2026-08-25" if interval == "1d" else "2026-08-25T09:30:00"
            return normalize_bars(
                [{"date": timestamp, "open": 70, "high": 72, "low": 69, "close": 71}],
                interval,
                start,
                end,
                symbol,
            )

    yahoo = Yahoo()
    rows = sync(
        engine,
        Fmp(),
        ["7747"],
        date(2026, 8, 25),
        date(2026, 8, 25),
        fallback_client=yahoo,
    )

    assert yahoo.instrument_calls == 1
    assert yahoo.intervals == ["1d", "30min"]
    assert {(row["provider"], row["interval"], row["status"]) for row in rows} == {
        ("yfinance", "1d", "ok"),
        ("yfinance", "30min", "ok"),
    }
    with engine.connect() as conn:
        instrument = conn.execute(select(price_instruments)).mappings().one()
        bars = conn.execute(select(price_bars)).mappings().all()
    assert instrument["provider"] == "yfinance"
    assert instrument["symbol"] == "7747.HK"
    assert {row["provider"] for row in bars} == {"yfinance"}
    assert {row["adjustment"] for row in bars} == {"split_adjusted"}


def test_sync_switches_the_whole_product_to_yahoo_when_fmp_has_no_intraday_series(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "series-fallback.db").as_posix())
    create_all(engine)

    class Fmp:
        provider = "fmp"
        adjustment = "split_adjusted"
        intraday_interval = "30min"

        def __init__(self):
            self.intervals = []

        def instrument(self, code):
            assert code == "7709"
            return {
                "symbol": "7709.HK",
                "exchange": "HKSE",
                "currency": "HKD",
                "name": "CSOP Hang Seng TECH Index Daily (-2x) Inverse Product",
                "timezone": "Asia/Hong_Kong",
            }

        def bars(self, symbol, interval, start, end):
            assert symbol == "7709.HK"
            self.intervals.append(interval)
            if interval == "30min":
                return []
            return normalize_bars(
                [{"date": "2026-08-25", "open": 7, "high": 8, "low": 6, "close": 7.5}],
                interval,
                start,
                end,
                symbol,
            )

    class Yahoo(Fmp):
        provider = "yfinance"

        def __init__(self):
            super().__init__()
            self.instrument_calls = 0

        def instrument(self, code, *, start, end):
            self.instrument_calls += 1
            assert (start, end) == (date(2026, 8, 25), date(2026, 8, 25))
            return super().instrument(code)

        def bars(self, symbol, interval, start, end):
            self.intervals.append(interval)
            timestamp = "2026-08-25" if interval == "1d" else "2026-08-25T09:30:00"
            return normalize_bars(
                [{"date": timestamp, "open": 7, "high": 8, "low": 6, "close": 7.5}],
                interval,
                start,
                end,
                symbol,
            )

    fmp, yahoo = Fmp(), Yahoo()
    rows = sync(
        engine,
        fmp,
        ["7709"],
        date(2026, 8, 25),
        date(2026, 8, 25),
        fallback_client=yahoo,
    )

    assert fmp.intervals == ["1d", "30min"]
    assert yahoo.instrument_calls == 1
    assert yahoo.intervals == ["1d", "30min"]
    assert {(row["provider"], row["interval"], row["status"]) for row in rows} == {
        ("yfinance", "1d", "ok"),
        ("yfinance", "30min", "ok"),
    }
    with engine.connect() as conn:
        instrument = conn.execute(select(price_instruments)).mappings().one()
        active_bars = conn.execute(select(price_bars).where(
            price_bars.c.code == "7709",
            price_bars.c.provider == instrument["provider"],
        )).mappings().all()
    assert instrument["provider"] == "yfinance"
    assert {row["interval"] for row in active_bars} == {"1d", "30min"}


def test_series_fallback_does_not_hide_a_primary_price_error(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "series-error.db").as_posix())
    create_all(engine)

    class Fmp:
        provider = "fmp"
        adjustment = "split_adjusted"
        intraday_interval = "30min"

        def instrument(self, _code):
            return {
                "symbol": "7709.HK",
                "exchange": "HKSE",
                "currency": "HKD",
                "name": "ETF",
                "timezone": "Asia/Hong_Kong",
            }

        def bars(self, _symbol, interval, _start, _end):
            if interval == "1d":
                raise MarketDataError("permission_denied")
            return []

    class Yahoo:
        calls = 0

        def instrument(self, _code, **_kwargs):
            self.calls += 1
            raise AssertionError("unsafe FMP errors must never select another provider")

    yahoo = Yahoo()
    rows = sync(
        engine,
        Fmp(),
        ["7709"],
        date(2026, 8, 25),
        date(2026, 8, 25),
        fallback_client=yahoo,
    )

    assert yahoo.calls == 0
    assert {(row["interval"], row["reason"]) for row in rows} == {
        ("1d", "permission_denied"),
        ("30min", "no_data_returned"),
    }
    with engine.connect() as conn:
        instrument = conn.execute(select(price_instruments)).mappings().one()
    assert instrument["provider"] == "fmp"


def test_series_fallback_does_not_replace_a_sparse_but_real_fmp_series(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "sparse-series.db").as_posix())
    create_all(engine)

    class Fmp:
        provider = "fmp"
        adjustment = "split_adjusted"
        intraday_interval = "30min"

        def instrument(self, _code):
            return {
                "symbol": "3167.HK",
                "exchange": "HKSE",
                "currency": "HKD",
                "name": "ETF",
                "timezone": "Asia/Hong_Kong",
            }

        def bars(self, symbol, interval, start, end):
            timestamp = "2026-08-25" if interval == "1d" else "2026-08-25T09:30:00"
            return normalize_bars(
                [{"date": timestamp, "open": 7, "high": 8, "low": 6, "close": 7.5}],
                interval,
                start,
                end,
                symbol,
            )

    class Yahoo:
        calls = 0

        def instrument(self, _code, **_kwargs):
            self.calls += 1
            raise AssertionError("a sparse real series must stay with FMP")

    yahoo = Yahoo()
    rows = sync(
        engine,
        Fmp(),
        ["3167"],
        date(2026, 8, 25),
        date(2026, 8, 25),
        fallback_client=yahoo,
    )

    assert yahoo.calls == 0
    assert {row["provider"] for row in rows} == {"fmp"}


def test_series_fallback_keeps_fmp_active_when_yahoo_is_also_missing_a_series(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "series-still-missing.db").as_posix())
    create_all(engine)

    class Provider:
        adjustment = "split_adjusted"
        intraday_interval = "30min"

        def instrument_row(self):
            return {
                "symbol": "7709.HK",
                "exchange": "HKSE",
                "currency": "HKD",
                "name": "ETF",
                "timezone": "Asia/Hong_Kong",
            }

        def bars(self, symbol, interval, start, end):
            if interval == "30min":
                return []
            return normalize_bars(
                [{"date": "2026-08-25", "open": 7, "high": 8, "low": 6, "close": 7.5}],
                interval,
                start,
                end,
                symbol,
            )

    class Fmp(Provider):
        provider = "fmp"

        def instrument(self, _code):
            return self.instrument_row()

    class Yahoo(Provider):
        provider = "yfinance"

        def instrument(self, _code, *, start, end):
            assert (start, end) == (date(2026, 8, 25), date(2026, 8, 25))
            return self.instrument_row()

    rows = sync(
        engine,
        Fmp(),
        ["7709"],
        date(2026, 8, 25),
        date(2026, 8, 25),
        fallback_client=Yahoo(),
    )

    assert {row["provider"] for row in rows} == {"fmp"}
    with engine.connect() as conn:
        instrument = conn.execute(select(price_instruments)).mappings().one()
        bars = conn.execute(select(price_bars)).mappings().all()
    assert instrument["provider"] == "fmp"
    assert {row["provider"] for row in bars} == {"fmp"}


def test_incomplete_yahoo_candidate_cannot_replace_an_active_fmp_product(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "preserve-fmp.db").as_posix())
    create_all(engine)

    class Fmp:
        provider = "fmp"
        adjustment = "split_adjusted"
        intraday_interval = "30min"

        def instrument(self, _code):
            return {
                "symbol": "7709.HK", "exchange": "HKSE", "currency": "HKD",
                "name": "ETF", "timezone": "Asia/Hong_Kong",
            }

        def bars(self, symbol, interval, start, end):
            timestamp = "2026-08-25" if interval == "1d" else "2026-08-25T09:30:00"
            return normalize_bars(
                [{"date": timestamp, "open": 7, "high": 8, "low": 6, "close": 7.5}],
                interval, start, end, symbol,
            )

    start = end = date(2026, 8, 25)
    sync(engine, Fmp(), ["7709"], start, end)

    class MissingFmp(Fmp):
        def instrument(self, _code):
            raise MarketDataError("instrument_not_verified")

    class IncompleteYahoo(Fmp):
        provider = "yfinance"

        def instrument(self, _code, *, start, end):
            return super().instrument(_code)

        def bars(self, symbol, interval, start, end):
            return [] if interval == "30min" else super().bars(symbol, interval, start, end)

    sync(engine, MissingFmp(), ["7709"], start, end, fallback_client=IncompleteYahoo())

    with engine.connect() as conn:
        instrument = conn.execute(select(price_instruments)).mappings().one()
        bars = conn.execute(select(price_bars)).mappings().all()
        statuses = conn.execute(select(price_syncs.c.status)).scalars().all()
    assert instrument["provider"] == "fmp"
    assert {row["provider"] for row in bars} == {"fmp"}
    assert set(statuses) == {"ok"}


def test_incomplete_fmp_candidate_cannot_replace_an_active_yahoo_product(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "preserve-yahoo.db").as_posix())
    create_all(engine)

    class Yahoo:
        provider = "yfinance"
        adjustment = "split_adjusted"
        intraday_interval = "30min"

        def instrument(self, _code):
            return {
                "symbol": "7709.HK", "exchange": "HKSE", "currency": "HKD",
                "name": "ETF", "timezone": "Asia/Hong_Kong",
            }

        def bars(self, symbol, interval, start, end):
            timestamp = "2026-08-25" if interval == "1d" else "2026-08-25T09:30:00"
            return normalize_bars(
                [{"date": timestamp, "open": 7, "high": 8, "low": 6, "close": 7.5}],
                interval, start, end, symbol,
            )

    start = end = date(2026, 8, 25)
    sync(engine, Yahoo(), ["7709"], start, end)

    class BrokenFmp(Yahoo):
        provider = "fmp"

        def bars(self, *_args):
            raise MarketDataError("permission_denied")

    class ForbiddenFallback:
        def instrument(self, *_args, **_kwargs):
            raise AssertionError("unsafe FMP errors must not trigger Yahoo probing")

    sync(engine, BrokenFmp(), ["7709"], start, end, fallback_client=ForbiddenFallback())

    with engine.connect() as conn:
        instrument = conn.execute(select(price_instruments)).mappings().one()
        statuses = conn.execute(select(price_syncs.c.status)).scalars().all()
    assert instrument["provider"] == "yfinance"
    assert set(statuses) == {"ok"}


def test_provider_switch_clears_providerless_sync_cache_before_future_reuse(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "providerless-cache.db").as_posix())
    create_all(engine)

    class Provider:
        adjustment = "split_adjusted"
        intraday_interval = "30min"

        def __init__(self, provider):
            self.provider = provider
            self.intervals = []

        def instrument(self, _code):
            return {
                "symbol": "7709.HK", "exchange": "HKSE", "currency": "HKD",
                "name": "ETF", "timezone": "Asia/Hong_Kong",
            }

        def bars(self, symbol, interval, start, end):
            self.intervals.append(interval)
            timestamp = end.isoformat() if interval == "1d" else f"{end.isoformat()}T09:30:00"
            return normalize_bars(
                [{"date": timestamp, "open": 7, "high": 8, "low": 6, "close": 7.5}],
                interval, start, end, symbol,
            )

    long_start, end = date(2026, 8, 1), date(2026, 8, 25)
    sync(engine, Provider("yfinance"), ["7709"], long_start, end, intraday_start=end)

    fmp = Provider("fmp")
    sync(engine, fmp, ["7709"], end, end, intraday_start=end)
    fmp.intervals.clear()
    rows = sync(engine, fmp, ["7709"], long_start, end, intraday_start=end)

    assert fmp.intervals == ["1d"]
    assert {(row["interval"], row["status"]) for row in rows} == {
        ("1d", "ok"),
        ("30min", "reused"),
    }


def test_sync_does_not_hide_a_primary_provider_failure_with_fallback(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "primary-failure.db").as_posix())
    create_all(engine)

    class Fmp:
        provider = "fmp"
        adjustment = "split_adjusted"
        intraday_interval = "30min"

        def instrument(self, _code):
            raise MarketDataError("permission_denied")

    class Yahoo:
        calls = 0

        def instrument(self, _code):
            self.calls += 1
            raise AssertionError("Yahoo is only allowed for an exact FMP identity miss")

    yahoo = Yahoo()
    rows = sync(
        engine,
        Fmp(),
        ["7747"],
        date(2026, 8, 25),
        date(2026, 8, 25),
        fallback_client=yahoo,
    )

    assert yahoo.calls == 0
    assert {(row["provider"], row["status"], row["reason"]) for row in rows} == {
        ("fmp", "unavailable", "permission_denied")
    }


def test_provider_change_never_reuses_bars_from_an_older_provider_run(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "provider-change.db").as_posix())
    create_all(engine)

    class AvailableFmp:
        provider = "fmp"
        adjustment = "split_adjusted"
        intraday_interval = "30min"
        calls = 0

        def instrument(self, _code):
            return {
                "symbol": "7747.HK",
                "exchange": "HKSE",
                "currency": "HKD",
                "name": "ETF",
                "timezone": "Asia/Hong_Kong",
            }

        def bars(self, symbol, interval, start, end):
            self.calls += 1
            timestamp = "2026-08-25" if interval == "1d" else "2026-08-25T09:30:00"
            return normalize_bars(
                [{"date": timestamp, "open": 10, "high": 12, "low": 9, "close": 11}],
                interval,
                start,
                end,
                symbol,
            )

    first_fmp = AvailableFmp()
    sync(engine, first_fmp, ["7747"], date(2026, 8, 25), date(2026, 8, 25))
    assert first_fmp.calls == 2

    class MissingFmp(AvailableFmp):
        def instrument(self, _code):
            raise MarketDataError("instrument_not_verified")

    class Yahoo(AvailableFmp):
        provider = "yfinance"

        def instrument(self, code, *, start, end):
            assert (start, end) == (date(2026, 8, 25), date(2026, 8, 25))
            return super().instrument(code)

    yahoo = Yahoo()
    rows = sync(
        engine,
        MissingFmp(),
        ["7747"],
        date(2026, 8, 25),
        date(2026, 8, 25),
        fallback_client=yahoo,
    )

    assert yahoo.calls == 2
    assert {row["status"] for row in rows} == {"ok"}
    with engine.connect() as conn:
        assert {row.provider for row in conn.execute(select(price_bars))} == {"fmp", "yfinance"}

    restored_fmp = AvailableFmp()
    restored = sync(
        engine,
        restored_fmp,
        ["7747"],
        date(2026, 8, 25),
        date(2026, 8, 25),
    )
    assert restored_fmp.calls == 2
    assert {row["status"] for row in restored} == {"ok"}


def test_cli_defaults_to_every_active_pool_product_including_peers(tmp_path, monkeypatch):
    engine = make_engine("sqlite:///" + (tmp_path / "cli.db").as_posix())
    create_all(engine)
    with engine.begin() as conn:
        from radar_db.schema import meta_kv

        conn.execute(meta_kv.insert().values(k="anchor", v="2026-08-25"))

    products = [
        {"code": "3033", "ownership": "own"},
        {"code": "2800", "ownership": "peer"},
    ]
    captured = {}
    primary, fallback = object(), object()
    monkeypatch.setattr(sync_prices, "make_engine", lambda: engine)
    monkeypatch.setattr(sync_prices, "load_products", lambda **_kwargs: products)
    monkeypatch.setattr(sync_prices, "FmpClient", lambda: primary)
    monkeypatch.setattr(sync_prices, "YahooFinanceClient", lambda: fallback)

    def fake_sync(_engine, client, codes, _start, _end, **kwargs):
        captured.update(client=client, codes=codes, kwargs=kwargs)
        return []

    monkeypatch.setattr(sync_prices, "sync", fake_sync)
    sync_prices.main([])

    assert captured["codes"] == ["3033", "2800"]
    assert captured["client"] is primary
    assert captured["kwargs"]["fallback_client"] is fallback


def test_series_fallback_uses_yahoo_only_after_every_fmp_intraday_chunk_is_empty(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "all-intraday-chunks-empty.db").as_posix())
    create_all(engine)
    start, end = date(2026, 8, 1), date(2026, 8, 15)

    class Fmp:
        provider = "fmp"
        adjustment = "split_adjusted"
        intraday_interval = "30min"

        def __init__(self):
            self.windows = []

        def instrument(self, _code):
            return {
                "symbol": "7709.HK", "exchange": "HKSE", "currency": "HKD",
                "name": "ETF", "timezone": "Asia/Hong_Kong",
            }

        def bars(self, symbol, interval, chunk_start, chunk_end):
            self.windows.append((interval, chunk_start, chunk_end))
            if interval == "30min":
                return []
            return normalize_bars(
                [{"date": chunk_start.isoformat(), "open": 7, "high": 8,
                  "low": 6, "close": 7.5}],
                interval, chunk_start, chunk_end, symbol,
            )

    class Yahoo(Fmp):
        provider = "yfinance"

        def instrument(self, code, *, start, end):
            return super().instrument(code)

        def bars(self, symbol, interval, chunk_start, chunk_end):
            self.windows.append((interval, chunk_start, chunk_end))
            timestamp = (
                chunk_start.isoformat()
                if interval == "1d"
                else f"{chunk_start.isoformat()}T09:30:00"
            )
            return normalize_bars(
                [{"date": timestamp, "open": 7, "high": 8,
                  "low": 6, "close": 7.5}],
                interval, chunk_start, chunk_end, symbol,
            )

    fmp, yahoo = Fmp(), Yahoo()
    rows = sync(
        engine,
        fmp,
        ["7709"],
        start,
        end,
        intraday_start=start,
        fallback_client=yahoo,
    )

    assert [window[1:] for window in fmp.windows if window[0] == "30min"] == [
        (date(2026, 8, 1), date(2026, 8, 7)),
        (date(2026, 8, 8), date(2026, 8, 14)),
        (date(2026, 8, 15), date(2026, 8, 15)),
    ]
    assert [window[1:] for window in yahoo.windows if window[0] == "30min"] == [
        (date(2026, 8, 1), date(2026, 8, 7)),
        (date(2026, 8, 8), date(2026, 8, 14)),
        (date(2026, 8, 15), date(2026, 8, 15)),
    ]
    assert {row["provider"] for row in rows} == {"yfinance"}
    with engine.connect() as conn:
        instrument = conn.execute(select(price_instruments)).mappings().one()
    assert instrument["provider"] == "yfinance"


def test_series_fallback_stays_on_fmp_when_any_intraday_chunk_has_a_bar(tmp_path):
    engine = make_engine("sqlite:///" + (tmp_path / "one-intraday-chunk-present.db").as_posix())
    create_all(engine)
    start, end = date(2026, 8, 1), date(2026, 8, 15)

    class Fmp:
        provider = "fmp"
        adjustment = "split_adjusted"
        intraday_interval = "30min"

        def instrument(self, _code):
            return {
                "symbol": "7709.HK", "exchange": "HKSE", "currency": "HKD",
                "name": "ETF", "timezone": "Asia/Hong_Kong",
            }

        def bars(self, symbol, interval, chunk_start, chunk_end):
            if interval == "30min" and chunk_start != date(2026, 8, 8):
                return []
            timestamp = (
                chunk_start.isoformat()
                if interval == "1d"
                else f"{chunk_start.isoformat()}T09:30:00"
            )
            return normalize_bars(
                [{"date": timestamp, "open": 7, "high": 8,
                  "low": 6, "close": 7.5}],
                interval, chunk_start, chunk_end, symbol,
            )

    class ForbiddenYahoo:
        def instrument(self, *_args, **_kwargs):
            raise AssertionError("one real FMP intraday chunk must prevent fallback")

    rows = sync(
        engine,
        Fmp(),
        ["7709"],
        start,
        end,
        intraday_start=start,
        fallback_client=ForbiddenYahoo(),
    )

    assert {row["provider"] for row in rows} == {"fmp"}
    assert [(row["date_from"], row["status"]) for row in rows if row["interval"] == "30min"] == [
        ("2026-08-01", "unavailable"),
        ("2026-08-08", "ok"),
        ("2026-08-15", "unavailable"),
    ]
    with engine.connect() as conn:
        instrument = conn.execute(select(price_instruments)).mappings().one()
    assert instrument["provider"] == "fmp"


@pytest.mark.parametrize(
    ("failed_interval", "failed_chunk_start"),
    [
        ("1d", date(2026, 8, 1)),
        ("30min", date(2026, 8, 8)),
    ],
)
def test_failed_yahoo_chunk_never_replaces_the_active_fmp_provider(
    tmp_path, failed_interval, failed_chunk_start
):
    engine = make_engine(
        "sqlite:///"
        + (tmp_path / f"failed-yahoo-{failed_interval}-chunk.db").as_posix()
    )
    create_all(engine)

    class SeedFmp:
        provider = "fmp"
        adjustment = "split_adjusted"
        intraday_interval = "30min"

        def instrument(self, _code):
            return {
                "symbol": "7709.HK", "exchange": "HKSE", "currency": "HKD",
                "name": "ETF", "timezone": "Asia/Hong_Kong",
            }

        def bars(self, symbol, interval, start, end):
            timestamp = start.isoformat() if interval == "1d" else f"{start.isoformat()}T09:30:00"
            return normalize_bars(
                [{"date": timestamp, "open": 7, "high": 8,
                  "low": 6, "close": 7.5}],
                interval, start, end, symbol,
            )

    seed_day = date(2025, 7, 31)
    sync(engine, SeedFmp(), ["7709"], seed_day, seed_day)

    class MissingIntradayFmp(SeedFmp):
        def bars(self, symbol, interval, start, end):
            if interval == "30min":
                return []
            return super().bars(symbol, interval, start, end)

    class PartiallyFailedYahoo(SeedFmp):
        provider = "yfinance"

        def __init__(self):
            self.failed_chunk_seen = False

        def instrument(self, code, *, start, end):
            return super().instrument(code)

        def bars(self, symbol, interval, start, end):
            if interval == failed_interval and start == failed_chunk_start:
                self.failed_chunk_seen = True
                raise MarketDataError("provider_unavailable")
            return super().bars(symbol, interval, start, end)

    yahoo = PartiallyFailedYahoo()
    start, end = date(2025, 8, 1), date(2026, 8, 25)
    rows = sync(
        engine,
        MissingIntradayFmp(),
        ["7709"],
        start,
        end,
        intraday_start=date(2026, 8, 1),
        fallback_client=yahoo,
    )

    assert yahoo.failed_chunk_seen is True
    assert {row["provider"] for row in rows} == {"fmp"}
    with engine.connect() as conn:
        instrument = conn.execute(select(price_instruments)).mappings().one()
        yahoo_bar_count = conn.execute(
            select(price_bars).where(price_bars.c.provider == "yfinance")
        ).all()
    assert instrument["provider"] == "fmp"
    assert yahoo_bar_count == []
