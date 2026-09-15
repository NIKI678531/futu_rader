from datetime import date

import pytest

from market_data.fmp import FmpClient, MarketDataError, normalize_bars


def test_bars_are_sorted_deduplicated_and_checked():
    row = {"date": "2026-08-25", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 0}
    values = normalize_bars([row, row], "1d", date(2026, 8, 1), date(2026, 8, 25), "3033.HK")
    assert len(values) == 1 and values[0]["volume"] == 0
    for bad in ({**row, "close": 15}, {**row, "date": "2026-08-26"}, {**row, "open": None}):
        with pytest.raises(MarketDataError, match="invalid_bar"):
            normalize_bars([bad], "1d", date(2026, 8, 1), date(2026, 8, 25), "3033.HK")


def test_errors_never_expose_url_or_credentials():
    class Session:
        def get(self, *args, **kwargs):
            class Response:
                status_code = 403
                headers = {}
            return Response()
    with pytest.raises(MarketDataError) as caught:
        FmpClient(api_key="secret-test", session=Session()).get("profile", symbol="3033.HK")
    assert str(caught.value) == "permission_denied"
    assert "secret" not in str(caught.value)