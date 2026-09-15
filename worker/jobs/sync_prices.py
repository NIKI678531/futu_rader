import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import insert, select, update

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "worker"))

import clock
from ai import config
from market_data.fmp import FmpClient, MarketDataError
from radar_db import make_engine
from radar_db.revisions import bump_revision
from radar_db.schema import meta_kv, price_bars, price_instruments, price_syncs



def _utcnow():
    """行情事实的时间戳历来存 UTC（naive）。读钟走 clock 那扇门（守卫③），再转成 UTC 保持列语义不变。"""
    return clock.now().astimezone(timezone.utc).replace(tzinfo=None)

def upsert(conn, table, row):
    match = [column == row[column.name] for column in table.primary_key.columns]
    if not conn.execute(update(table).where(*match).values(**row)).rowcount:
        conn.execute(insert(table).values(**row))


def sync(engine, client, codes, start, end, intraday_start=None, force=False):
    result = []
    for code in codes:
        try:
            instrument = client.instrument(code)
            with engine.begin() as conn:
                upsert(conn, price_instruments, {"code": code, "provider": "fmp", **instrument,
                                               "verified_at": _utcnow()})
        except MarketDataError as error:
            instrument = None
            identity_error = error.reason
        for interval, first in (("1d", start), ("30min", intraday_start or max(start, end - timedelta(days=1)))):
            cursor = first
            while cursor <= end:
                last = min(end, cursor + timedelta(days=6 if interval == "30min" else 364))
                key = {"code": code, "interval": interval, "date_from": cursor.isoformat(), "date_to": last.isoformat()}
                with engine.connect() as conn:
                    prior = conn.execute(select(price_syncs.c.status).where(*[
                        price_syncs.c[name] == value for name, value in key.items()
                    ])).scalar()
                if prior == "ok" and not force:
                    result.append({**key, "status": "reused"})
                    cursor = last + timedelta(days=1)
                    continue
                try:
                    if instrument is None:
                        raise MarketDataError(identity_error)
                    bars = client.bars(instrument["symbol"], interval, cursor, last)
                    status, reason = ("ok", None) if bars else ("unavailable", "no_data_returned")
                    with engine.begin() as conn:
                        for bar in bars:
                            upsert(conn, price_bars, {
                                "code": code, "provider": "fmp", "interval": interval,
                                "adjustment": "split_adjusted", **bar, "fetched_at": _utcnow(),
                            })
                        upsert(conn, price_syncs, {**key, "status": status, "reason": reason,
                                                  "row_count": len(bars), "updated_at": _utcnow()})
                        bump_revision(conn, "price")
                except MarketDataError as error:
                    bars, status, reason = [], "unavailable", error.reason
                    with engine.begin() as conn:
                        upsert(conn, price_syncs, {**key, "status": status, "reason": reason,
                                                  "row_count": 0, "updated_at": _utcnow()})
                        bump_revision(conn, "price")
                result.append({**key, "status": status, "reason": reason, "rows": len(bars)})
                cursor = last + timedelta(days=1)
        print(json.dumps({"code": code, "sync": result[-2:]}, ensure_ascii=True), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes")
    parser.add_argument("--from", dest="start")
    parser.add_argument("--to", dest="end")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    engine = make_engine()
    with engine.connect() as conn:
        anchor = date.fromisoformat(conn.execute(select(meta_kv.c.v).where(meta_kv.c.k == "anchor")).scalar_one())
    end = date.fromisoformat(args.end) if args.end else anchor
    start = date.fromisoformat(args.start) if args.start else end - timedelta(days=59)
    master = json.loads((ROOT / "backend/fixtures/demo/master.json").read_text(encoding="utf-8"))
    own = [product["code"] for product in master["products"] if product["ownership"] == "own"]
    codes = args.codes.split(",") if args.codes else own
    if not set(codes) <= set(own):
        parser.error("Only configured own products are supported")
    output = sync(engine, FmpClient(), codes, start, end, force=args.force)
    print(json.dumps({"products": len(codes), "ok": sum(row["status"] in ("ok", "reused") for row in output),
                      "unavailable": sum(row["status"] == "unavailable" for row in output)}))


if __name__ == "__main__":
    main()