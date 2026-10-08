"""Yahoo Finance fallback after an FMP identity or safe intraday-series miss.

The adapter deliberately verifies the Yahoo quote metadata before requesting
prices.  A numeric Hong Kong code is not enough evidence by itself: the quote
must identify the exact requested symbol as an HKD-listed ETF on HKEX.
"""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars

from market_data.fmp import MarketDataError


HK = ZoneInfo("Asia/Hong_Kong")
MAX_ENVELOPE_REPAIR_RATIO = Decimal("0.005")


@lru_cache(maxsize=1)
def _exchange_calendar():
    return exchange_calendars.get_calendar("XHKG", start="2000-01-01", end="2035-12-31")


def _regular_slots(start, end):
    schedule = _exchange_calendar().schedule.loc[start.isoformat():end.isoformat()]
    slots = set()
    for _day, row in schedule.iterrows():
        intervals = [(row["open"], row["break_start"]), (row["break_end"], row["close"])]
        if row["break_start"] is None or str(row["break_start"]) == "NaT":
            intervals = [(row["open"], row["close"])]
        for first, last in intervals:
            cursor = first.tz_convert(HK).to_pydatetime().replace(tzinfo=None)
            finish = last.tz_convert(HK).to_pydatetime().replace(tzinfo=None)
            while cursor < finish:
                slots.add(cursor)
                cursor += timedelta(minutes=30)
    return slots


class YahooFinanceClient:
    provider = "yfinance"
    adjustment = "split_adjusted"
    intraday_interval = "30min"

    def __init__(self, ticker_factory=None):
        if ticker_factory is None:
            import yfinance

            ticker_factory = yfinance.Ticker
        self.ticker_factory = ticker_factory
        self._tickers = {}

    def instrument(self, code, *, start=None, end=None):
        candidates = list(dict.fromkeys((f"{code}.HK", f"{code.zfill(5)}.HK")))
        received_metadata = False
        for symbol in candidates:
            ticker = self.ticker_factory(symbol)
            try:
                metadata = ticker.get_history_metadata() or {}
            except Exception:  # yfinance exposes provider failures as several exception types
                metadata = {}
            if not metadata:
                try:
                    if start is not None and end is not None:
                        ticker.history(
                            start=start,
                            end=end + timedelta(days=1),
                            interval="1d",
                            auto_adjust=False,
                            actions=False,
                            repair=False,
                            prepost=False,
                            timeout=45,
                        )
                    else:
                        ticker.history(
                            period="1mo",
                            interval="1d",
                            auto_adjust=False,
                            actions=False,
                            repair=False,
                            prepost=False,
                            timeout=45,
                        )
                    metadata = ticker.get_history_metadata() or {}
                except Exception:
                    continue
            received_metadata = True
            exchange = {metadata.get("exchangeName"), metadata.get("fullExchangeName")}
            instrument_type = metadata.get("instrumentType") or metadata.get("quoteType")
            timezone = metadata.get("exchangeTimezoneName") or metadata.get("timezone")
            if (
                metadata.get("symbol") == symbol
                and metadata.get("currency") == "HKD"
                and instrument_type == "ETF"
                and exchange.intersection({"HKG", "HKSE"})
                and timezone in {"Asia/Hong_Kong", "HKT"}
            ):
                self._tickers[symbol] = ticker
                return {
                    "symbol": symbol,
                    "currency": "HKD",
                    "exchange": "HKSE",
                    "name": metadata.get("longName") or metadata.get("shortName"),
                    "timezone": "Asia/Hong_Kong",
                }
        raise MarketDataError(
            "instrument_not_verified" if received_metadata else "provider_unavailable"
        )

    def bars(self, symbol, interval, start, end):
        yahoo_interval = {"1d": "1d", "30min": "30m"}.get(interval)
        if yahoo_interval is None:
            raise MarketDataError("unsupported_interval")
        ticker = self._tickers.get(symbol)
        if ticker is None:
            raise MarketDataError("instrument_not_verified")
        try:
            frame = ticker.history(
                start=start,
                end=end + timedelta(days=1),
                interval=yahoo_interval,
                auto_adjust=False,
                actions=False,
                repair=False,
                prepost=False,
                timeout=45,
            )
        except Exception:
            raise MarketDataError("provider_unavailable") from None
        if frame is None or frame.empty:
            return []
        required = {"Open", "High", "Low", "Close"}
        if not required.issubset(frame.columns):
            raise MarketDataError("invalid_response")

        regular_slots = _regular_slots(start, end) if interval == "30min" else None
        trading_days = {slot.date() for slot in _regular_slots(start, end)}
        output = {}
        invalid_rows = 0
        for index, row in frame.iterrows():
            try:
                timestamp = index.to_pydatetime() if hasattr(index, "to_pydatetime") else index
                if not isinstance(timestamp, datetime):
                    raise ValueError("invalid timestamp")
                if timestamp.tzinfo is not None:
                    timestamp = timestamp.astimezone(HK).replace(tzinfo=None)
                day = timestamp.date()
                if not start <= day <= end:
                    raise ValueError("outside window")
                if interval == "1d":
                    if day not in trading_days:
                        continue
                    timestamp = datetime.combine(day, datetime.min.time())
                elif timestamp not in regular_slots:
                    # Yahoo exposes the HK closing-auction print at 16:00 in
                    # addition to the regular continuous-trading half hours.
                    continue

                values = {
                    field: Decimal(str(row[source]))
                    for field, source in (
                        ("open", "Open"),
                        ("high", "High"),
                        ("low", "Low"),
                        ("close", "Close"),
                    )
                }
                if any(not value.is_finite() or value <= 0 for value in values.values()):
                    raise ValueError("invalid price")
                # Sparse HK quotes occasionally disagree by one tick because
                # Yahoo rounds O/H/L/C independently. Repair only a tiny
                # envelope violation; a materially inconsistent row still
                # fails closed instead of becoming invented market data.
                envelope_high = max(values.values())
                envelope_low = min(values.values())
                if values["high"] < envelope_high:
                    if (envelope_high - values["high"]) / envelope_high > MAX_ENVELOPE_REPAIR_RATIO:
                        raise ValueError("invalid OHLC")
                    values["high"] = envelope_high
                if values["low"] > envelope_low:
                    if (values["low"] - envelope_low) / envelope_low > MAX_ENVELOPE_REPAIR_RATIO:
                        raise ValueError("invalid OHLC")
                    values["low"] = envelope_low
                if not (
                    values["low"] <= min(values["open"], values["close"])
                    <= max(values["open"], values["close"]) <= values["high"]
                ):
                    raise ValueError("invalid OHLC")
                raw_volume = row.get("Volume")
                volume = None
                if raw_volume is not None:
                    parsed_volume = Decimal(str(raw_volume))
                    if parsed_volume.is_finite():
                        if parsed_volume < 0 or parsed_volume != parsed_volume.to_integral_value():
                            raise ValueError("invalid volume")
                        volume = int(parsed_volume)
                record = {
                    "timestamp": timestamp,
                    "session_date": day.isoformat(),
                    **values,
                    "volume": volume,
                }
            except (KeyError, ValueError, TypeError, InvalidOperation, OverflowError):
                invalid_rows += 1
                continue
            if timestamp in output and output[timestamp] != record:
                raise MarketDataError("conflicting_bars")
            output[timestamp] = record
        if not output and invalid_rows:
            raise MarketDataError("all_bars_invalid")
        return [output[key] for key in sorted(output)]
