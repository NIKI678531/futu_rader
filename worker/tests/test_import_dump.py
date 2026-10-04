import argparse
import os
from datetime import date, datetime

import pytest

from jobs import import_dump


@pytest.mark.parametrize(
    "ticker, expected",
    [("03033.HK", "3033"), ("HK.03033", "3033"), ("03033.hk", "3033")],
)
def test_ticker_to_code_accepts_dump_and_marketinsight_forms(ticker, expected):
    assert import_dump.ticker_to_code(ticker) == expected


@pytest.mark.parametrize("ticker", ["03037.US", "US.03037", "03037.SZ", "3037.HK"])
def test_ticker_to_code_rejects_non_hk_or_noncanonical_discussion_tickers(ticker):
    assert import_dump.ticker_to_code(ticker) == ""


def test_pool_codes_can_be_scoped_to_historical_snapshot():
    current = import_dump.pool_codes()
    historical = import_dump.pool_codes(as_of=date(2026, 8, 26))

    assert len(current) == 135
    assert len(historical) == 134
    assert "3408" in current
    assert "3408" not in historical  # listed 2026-09-15, after this dump
    assert "3537" in historical  # listed 2026-07-31, so the dump must cover it


def test_pool_codes_includes_products_listed_on_snapshot_day(monkeypatch):
    monkeypatch.setattr(
        import_dump,
        "load_products",
        lambda: [
            {"code": "1001", "ownership": "own", "listingDate": "2026-08-26"},
            {"code": "1002", "ownership": "peer", "listingDate": "2026-08-27"},
        ],
    )

    assert import_dump.pool_codes(as_of="2026-08-26") == {"1001": "own"}


def test_catalog_as_of_defaults_to_dump_mtime_and_allows_override(tmp_path):
    dump = tmp_path / "historical.sql"
    dump.touch()
    snapshot = datetime(2026, 8, 26, 11, 1)
    os.utime(dump, (snapshot.timestamp(), snapshot.timestamp()))

    assert import_dump.catalog_as_of_for(dump) == date(2026, 8, 26)
    assert import_dump.catalog_as_of_for(dump, date(2026, 8, 25)) == date(2026, 8, 25)


def test_catalog_as_of_cli_value_rejects_non_iso_date():
    with pytest.raises(argparse.ArgumentTypeError, match="YYYY-MM-DD"):
        import_dump._catalog_as_of_arg("26/08/2026")

