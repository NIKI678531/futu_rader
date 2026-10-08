import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, insert, select, update

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "worker"))

from ai import config
from market_data.fmp import FmpClient, MarketDataError
from market_data.yahoo import YahooFinanceClient
from radar_db import make_engine
from radar_db.product_catalog import load_products
from radar_db.revisions import bump_revision
from radar_db.schema import meta_kv, price_bars, price_instruments, price_syncs


def upsert(conn, table, row):
    match = [column == row[column.name] for column in table.primary_key.columns]
    if not conn.execute(update(table).where(*match).values(**row)).rowcount:
        conn.execute(insert(table).values(**row))


def _contract(client):
    return {
        "provider": getattr(client, "provider", "fmp"),
        "adjustment": getattr(client, "adjustment", "split_adjusted"),
        "intraday_interval": getattr(client, "intraday_interval", "30min"),
    }


def _fetch_plan(engine, client, instrument, contract, code, start, end,
                intraday_start, previous_instrument, force, identity_error=None,
                allow_reuse=True):
    identity_unchanged = bool(
        instrument is not None
        and previous_instrument is not None
        and previous_instrument["provider"] == contract["provider"]
        and previous_instrument["symbol"] == instrument["symbol"]
    )
    plan = []
    intervals = (
        ("1d", start),
        (
            contract["intraday_interval"],
            intraday_start or max(start, end - timedelta(days=1)),
        ),
    )
    for interval, first in intervals:
        cursor = first
        while cursor <= end:
            last = min(end, cursor + timedelta(days=6 if interval == "30min" else 364))
            key = {
                "code": code,
                "interval": interval,
                "date_from": cursor.isoformat(),
                "date_to": last.isoformat(),
            }
            with engine.connect() as conn:
                prior = conn.execute(select(price_syncs.c.status).where(*[
                    price_syncs.c[name] == value for name, value in key.items()
                ])).scalar()
                matching_bar = conn.execute(select(price_bars.c.timestamp).where(
                    price_bars.c.code == code,
                    price_bars.c.provider == contract["provider"],
                    price_bars.c.adjustment == contract["adjustment"],
                    price_bars.c.interval == interval,
                    price_bars.c.session_date >= cursor.isoformat(),
                    price_bars.c.session_date <= last.isoformat(),
                ).limit(1)).first()
            if (allow_reuse and prior == "ok" and identity_unchanged
                    and matching_bar is not None and not force):
                plan.append({
                    **key,
                    "provider": contract["provider"],
                    "adjustment": contract["adjustment"],
                    "status": "reused",
                    "bars": None,
                })
                cursor = last + timedelta(days=1)
                continue
            try:
                if instrument is None:
                    raise MarketDataError(identity_error)
                bars = client.bars(instrument["symbol"], interval, cursor, last)
                status, reason = (("ok", None) if bars else
                                  ("unavailable", "no_data_returned"))
            except MarketDataError as error:
                bars, status, reason = [], "unavailable", error.reason
            plan.append({
                **key,
                "provider": contract["provider"],
                "adjustment": contract["adjustment"],
                "status": status,
                "reason": reason,
                "rows": len(bars),
                "bars": bars,
            })
            cursor = last + timedelta(days=1)
    return plan


def _has_safe_missing_intraday_series(plan, intraday_interval):
    """True only when the whole requested intraday series is safely known empty."""
    if any(
        row["status"] == "unavailable" and row.get("reason") != "no_data_returned"
        for row in plan
    ):
        return False
    rows = [row for row in plan if row["interval"] == intraday_interval]
    return bool(rows) and all(
        row["status"] == "unavailable" and row.get("reason") == "no_data_returned"
        for row in rows
    )


def _has_complete_required_series(plan, contract):
    required_intervals = {"1d", contract["intraday_interval"]}
    if {row["interval"] for row in plan} != required_intervals:
        return False
    return all(
        any(row["status"] == "ok" and row["rows"] > 0 for row in plan
            if row["interval"] == interval)
        and all(row["status"] == "ok" for row in plan if row["interval"] == interval)
        for interval in required_intervals
    )


def _would_replace_active(previous_instrument, instrument, contract):
    if previous_instrument is None:
        return False
    if previous_instrument["provider"] != contract["provider"]:
        return True
    return bool(
        instrument is not None
        and previous_instrument["symbol"] != instrument["symbol"]
    )


