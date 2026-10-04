"""Create a frozen byte copy and SHA-256 manifest before the MySQL cutover."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from radar_db import db_url


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def backup_sqlite(source: Path, output_dir: Path, *, now=None) -> dict:
    source = source.resolve(strict=True)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    target = output_dir / f"{source.stem}-{stamp}{source.suffix or '.sqlite'}"
    if target.exists():
        raise FileExistsError(f"Backup already exists: {target}")
    if target.resolve() == source:
        raise ValueError("Backup target must differ from the live SQLite file")

    # Flush WAL pages, then hold an exclusive read/write lock for the duration
    # of the byte copy.  The cutover runbook still requires application writers
    # to be stopped; this lock is the final guard against an accidental writer.
    connection = sqlite3.connect(source)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        connection.execute("BEGIN EXCLUSIVE")
        try:
            shutil.copy2(source, target)
        finally:
            connection.rollback()
    finally:
        connection.close()

    result = {
        "source": str(source),
        "backup": str(target),
        "bytes": target.stat().st_size,
        "sha256": sha256_file(target),
        "createdAt": (now or datetime.now(timezone.utc)).isoformat(),
    }
    manifest = target.with_suffix(target.suffix + ".sha256.json")
    manifest.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["manifest"] = str(manifest)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Freeze, copy and hash the Radar SQLite database")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.getenv("RADAR_BACKUP_DIR", "")) if os.getenv("RADAR_BACKUP_DIR") else None,
    )
    args = parser.parse_args(argv)
    if args.output_dir is None:
        parser.error("--output-dir or RADAR_BACKUP_DIR is required")
    source_url = os.getenv("SOURCE_RADAR_DB_URL", "").strip() or db_url()
    parsed = make_url(source_url)
    if parsed.get_backend_name() != "sqlite" or not parsed.database or parsed.database == ":memory:":
        parser.error("SOURCE_RADAR_DB_URL must point to a file-backed SQLite database")
    result = backup_sqlite(Path(parsed.database), args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

