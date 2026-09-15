from datetime import date

from sqlalchemy import select

from jobs.sync_prices import sync
from market_data.fmp import MarketDataError, normalize_bars
from radar_db import make_engine, create_all
from radar_db.schema import price_bars, price_syncs


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