import os
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import requests

HK = ZoneInfo("Asia/Hong_Kong")


class MarketDataError(RuntimeError):
    def __init__(self, reason, status=None):
        self.reason, self.status = reason, status
        super().__init__(reason)


class FmpClient:
    def __init__(self, api_key=None, session=None):
        self.api_key = api_key or os.getenv("FMP_API_KEY", "")
        self.base = os.getenv("FMP_BASE_URL", "https://financialmodelingprep.com/stable").rstrip("/")
        if self.base != "https://financialmodelingprep.com/stable":
            raise MarketDataError("unsupported_base_url")
        if not self.api_key:
            raise MarketDataError("missing_credentials")
        self.session = session or requests.Session()

    def get(self, endpoint, **params):
        for attempt in range(4):
            try:
                response = self.session.get(
                    f"{self.base}/{endpoint}", params={**params, "apikey": self.api_key},
                    timeout=(10, 45), allow_redirects=False,
                )
            except requests.RequestException:
                if attempt == 3:
                    raise MarketDataError("network_unavailable") from None
                time.sleep(2 ** attempt)
                continue
            if response.status_code in (401, 403):
                raise MarketDataError("permission_denied", response.status_code)
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 3:
                    retry = response.headers.get("Retry-After", "")
                    time.sleep(min(60, int(retry)) if retry.isdigit() else 2 ** attempt)
                    continue
                raise MarketDataError("rate_limited" if response.status_code == 429 else "provider_unavailable", response.status_code)
            if response.status_code != 200:
                raise MarketDataError("http_error", response.status_code)
            try:
                body = response.json()
            except ValueError:
                raise MarketDataError("invalid_json") from None
            if not isinstance(body, list):
                raise MarketDataError("invalid_response")
            return body
        raise MarketDataError("provider_unavailable")

    def instrument(self, code):
        candidates = list(dict.fromkeys((f"{code}.HK", f"{code.zfill(5)}.HK")))
        for symbol in candidates + candidates:
            rows = self.get("profile", symbol=symbol)
            if not rows:
                continue
            row = rows[0]
            if (row.get("symbol") == symbol and row.get("exchange") == "HKSE"
                    and row.get("currency") == "HKD" and row.get("isEtf") is True):
                return {"symbol": symbol, "currency": "HKD", "exchange": "HKSE",
                        "name": row.get("companyName"), "timezone": "Asia/Hong_Kong"}
        raise MarketDataError("instrument_not_verified")

    def bars(self, symbol, interval, start, end):
        endpoint = "historical-price-eod/full" if interval == "1d" else "historical-chart/30min"
        params = {"symbol": symbol, "from": start.isoformat(), "to": end.isoformat()}
        if interval != "1d":
            params["nonadjusted"] = "false"
        rows = self.get(endpoint, **params)
        if len(rows) >= 5000:
            raise MarketDataError("response_truncated")
        return normalize_bars(rows, interval, start, end, symbol)


def normalize_bars(rows, interval, start, end, symbol):
    output = {}
    for row in rows:
        try:
            if row.get("symbol", symbol) != symbol:
                raise ValueError("wrong symbol")
            timestamp = datetime.fromisoformat(row["date"])
            day = timestamp.date()
            if not start <= day <= end:
                raise ValueError("outside window")
            values = {field: Decimal(str(row[field])) for field in ("open", "high", "low", "close")}
            if any(not value.is_finite() or value <= 0 for value in values.values()):
                raise ValueError("invalid price")
            if not (values["low"] <= min(values["open"], values["close"])
                    <= max(values["open"], values["close"]) <= values["high"]):
                raise ValueError("invalid OHLC")
            volume = row.get("volume")
            if volume is not None and (isinstance(volume, bool) or int(volume) != volume or volume < 0):
                raise ValueError("invalid volume")
            if interval == "1d":
                timestamp = datetime.combine(day, datetime.min.time())
            elif timestamp.tzinfo is not None:
                timestamp = timestamp.astimezone(HK).replace(tzinfo=None)
            record = {"timestamp": timestamp, "session_date": day.isoformat(),
                      **values, "volume": int(volume) if volume is not None else None}
        except (KeyError, ValueError, TypeError, InvalidOperation, OverflowError):
            raise MarketDataError("invalid_bar") from None
        if timestamp in output and output[timestamp] != record:
            raise MarketDataError("conflicting_bars")
        output[timestamp] = record
    return [output[key] for key in sorted(output)]