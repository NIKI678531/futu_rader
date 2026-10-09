"""Opt-in, read-through MarketInsight relations for a dedicated application database.

SQLite TEMP views keep the existing relational contracts without materializing
source bodies. Only identifiers, qualification indexes and binding metadata are
stored in the application database; model outputs use its ordinary tables.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, event, func, inspect, select
from sqlalchemy.engine import make_url

from . import schema
from .comment_filter import filter_readiness_values, load_comment_filter_config, normalize_source_ticker, source_ticker_to_code
from .comment_routes import COMMENT_ROUTE_VERSION, product_pool_digest, route_rows
from .source_db import make_source_engine, require_separate_databases
from .source_normalization import normalize_feed, timestamp_from_source
from .time_windows import utc_naive_to_hkt


FACT_TABLES = ("feeds", "comments", "mentions", "feed_mentions", "users")
SOURCE_COLUMNS = (
    "feed_id", "stock_id", "feed_type", "posted_at", "author_uid", "author_name",
    "feed_title", "content_text", "like_count", "comment_count", "image_count",
    "raw_json", "scraped_at", "detail_updated_at",
)


def fact_source():
    value = os.getenv("RADAR_FACT_SOURCE", "radar").strip().lower()
    if value not in {"radar", "market_insight"}:
        raise ValueError("RADAR_FACT_SOURCE must be radar or market_insight")
    return value


def _source_path(url):
    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        raise ValueError("Direct source relations currently require a SQLite source snapshot")
    path = Path((parsed.database or "").removeprefix("file:")).resolve()
    if not path.is_file():
        raise ValueError("MarketInsight snapshot does not exist")
    return path


def _stamp(path):
    stat = path.stat()
    wal = Path(str(path) + "-wal")
    wal_stat = wal.stat() if wal.exists() else None
    return json.dumps([stat.st_size, stat.st_mtime_ns,
                       wal_stat.st_size if wal_stat else None,
                       wal_stat.st_mtime_ns if wal_stat else None])


def _identity(path):
    return hashlib.sha256(os.path.normcase(str(path)).encode("utf-8")).hexdigest()


def _dump(value):
    return json.dumps(value, ensure_ascii=False, default=lambda item: item.isoformat(" ", timespec="microseconds"))


def _normalized(*values):
    row = dict(zip(SOURCE_COLUMNS, values[:len(SOURCE_COLUMNS)]))
    code, ticker = values[-2:]
    observed = timestamp_from_source(row["scraped_at"] or row["detail_updated_at"])
    observation = normalize_feed(row, code, observed, source_ticker=ticker)
    return observation


def prepare_source_state(application_url, source_url, *, stocks=None, since=None,
                         complete_through=None, page_size=1000):
    """Create/reconcile an explicitly separate state DB, never copy source bodies."""
    if not application_url or not source_url:
        raise ValueError("Explicit application and source URLs are required")
    if make_url(application_url).database in (None, "", ":memory:"):
        raise ValueError("Preparation requires a dedicated persistent application database file")
    require_separate_databases(application_url, source_url)
    if make_url(application_url).get_backend_name() != "sqlite":
        raise ValueError("SQLite direct source mode requires a dedicated SQLite application database")
    if page_size < 1 or page_size > 10000:
        raise ValueError("page_size must be between 1 and 10000")
    path = _source_path(source_url)
    from . import default_data_dir
    if Path(make_url(application_url).database).resolve() == (default_data_dir() / "radar.db").resolve():
        raise ValueError("Direct source preparation cannot modify the existing default radar.db")
    since_value = timestamp_from_source(since) if since else None
    if since and since_value is None:
        raise ValueError("since must be an ISO date or timestamp")
    complete = date.fromisoformat(str(complete_through)) if complete_through else None
    source = make_source_engine(source_url)
    target = create_engine(application_url, future=True)
    before = _stamp(path)
    try:
        with target.connect() as conn:
            tables = set(inspect(conn).get_table_names())
            if "source_binding" not in tables and tables:
                raise ValueError("Preparation requires an empty dedicated application database")
            if "source_binding" in tables:
                binding = conn.exec_driver_sql("SELECT identity FROM source_binding").scalar_one()
                if binding != _identity(path):
                    raise ValueError("Application state is bound to another source")
                prior_window = conn.exec_driver_sql("SELECT since,complete_through FROM source_binding").one()
                desired_window = (since_value.isoformat(" ") if since_value else None,
                                  complete.isoformat() if complete else None)
                if tuple(prior_window) != desired_window:
                    raise ValueError("Changing the source observation window requires a new isolated state database")
                prior_stamp = conn.exec_driver_sql("SELECT stamp FROM source_binding").scalar_one()
                if prior_stamp != before and conn.exec_driver_sql(
                    "SELECT EXISTS(SELECT 1 FROM annotations) OR EXISTS(SELECT 1 FROM annotation_jobs) "
                    "OR EXISTS(SELECT 1 FROM synthesis_outputs)"
                ).scalar_one():
                    raise ValueError("Changed source with existing AI results requires a new isolated state database")
        schema.metadata.create_all(target)
        from .product_catalog import load_products
        codes = {product["code"] for product in load_products(engine=target)}
        with source.connect() as source_conn, target.begin() as conn:
            available = set(inspect(source_conn).get_table_names())
            if "futu_comments_feeds" not in available:
                raise ValueError("Source is missing futu_comments_feeds")
            columns = {column["name"] for column in inspect(source_conn).get_columns("futu_comments_feeds")}
            missing = set(SOURCE_COLUMNS) - {"author_name", "detail_updated_at"} - columns
            if missing:
                raise ValueError("Source is missing columns: " + ", ".join(sorted(missing)))
            if "futu_comments_stocks" in available:
                stock_rows = source_conn.exec_driver_sql(
                    "SELECT stock_id, ticker FROM futu_comments_stocks"
                ).all()
                mapping = {int(stock_id): normalize_source_ticker(ticker) for stock_id, ticker in stock_rows}
            else:
                if stocks is None:
                    raise ValueError("Source has no stock catalog; supply a verified stock_id/ticker mapping")
                mapping = {int(stock_id): normalize_source_ticker(ticker) for stock_id, ticker in stocks.items()}
            selected = {stock_id: (source_ticker_to_code(ticker), ticker)
                        for stock_id, ticker in mapping.items()
                        if source_ticker_to_code(ticker) in codes}
            if not selected:
                raise ValueError("Stock mapping contains no active products")
            conn.exec_driver_sql("CREATE TABLE IF NOT EXISTS source_binding "
                                 "(identity TEXT PRIMARY KEY, stamp TEXT NOT NULL, since TEXT, complete_through TEXT)")
            conn.exec_driver_sql("CREATE TABLE IF NOT EXISTS source_stocks "
                                 "(stock_id INTEGER PRIMARY KEY, code TEXT NOT NULL, ticker TEXT NOT NULL)")
            conn.exec_driver_sql("CREATE TABLE IF NOT EXISTS source_comment_locator "
                                 "(comment_id INTEGER PRIMARY KEY, feed_id INTEGER NOT NULL)")
            conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_source_comment_feed ON source_comment_locator(feed_id)")
            for table in ("source_stocks", "source_comment_locator", "mentions", "feed_mentions", "comment_product_routes"):
                conn.exec_driver_sql("DELETE FROM " + table)
            conn.exec_driver_sql("INSERT INTO source_stocks VALUES (?, ?, ?)",
                                 [(stock_id, code, ticker) for stock_id, (code, ticker) in selected.items()])
            fields = ", ".join(name if name in columns else "NULL AS " + name for name in SOURCE_COLUMNS)
            count_feeds = count_comments = broken = 0
            latest = None
            now = datetime.utcnow()
            digest = hashlib.sha256()
            for stock_id, (code, ticker) in selected.items():
                cursor = 0
                while True:
                    params = [stock_id, cursor]
                    where = "stock_id = ? AND feed_id > ?"
                    if since_value:
                        where += " AND posted_at >= ?"
                        params.append(since_value.isoformat(" "))
                    params.append(page_size)
                    rows = source_conn.exec_driver_sql(
                        f"SELECT {fields} FROM futu_comments_feeds WHERE {where} ORDER BY feed_id LIMIT ?",
                        tuple(params),
                    ).all()
                    if not rows:
                        break
                    for row in rows:
                        observation = _normalized(*row, code, ticker)
                        feed = observation.feed
                        if feed["posted_at"] is None:
                            raise ValueError("Source contains an invalid parent timestamp")
                        latest = max(latest, feed["posted_at"]) if latest else feed["posted_at"]
                        count_feeds += 1
                        broken += int(feed["raw_json_broken"])
                        digest.update(_dump([feed, observation.comments]).encode("utf-8"))
                        if observation.mentions:
                            conn.execute(schema.mentions.insert(), [
                                {**mention, "in_pool": mention["code"] in codes} for mention in observation.mentions
                            ])
                        if observation.feed_mentions:
                            conn.execute(schema.feed_mentions.insert(), list(observation.feed_mentions))
                        for comment in observation.comments:
                            conn.exec_driver_sql("INSERT INTO source_comment_locator VALUES (?, ?)",
                                                 (comment["comment_id"], feed["feed_id"]))
                            routes = route_rows(comment["comment_id"], feed["feed_id"], feed["title"],
                                                feed["content"], comment["content"], now=now, pool_codes=codes)
                            if routes:
                                conn.execute(schema.comment_product_routes.insert(), routes)
                            count_comments += 1
                    cursor = rows[-1][0]
            if _stamp(path) != before:
                raise RuntimeError("Source changed during preparation; retry on a stable snapshot")
            if complete and (latest is None or complete > utc_naive_to_hkt(latest).date()):
                raise ValueError("Complete-through cannot exceed the observed source date")
            config = load_comment_filter_config()
            values = {
                "fact_source": "market_insight", "source_identity": _identity(path),
                "data_revision": digest.hexdigest(), "ai_input_revision": digest.hexdigest(),
                **filter_readiness_values(config),
                "comment_route_ready": "1", "comment_route_version": COMMENT_ROUTE_VERSION,
                "comment_route_pool_digest": product_pool_digest(codes),
                "source_feed_count": str(count_feeds), "source_comment_count": str(count_comments),
                "source_indexed_at": now.isoformat(" "),
                "source_window_from": utc_naive_to_hkt(since_value).isoformat(" ") if since_value else "",
            }
            if latest:
                values["data_max_ts"] = utc_naive_to_hkt(latest).isoformat(" ")
            if complete:
                values.update(anchor=complete.isoformat(), anchor_ts=f"{complete.isoformat()} 23:59:59")
            else:
                conn.execute(schema.meta_kv.delete().where(schema.meta_kv.c.k.in_(("anchor", "anchor_ts"))))
            for key, value in values.items():
                conn.exec_driver_sql("INSERT INTO meta_kv(k,v) VALUES (?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, value))
            conn.exec_driver_sql("DELETE FROM source_binding")
            conn.exec_driver_sql("INSERT INTO source_binding VALUES (?,?,?,?)",
                                 (_identity(path), before, since_value.isoformat(" ") if since_value else None,
                                  complete.isoformat() if complete else None))
        return {"source": "market_insight", "feeds": count_feeds, "comments": count_comments,
                "brokenJson": broken, "completeThrough": complete.isoformat() if complete else None,
                "copiedBodies": 0, "revision": digest.hexdigest()}
    finally:
        source.dispose()
        target.dispose()


def install_source_relations(engine, source_url):
    path = _source_path(source_url)
    if engine.dialect.name != "sqlite":
        raise ValueError("Direct source relations require a SQLite application database")
    require_separate_databases(engine.url, source_url)
    identity = _identity(path)

    @event.listens_for(engine, "connect")
    def connect(dbapi, _record):
        binding = dbapi.execute("SELECT identity,stamp,since FROM source_binding").fetchone()
        if not binding or binding[0] != identity:
            raise ValueError("Initialize a dedicated state database for this MarketInsight source first")
        if binding[1] != _stamp(path):
            raise ValueError("Source snapshot changed; reconcile its indexes before reading")
        dbapi.execute("ATTACH DATABASE ? AS market_source", (path.as_uri() + "?mode=ro",))
        source_columns = {row[1] for row in dbapi.execute("PRAGMA market_source.table_info(futu_comments_feeds)")}

        @lru_cache(maxsize=32)
        def normalized(*values):
            return _normalized(*values)

        def feed_json(*values):
            return _dump(normalized(*values).feed)

        def comment_json(comment_id, *values):
            for comment in normalized(*values).comments:
                if comment["comment_id"] == comment_id:
                    return _dump(comment)
            return None

        def source_timestamp(value):
            parsed = timestamp_from_source(value)
            return parsed.isoformat(" ", timespec="microseconds") if parsed else None

        dbapi.create_function("radar_feed", len(SOURCE_COLUMNS) + 2, feed_json)
        dbapi.create_function("radar_comment", len(SOURCE_COLUMNS) + 3, comment_json)
        dbapi.create_function("radar_timestamp", 1, source_timestamp, deterministic=True)
        arguments = ",".join("raw." + name if name in source_columns else "NULL" for name in SOURCE_COLUMNS)
        arguments += ",mapping.code,mapping.ticker"
        source_join = "market_source.futu_comments_feeds AS raw JOIN main.source_stocks AS mapping ON mapping.stock_id=raw.stock_id"
        cutoff = ""
        if binding[2]:
            cutoff = " WHERE raw.posted_at >= '" + binding[2].replace("'", "''") + "'"
        direct = {"feed_id": "raw.feed_id", "code": "mapping.code", "source_ticker": "mapping.ticker",
                  "title": "raw.feed_title", "content": "raw.content_text", "feed_type": "raw.feed_type",
                  "posted_at": "radar_timestamp(raw.posted_at)",
                  "like_count": "raw.like_count", "comment_count": "raw.comment_count",
                  "image_count": "raw.image_count"}
        columns = []
        for column in schema.feeds.c:
            expression = direct.get(column.name) or f"json_extract(radar_feed({arguments}), '$.{column.name}')"
            columns.append(expression + " AS " + column.name)
        dbapi.execute("CREATE TEMP VIEW feeds AS SELECT " + ",".join(columns) + " FROM " + source_join + cutoff)
        columns = []
        for column in schema.comments.c:
            expression = "locator." + column.name if column.name in {"comment_id", "feed_id"} else (
                f"json_extract(radar_comment(locator.comment_id,{arguments}), '$.{column.name}')"
            )
            columns.append(expression + " AS " + column.name)
        dbapi.execute("CREATE TEMP VIEW comments AS SELECT " + ",".join(columns)
                  + " FROM main.source_comment_locator AS locator "
                  + "JOIN market_source.futu_comments_feeds AS raw ON raw.feed_id=locator.feed_id "
                  + "JOIN main.source_stocks AS mapping ON mapping.stock_id=raw.stock_id" + cutoff)
        for name in ("mentions", "feed_mentions"):
            dbapi.execute(f"CREATE TEMP VIEW {name} AS SELECT indexed.* FROM main.{name} AS indexed JOIN feeds ON feeds.feed_id=indexed.feed_id")
        authors = ("SELECT author_uid,author_name FROM feeds UNION ALL "
                   "SELECT author_uid,author_name FROM comments")
        fallback = ("SELECT author_uid AS user_id, MAX(author_name) AS nick_name, "
                    "NULL AS follower_num,NULL AS following_num,NULL AS ip_region,NULL AS self_description "
                    f"FROM ({authors}) WHERE author_uid IS NOT NULL")
        user_columns = {row[1] for row in dbapi.execute("PRAGMA market_source.table_info(futu_comments_users)")}
        if user_columns:
            if "user_id" not in user_columns:
                raise ValueError("Source users table is missing user_id")
            profile_columns = ",".join(
                column.name if column.name in user_columns else "NULL AS " + column.name
                for column in schema.users.c
            )
            fallback += " AND author_uid NOT IN (SELECT user_id FROM market_source.futu_comments_users WHERE user_id IS NOT NULL)"
            user_query = f"SELECT {profile_columns} FROM market_source.futu_comments_users UNION ALL "
        else:
            user_query = ""
        dbapi.execute("CREATE TEMP VIEW users AS " + user_query + fallback + " GROUP BY author_uid")
        dbapi.set_authorizer(lambda action, _first, _second, database, _trigger:
                             sqlite3.SQLITE_DENY if database == "market_source" and action in {
                                 sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                                 sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE,
                                 sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_CREATE_INDEX,
                                 sqlite3.SQLITE_DROP_INDEX,
                             } else sqlite3.SQLITE_OK)

    @event.listens_for(engine, "checkout")
    def check_snapshot(dbapi, _record, _proxy):
        binding = dbapi.execute("SELECT stamp FROM main.source_binding").fetchone()
        if not binding or binding[0] != _stamp(path):
            raise ValueError("Source snapshot changed; reconcile before reading")

    @event.listens_for(engine, "before_cursor_execute")
    def guard_source(_connection, _cursor, _statement, _parameters, _context, _many):
        if _stamp(path) != binding_stamp:
            raise ValueError("Source snapshot changed; start a freshly prepared instance")

    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.close()
    binding_stamp = _stamp(path)


def source_state_status(engine):
    with engine.connect() as conn:
        values = dict(conn.execute(select(schema.meta_kv.c.k, schema.meta_kv.c.v)).all())
    return {"status": "succeeded", "mode": "market_insight_direct", "copiedBodies": 0,
            "completeThrough": values.get("anchor"), "dataThrough": values.get("data_max_ts"),
            "revision": values.get("data_revision")}


def audit_source_alignment(legacy_url, source_url, *, stocks=None, page_size=500):
    """Compare every legacy fact with the source without writing either database."""
    if not legacy_url or not source_url:
        raise ValueError("Explicit legacy and source URLs are required")
    if not 1 <= page_size <= 1000:
        raise ValueError("page_size must be between 1 and 1000")
    require_separate_databases(legacy_url, source_url)
    legacy = make_source_engine(legacy_url)
    source = make_source_engine(source_url)
    source_path = _source_path(source_url) if source.dialect.name == "sqlite" else None
    before_source = _stamp(source_path) if source_path else None
    report = {"status": "blocked", "productionReady": False, "readOnly": True,
              "scope": "all_legacy_facts", "tables": {}, "blockers": [],
              "resultInventory": {}}

    def compare_rows(name, expected, actual, primary):
        summary = report["tables"].setdefault(name, {
            "checked": 0, "missing": 0, "extra": 0, "different": 0, "fields": {},
        })
        expected_by_key = {tuple(row[key] for key in primary): row for row in expected}
        actual_by_key = {tuple(row[key] for key in primary): row for row in actual}
        summary["checked"] += len(expected_by_key)
        summary["missing"] += len(expected_by_key.keys() - actual_by_key.keys())
        summary["extra"] += len(actual_by_key.keys() - expected_by_key.keys())
        for key in expected_by_key.keys() & actual_by_key.keys():
            old, current = expected_by_key[key], actual_by_key[key]
            changed = [field for field, value in old.items() if current.get(field) != value]
            summary["different"] += bool(changed)
            for field in changed:
                summary["fields"][field] = summary["fields"].get(field, 0) + 1

    try:
        with legacy.connect() as previous, source.connect() as current:
            for connection in (previous, current):
                if connection.dialect.name == "sqlite":
                    connection.connection.driver_connection.execute("BEGIN")
                else:
                    connection.execution_options(isolation_level="REPEATABLE READ")
            available = set(inspect(current).get_table_names())
            if "futu_comments_feeds" not in available:
                report["blockers"].append("missing_source_table:futu_comments_feeds")
                return report
            raw_table = Table("futu_comments_feeds", MetaData(), autoload_with=current)
            missing = set(SOURCE_COLUMNS) - {"author_name", "detail_updated_at"} - set(raw_table.c.keys())
            if missing:
                report["blockers"].extend("missing_source_column:" + name for name in sorted(missing))
                return report
            if "futu_comments_stocks" in available:
                stock_table = Table("futu_comments_stocks", MetaData(), autoload_with=current)
                stocks = dict(current.execute(select(stock_table.c.stock_id, stock_table.c.ticker)).all())
            if not stocks:
                report["blockers"].append("missing_verified_stock_catalog")
                return report
            mapping = {int(stock_id): normalize_source_ticker(ticker) for stock_id, ticker in stocks.items()}
            from .product_catalog import load_products
            codes = {product["code"] for product in load_products(engine=legacy)}
            cursor = 0
            seen_comments = set()
            while True:
                old_feeds = previous.execute(select(schema.feeds).where(
                    schema.feeds.c.feed_id > cursor
                ).order_by(schema.feeds.c.feed_id).limit(page_size)).mappings().all()
                if not old_feeds:
                    break
                ids = [row["feed_id"] for row in old_feeds]
                raw_rows = current.execute(select(raw_table).where(raw_table.c.feed_id.in_(ids))
                                           .order_by(raw_table.c.feed_id)).mappings().all()
                actual = {name: [] for name in ("feeds", "comments", "mentions", "feed_mentions")}
                for row in raw_rows:
                    ticker = mapping.get(row["stock_id"])
                    if not ticker:
                        report["blockers"].append("unmapped_stock:" + str(row["stock_id"]))
                        continue
                    observation = normalize_feed(dict(row), source_ticker_to_code(ticker),
                                                 row["scraped_at"] or row.get("detail_updated_at"),
                                                 source_ticker=ticker)
                    actual["feeds"].append(observation.feed)
                    for comment in observation.comments:
                        if comment["comment_id"] not in seen_comments:
                            actual["comments"].append(comment)
                            seen_comments.add(comment["comment_id"])
                    actual["mentions"].extend(
                        {**mention, "in_pool": mention["code"] in codes} for mention in observation.mentions
                    )
                    actual["feed_mentions"].extend(observation.feed_mentions)
                compare_rows("feeds", old_feeds, actual["feeds"], ("feed_id",))
                for name in ("comments", "mentions", "feed_mentions"):
                    table = schema.metadata.tables[name]
                    old_rows = previous.execute(select(table).where(table.c.feed_id.in_(ids))).mappings().all()
                    compare_rows(name, old_rows, actual[name], tuple(column.name for column in table.primary_key))
                cursor = ids[-1]
            user_table = Table("futu_comments_users", MetaData(), autoload_with=current) if "futu_comments_users" in available else None
            if user_table is None:
                report["blockers"].append("missing_source_table:futu_comments_users")
                report["missingUserFields"] = {
                    column.name: previous.execute(select(func.count(column))).scalar_one()
                    for column in schema.users.c if column.name != "user_id"
                }
            else:
                missing_user_columns = set(schema.users.c.keys()) - set(user_table.c.keys())
                report["blockers"].extend("missing_user_column:" + name for name in sorted(missing_user_columns))
                if not missing_user_columns:
                    result = previous.execute(select(schema.users).order_by(schema.users.c.user_id)).mappings()
                    for rows in result.partitions(page_size):
                        source_users = current.execute(select(*(user_table.c[column.name] for column in schema.users.c))
                                                       .where(user_table.c.user_id.in_([row["user_id"] for row in rows]))).mappings().all()
                        normalized_users = [dict(row, user_id=str(row["user_id"])) for row in source_users]
                        compare_rows("users", rows, normalized_users, ("user_id",))
            for name in ("annotations", "annotation_evidence", "annotation_jobs", "annotation_runs",
                         "synthesis_outputs", "analysis_scopes", "price_bars", "price_instruments"):
                table = schema.metadata.tables[name]
                report["resultInventory"][name] = previous.execute(select(func.count()).select_from(table)).scalar_one()
        for name, summary in report["tables"].items():
            if any(summary[key] for key in ("missing", "extra", "different")):
                report["blockers"].append("fact_mismatch:" + name)
        if source_path and before_source != _stamp(source_path):
            report["blockers"].append("source_changed_during_audit")
        report["blockers"] = sorted(set(report["blockers"]))
        if not report["blockers"]:
            report["status"] = "legacy_facts_matched"
        return report
    finally:
        legacy.dispose()
        source.dispose()