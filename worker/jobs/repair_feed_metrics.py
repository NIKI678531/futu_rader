import argparse
import hashlib
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import ijson
from sqlalchemy import insert, select, update

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from radar_db import default_data_dir, make_engine
from radar_db.revisions import bump_revision
from radar_db.schema import feeds, meta_kv, src_feeds

PATHS = ("share_count", "common.share_count", "feed_comm.share_count")


def recover_share(raw):
    values = {}
    closed = set()
    repeated = False
    invalid = False
    try:
        for prefix, event, value in ijson.parse(io.BytesIO((raw or "").encode())):
            if event == "end_map":
                closed.add(prefix)
            if prefix not in PATHS or event in ("map_key", "end_map", "end_array"):
                continue
            if prefix in values:
                repeated = True
            if event != "number" or isinstance(value, bool) or value < 0 or int(value) != value:
                invalid = True
            else:
                values[prefix] = int(value)
    except (ijson.JSONError, ValueError):
        pass
    if repeated or invalid or len(set(values.values())) != 1:
        return None
    complete = [path for path in values if path.rpartition(".")[0] in closed]
    if not complete:
        return None
    return {"value": values[complete[0]], "path": complete[0],
            "sourceHash": hashlib.sha256((raw or "").encode()).hexdigest()}


def run(engine, apply=False):
    report = {"checked": 0, "recoverable": 0, "written": 0, "unrecoverable": [], "recovered": []}
    with engine.connect() as conn:
        records = conn.execute(select(feeds.c.feed_id, src_feeds.c.raw_json).join(
            src_feeds, src_feeds.c.feed_id == feeds.c.feed_id,
        ).where(feeds.c.share_count.is_(None))).all()
    for feed_id, raw in records:
        report["checked"] += 1
        recovered = recover_share(raw)
        if recovered is None:
            report["unrecoverable"].append(feed_id)
            continue
        report["recoverable"] += 1
        report["recovered"].append({"feed_id": feed_id, **recovered})
        if apply:
            with engine.begin() as conn:
                changed = conn.execute(update(feeds).where(feeds.c.feed_id == feed_id,
                                                           feeds.c.share_count.is_(None)).values(share_count=recovered["value"]))
                if changed.rowcount:
                    key = f"metric_repair_{feed_id}"
                    value = json.dumps({**recovered, "at": datetime.utcnow().isoformat() + "Z"})
                    if not conn.execute(update(meta_kv).where(meta_kv.c.k == key).values(v=value)).rowcount:
                        conn.execute(insert(meta_kv).values(k=key, v=value))
                    bump_revision(conn, "data")
                    report["written"] += 1
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = run(make_engine(), args.apply)
    destination = default_data_dir() / "metric-repair-report.json"
    destination.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: (len(value) if isinstance(value, list) else value) for key, value in report.items()}))
    print("Report:", destination)


if __name__ == "__main__":
    main()