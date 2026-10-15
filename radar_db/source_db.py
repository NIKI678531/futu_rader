"""Read-only connections to the external MarketInsight source database."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def _parse_url(value):
    try:
        parsed = make_url(value)
    except (ArgumentError, TypeError, ValueError):
        raise ValueError("Invalid database URL; check the environment configuration") from None
    # 本仓库只安装 PyMySQL。部署入口会做同一层规范化，但本地 worker 直接读取
    # ``worker/.env`` 时不会经过 deploy/runtime.py，旧的 mysqldb 写法不能因此炸在导入驱动。
    if parsed.drivername in {"mysql", "mysql+mysqldb"}:
        parsed = parsed.set(drivername="mysql+pymysql")
    return parsed


def _identity(url):
    backend = url.get_backend_name()
    if backend == "sqlite":
        if not url.database or url.database == ":memory:":
            return None
        database = url.database.removeprefix("file:")
        return backend, os.path.normcase(str(Path(database).resolve()))
    if backend == "mysql":
        socket = url.query.get("unix_socket")
        return backend, socket or (url.host or "localhost").lower(), url.port or 3306, url.database
    return backend, url.host, url.port, url.database


def require_separate_databases(application_url, source_url=None):
    configured = source_url or os.getenv("MARKET_INSIGHT_DATABASE_URL", "").strip()
    if not configured:
        return
    application = _identity(_parse_url(application_url))
    source = _identity(_parse_url(configured))
    if application is not None and application == source:
        raise ValueError(
            "RADAR_DB_URL and MARKET_INSIGHT_DATABASE_URL must use separate databases; "
            "application migrations must never target the source"
        )


def make_source_engine(url=None):
    configured = url or os.getenv("MARKET_INSIGHT_DATABASE_URL", "").strip()
    if not configured:
        raise ValueError("Missing MARKET_INSIGHT_DATABASE_URL; configure a read-only source connection")
    parsed = _parse_url(configured)
    backend = parsed.get_backend_name()
    if backend not in {"mysql", "sqlite"}:
        raise ValueError("MarketInsight source must use MySQL or an existing SQLite snapshot")
    application_url = os.getenv("RADAR_DB_URL", "").strip()
    if application_url:
        require_separate_databases(application_url, parsed)
    options = {}
    if backend == "sqlite":
        database = parsed.database
        if not database or database == ":memory:":
            raise ValueError("Source SQLite must point to an existing snapshot file")
        path = Path(database.removeprefix("file:")).resolve()
        if not path.is_file():
            raise ValueError("Source SQLite snapshot does not exist; no database was created")
        parsed = parsed.set(
            database="file:" + path.as_posix(),
            query={**parsed.query, "mode": "ro", "uri": "true"},
        )
    elif parsed.drivername == "mysql+pymysql":
        options["connect_args"] = {"connect_timeout": 10, "read_timeout": 30, "write_timeout": 30}
    engine = create_engine(parsed, future=True, pool_pre_ping=True, hide_parameters=True, **options)

    @event.listens_for(engine, "before_cursor_execute")
    def _reject_mutation(_connection, _cursor, statement, _parameters, _context, _many):
        command = statement.lstrip().split(None, 1)[0].upper() if statement.strip() else ""
        permitted = {"SELECT", "SHOW", "DESCRIBE", "DESC"}
        sqlite_metadata = backend == "sqlite" and command == "PRAGMA" and (
            statement.lstrip().upper().startswith((
                "PRAGMA MAIN.TABLE_", "PRAGMA TEMP.TABLE_", "PRAGMA MAIN.INDEX_",
                "PRAGMA TEMP.INDEX_", "PRAGMA MAIN.FOREIGN_KEY_LIST",
                "PRAGMA TEMP.FOREIGN_KEY_LIST", "PRAGMA DATABASE_LIST",
            )) and "=" not in statement
        )
        if command not in permitted and not sqlite_metadata:
            raise ValueError("MarketInsight source connections only allow read queries")

    @event.listens_for(engine, "connect")
    def _read_only(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(
                "PRAGMA query_only=ON" if backend == "sqlite"
                else "SET SESSION TRANSACTION READ ONLY"
            )
        finally:
            cursor.close()

    return engine