def _persist_plan(engine, instrument, contract, code, plan, clear_sync_cache=False):
    with engine.begin() as conn:
        if clear_sync_cache:
            # ``price_syncs`` predates multi-provider prices and therefore has
            # no provider key.  Remove the old provider's reuse evidence in
            # the same transaction that activates the replacement provider.
            conn.execute(delete(price_syncs).where(price_syncs.c.code == code))
        if instrument is not None:
            upsert(conn, price_instruments, {
                "code": code,
                "provider": contract["provider"],
                **instrument,
                "verified_at": datetime.utcnow(),
            })
        for row in plan:
            if row["status"] == "reused":
                continue
            for bar in row["bars"]:
                upsert(conn, price_bars, {
                    "code": code,
                    "provider": contract["provider"],
                    "interval": row["interval"],
                    "adjustment": contract["adjustment"],
                    **bar,
                    "fetched_at": datetime.utcnow(),
                })
            key = {
                name: row[name]
                for name in ("code", "interval", "date_from", "date_to")
            }
            upsert(conn, price_syncs, {
                **key,
                "status": row["status"],
                "reason": row.get("reason"),
                "row_count": row["rows"],
                "updated_at": datetime.utcnow(),
            })
            bump_revision(conn, "price")


def sync(engine, client, codes, start, end, intraday_start=None, force=False,
         fallback_client=None):
    result = []
    for code in codes:
        with engine.connect() as conn:
            previous_instrument = conn.execute(select(price_instruments).where(
                price_instruments.c.code == code,
            )).mappings().first()
        selected_client = client
        identity_error = None
        try:
            instrument = client.instrument(code)
        except MarketDataError as error:
            instrument = None
            identity_error = error.reason
            if error.reason == "instrument_not_verified" and fallback_client is not None:
                selected_client = fallback_client
                try:
                    instrument = fallback_client.instrument(code, start=start, end=end)
                except MarketDataError as fallback_error:
                    identity_error = fallback_error.reason
        contract = _contract(selected_client)
        plan = _fetch_plan(
            engine,
            selected_client,
            instrument,
            contract,
            code,
            start,
            end,
            intraday_start,
            previous_instrument,
            force,
            identity_error,
        )

        primary_contract = _contract(client)
        can_try_series_fallback = (
            instrument is not None
            and selected_client is client
            and primary_contract["provider"] == "fmp"
            and fallback_client is not None
            and _has_safe_missing_intraday_series(
                plan, primary_contract["intraday_interval"]
            )
        )
        if can_try_series_fallback:
            try:
                fallback_instrument = fallback_client.instrument(code, start=start, end=end)
                fallback_contract = _contract(fallback_client)
                fallback_plan = _fetch_plan(
                    engine,
                    fallback_client,
                    fallback_instrument,
                    fallback_contract,
                    code,
                    start,
                    end,
                    intraday_start,
                    previous_instrument,
                    True,
                    allow_reuse=False,
                )
                if _has_complete_required_series(fallback_plan, fallback_contract):
                    selected_client = fallback_client
                    instrument = fallback_instrument
                    contract = fallback_contract
                    plan = fallback_plan
            except MarketDataError:
                pass

        replacing_active = _would_replace_active(previous_instrument, instrument, contract)
        candidate_complete = _has_complete_required_series(plan, contract)
        if not replacing_active or candidate_complete:
            _persist_plan(
                engine,
                instrument,
                contract,
                code,
                plan,
                clear_sync_cache=replacing_active,
            )
        public_plan = [{key: value for key, value in row.items() if key != "bars"} for row in plan]
        result.extend(public_plan)
        print(json.dumps({"code": code, "sync": public_plan[-2:]}, ensure_ascii=True), flush=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes")
    parser.add_argument("--from", dest="start")
    parser.add_argument("--to", dest="end")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    engine = make_engine()
    with engine.connect() as conn:
        anchor = date.fromisoformat(conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "anchor")).scalar_one())
    end = date.fromisoformat(args.end) if args.end else anchor
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=59)
    pool_codes = [product["code"] for product in load_products(engine=engine)]
    codes = args.codes.split(",") if args.codes else pool_codes
    if not set(codes) <= set(pool_codes):
        parser.error("Only configured active pool products are supported")
    output = sync(
        engine,
        FmpClient(),
        codes,
        start,
        end,
        force=args.force,
        fallback_client=YahooFinanceClient(),
    )
    print(json.dumps({"products": len(codes), "ok": sum(row["status"] in ("ok", "reused") for row in output),
                      "unavailable": sum(row["status"] == "unavailable" for row in output)}))


if __name__ == "__main__":
    main()
