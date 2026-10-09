"""Prepare an isolated MarketInsight read-through instance without copying bodies."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT, ROOT / "worker"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from radar_db.source_facts import audit_source_alignment, prepare_source_state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stock-catalog", help="JSON object mapping verified stock IDs to tickers")
    parser.add_argument("--stock-dump", help="Same-source SQL dump containing futu_comments_stocks")
    parser.add_argument("--since", help="Optional lower bound for the observation window")
    parser.add_argument("--complete-through", help="Explicit verified complete snapshot day (YYYY-MM-DD)")
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--check", action="store_true", help="Read-only full legacy fact audit; requires SOURCE_RADAR_DB_URL")
    parser.add_argument("--report", help="Write a new JSON audit report; existing files are never overwritten")
    args = parser.parse_args(argv)
    if args.stock_catalog and args.stock_dump:
        parser.error("Choose one stock catalog input")
    if args.report and not args.check:
        parser.error("--report requires --check")
    if args.report and Path(args.report).exists():
        parser.error("Report already exists; choose a new report path")
    stocks = None
    if args.stock_catalog:
        stocks = json.loads(Path(args.stock_catalog).read_text(encoding="utf-8"))
    elif args.stock_dump:
        from jobs.dumpio import DumpReader, to_python
        stocks = {}
        for _table, fields in DumpReader(args.stock_dump, {"futu_comments_stocks": 10}, stop_when_past=True).rows():
            values = to_python(fields)
            stocks[int(values[0])] = values[1]
    try:
        if args.check:
            result = audit_source_alignment(
                os.getenv("SOURCE_RADAR_DB_URL", "").strip(),
                os.getenv("MARKET_INSIGHT_DATABASE_URL", "").strip(),
                stocks=stocks, page_size=args.page_size,
            )
        else:
            result = prepare_source_state(
                os.getenv("RADAR_DB_URL", "").strip(),
                os.getenv("MARKET_INSIGHT_DATABASE_URL", "").strip(),
                stocks=stocks, since=args.since, complete_through=args.complete_through,
                page_size=args.page_size,
            )
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    if args.report:
        with open(args.report, "x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))
    return 2 if result.get("blockers") else 0


if __name__ == "__main__":
    raise SystemExit(main())