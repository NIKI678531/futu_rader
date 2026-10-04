"""MySQL timestamp precision must match metadata and the upgrade path."""

from __future__ import annotations

from importlib import import_module
from io import StringIO
import os
import sys

import pytest
from sqlalchemy import DateTime
from sqlalchemy.dialects import mysql, sqlite

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from radar_db.schema import metadata  # noqa: E402

pytest.importorskip("alembic")
from alembic.migration import MigrationContext  # noqa: E402
from alembic.operations import Operations  # noqa: E402

migration = import_module(
    "radar_db.migrations.versions.0010_mysql_datetime_precision"
)
routes_migration = import_module(
    "radar_db.migrations.versions.0012_comment_product_routes"
)


def _datetime_columns():
    return {
        (table.name, column.name, column.nullable): column
        for table in metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, DateTime)
    }


def _render_migration(monkeypatch, dialect, direction: str) -> str:
    output = StringIO()
    context = MigrationContext.configure(
        dialect=dialect,
        opts={"as_sql": True, "output_buffer": output},
    )
    monkeypatch.setattr(migration, "op", Operations(context))
    getattr(migration, direction)()
    return output.getvalue()


def test_all_metadata_datetimes_use_microseconds_only_on_mysql():
    columns = _datetime_columns()

    expected = set(migration.DATETIME_COLUMNS) | set(routes_migration.DATETIME_COLUMNS)
    assert set(columns) == expected
    for column in columns.values():
        assert column.type.compile(dialect=mysql.dialect()) == "DATETIME(6)"
        assert column.type.compile(dialect=sqlite.dialect()) == "DATETIME"


def test_mysql_upgrade_alters_every_datetime_and_preserves_nullability(monkeypatch):
    sql = _render_migration(monkeypatch, mysql.dialect(), "upgrade")

    assert sql.count("DATETIME(6)") == len(migration.DATETIME_COLUMNS)
    for table_name, column_name, nullable in migration.DATETIME_COLUMNS:
        null_sql = "NULL" if nullable else "NOT NULL"
        assert (
            f"ALTER TABLE {table_name} CHANGE {column_name} {column_name} "
            f"DATETIME(6) {null_sql}"
        ) in sql


def test_mysql_downgrade_removes_fractional_precision(monkeypatch):
    sql = _render_migration(monkeypatch, mysql.dialect(), "downgrade")

    assert "DATETIME(6)" not in sql
    assert sql.count(" DATETIME ") == len(migration.DATETIME_COLUMNS)


def test_sqlite_upgrade_and_downgrade_are_noops(monkeypatch):
    assert _render_migration(monkeypatch, sqlite.dialect(), "upgrade") == ""
    assert _render_migration(monkeypatch, sqlite.dialect(), "downgrade") == ""
