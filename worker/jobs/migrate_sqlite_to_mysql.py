"""Stream the existing Radar SQLite history into an already-migrated MySQL database.

The source defaults to the normal local SQLite path. The destination is read
from ``RADAR_TARGET_DB_URL`` only; connection strings are deliberately not CLI
arguments so Airflow/process listings cannot expose credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, func, inspect, select, text, tuple_, update
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "worker"))

from radar_db import db_url, make_engine
from radar_db.schema import annotation_jobs, meta_kv, metadata


SKIP_TABLES = {"runtime_leases"}


def _expected_alembic_heads():
    config = Config(str(ROOT / "radar_db" / "alembic.ini"))
    return set(ScriptDirectory.from_config(config).get_heads())


def _assert_target_at_head(engine):
    """Refuse a target whose schema has not reached the repository's head."""

    if "alembic_version" not in inspect(engine).get_table_names():
        raise ValueError("Target schema has no alembic_version; run Alembic upgrade head first")
    with engine.connect() as conn:
        current = set(conn.execute(text("SELECT version_num FROM alembic_version")).scalars())
    expected = _expected_alembic_heads()
    if current != expected:
        raise ValueError(
            "Target schema is not at the current Alembic head: "
            f"current={sorted(current)}, expected={sorted(expected)}"
        )


def _manifest(engine):
    existing = set(inspect(engine).get_table_names())
    result = {}
    with engine.connect() as conn:
        for table in metadata.sorted_tables:
            if table.name in existing and table.name not in SKIP_TABLES:
                result[table.name] = conn.execute(select(func.count()).select_from(table)).scalar_one()
    return result


def _sample_hash(conn, table, columns, sample=100):
    primary = list(table.primary_key.columns)
    query = select(*(table.c[name] for name in columns))
    if primary:
        query = query.order_by(*primary)
    rows = conn.execute(query.limit(sample)).all()
    payload = json.dumps([list(row) for row in rows], ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _existing_keys(conn, table, primary, rows):
    if not primary or not rows:
        return set()
    keys = [tuple(row[column.name] for column in primary) for row in rows]
    if len(primary) == 1:
        values = [key[0] for key in keys]
        found = conn.execute(select(primary[0]).where(primary[0].in_(values))).all()
        return {(row[0],) for row in found}
    found = conn.execute(select(*primary).where(tuple_(*primary).in_(keys))).all()
    return {tuple(row) for row in found}


def migrate(source_engine, target_engine, *, batch_size=500, resume=False, progress=print):
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    _assert_target_at_head(target_engine)
    source_names = set(inspect(source_engine).get_table_names())
    target_names = set(inspect(target_engine).get_table_names())
    required = set(metadata.tables) - SKIP_TABLES
    missing_target = required - target_names
    if missing_target:
        raise ValueError(f"Target schema is not at the current Alembic head: {sorted(missing_target)}")

    source_meta = MetaData()
    source_meta.reflect(bind=source_engine, only=list(source_names & set(metadata.tables)))
    before = _manifest(source_engine)
    if not resume:
        nonempty = {}
        with target_engine.connect() as conn:
            for name in sorted(required):
                count = conn.execute(select(func.count()).select_from(metadata.tables[name])).scalar_one()
                if count:
                    nonempty[name] = count
        if nonempty:
            raise ValueError(f"Target is not empty; use --resume only for an interrupted copy: {nonempty}")

    copied = {}
    for target_table in metadata.sorted_tables:
        name = target_table.name
        if name in SKIP_TABLES or name not in source_meta.tables:
            continue
        source_table = source_meta.tables[name]
        common = [column.name for column in target_table.columns if column.name in source_table.c]
        primary = [target_table.c[column.name] for column in source_table.primary_key.columns
                   if column.name in target_table.c]
        read = source_engine.connect().execution_options(stream_results=True, yield_per=batch_size)
        written = 0
        try:
            result = read.execute(select(*(source_table.c[name] for name in common)))
            while True:
                rows = [dict(row._mapping) for row in result.fetchmany(batch_size)]
                if not rows:
                    break
                with target_engine.begin() as write:
                    if resume:
                        existing = _existing_keys(write, target_table, primary, rows)
                        rows = [row for row in rows
                                if tuple(row[column.name] for column in primary) not in existing]
                    if rows:
                        write.execute(target_table.insert(), rows)
                        written += len(rows)
                progress(f"{name}: copied {written:,}")
        finally:
            read.close()
        copied[name] = written

    after = _manifest(target_engine)
    expected_counts = {name: before.get(name, 0) for name in required}
    mismatched = {
        name: {"source": count, "target": after.get(name)}
        for name, count in expected_counts.items()
        if after.get(name) != count
    }
    if mismatched:
        raise RuntimeError(f"Row-count verification failed: {mismatched}")

    samples = {}
    with source_engine.connect() as source, target_engine.connect() as target:
        for name in before:
            source_table = source_meta.tables[name]
            target_table = metadata.tables[name]
            common = [column.name for column in target_table.columns if column.name in source_table.c]
            source_hash = _sample_hash(source, source_table, common)
            target_hash = _sample_hash(target, target_table, common)
            if source_hash != target_hash:
                raise RuntimeError(f"Sample verification failed for {name}")
            samples[name] = source_hash

    # Process-local ownership must never cross machines. Completed jobs remain
    # untouched; only abandoned claims are made eligible for a later resume.
    # Do this after source/target verification because the reset is an intended
    # cutover mutation, not data copied from SQLite.
    with target_engine.begin() as conn:
        conn.execute(update(annotation_jobs).where(annotation_jobs.c.status == "claimed").values(
            status="pending", claimed_at=None, lease_until=None,
        ))
        # The copied facts are byte-for-byte the same logical dataset. Keep its
        # revision so copied scopes/progress remain current after cutover.
        revision = conn.execute(select(meta_kv.c.v).where(
            meta_kv.c.k == "data_revision"
        )).scalar()
    return {"sourceCounts": before, "targetCounts": after, "copied": copied,
            "sampleHashes": samples, "dataRevision": revision}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Migrate Radar SQLite history to an empty MySQL 8 database")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--check", action="store_true", help="only print the source row-count manifest")
    args = parser.parse_args(argv)

    source_url = os.getenv("SOURCE_RADAR_DB_URL", "").strip() or db_url()
    if make_url(source_url).get_backend_name() != "sqlite":
        parser.error("SOURCE_RADAR_DB_URL must point to the SQLite source database")
    source = make_engine(source_url)
    if args.check:
        safe_source = make_url(source_url).render_as_string(hide_password=True)
        print(json.dumps({"source": safe_source, "counts": _manifest(source)}, indent=2))
        return 0
    target_url = os.getenv("RADAR_TARGET_DB_URL", "").strip()
    if not target_url:
        parser.error("RADAR_TARGET_DB_URL is required and must point to the migrated empty MySQL target")
    if make_url(target_url).get_backend_name() != "mysql":
        parser.error("RADAR_TARGET_DB_URL must be MySQL for the production migration command")
    report = migrate(source, make_engine(target_url), batch_size=args.batch_size, resume=args.resume)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
