import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs.backup_sqlite import backup_sqlite


def test_backup_is_a_byte_copy_with_a_sha256_manifest(tmp_path):
    source = tmp_path / "radar.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        conn.executemany("INSERT INTO facts(value) VALUES (?)", [("one",), ("two",)])
    result = backup_sqlite(
        source,
        tmp_path / "backups",
        now=datetime(2026, 10, 2, 1, 2, 3, tzinfo=timezone.utc),
    )
    backup = tmp_path / "backups" / "radar-20261002T010203Z.db"
    assert backup.read_bytes() == source.read_bytes()
    assert result["sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest()
    manifest = json.loads((backup.with_suffix(".db.sha256.json")).read_text(encoding="utf-8"))
    assert manifest["sha256"] == result["sha256"]
    assert manifest["bytes"] == backup.stat().st_size

