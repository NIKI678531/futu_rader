"""Read-only source adapters for the MarketInsight database seam."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import MetaData, Table, and_, func, inspect, or_, select
from sqlalchemy.exc import NoSuchTableError

from radar_db.comment_filter import normalize_source_ticker, source_ticker_to_code

from .models import Cursor, FeedObservation, SourcePage, UserObservation
from .normalization import normalize_feed


STREAMS = ("feeds", "feed_details", "users")
COMPLETION_PROOF_COLUMNS = {
    "configured_symbol_count",
    "configured_symbols_fingerprint",
    "attempted_symbol_count",
    "succeeded_symbol_count",
}


class SourceSchemaError(RuntimeError):
    pass


def inspect_source(engine, codes, *, sample_size=25):
    """Inspect the source contract and bounded samples without reading the application DB."""
    selected = tuple(dict.fromkeys(str(code).strip() for code in codes))
    if not selected or any(len(code) != 4 or not code.isascii() or not code.isdigit() for code in selected):
        raise ValueError("codes must contain four-digit HK product codes")
    if not 1 <= sample_size <= 100:
        raise ValueError("sample_size must be between 1 and 100")

    inspector = inspect(engine)
    report = {
        "source": "market_insight",
        "dialect": engine.dialect.name,
        "status": "blocked",
        "productionReady": False,
        "tables": {},
        "issues": [],
        "samples": {},
        "unverified": [
            "database_account_select_only_grants",
            "source_timestamp_timezone_and_epoch_units",
            "full_dataset_identity_and_comment_completeness",
            "same_snapshot_radar_fact_and_api_parity",
            "backend_and_ai_direct_read_cutover",
        ],
    }
    for name, required in MarketInsightMySqlAdapter._required.items():
        if not inspector.has_table(name):
            report["issues"].append({"table": name, "reason": "missing_table"})
            continue
        columns = inspector.get_columns(name)
        expected = required | ({"detail_updated_at"} if name == "futu_comments_feeds" else set())
        missing = sorted(expected - {column["name"] for column in columns})
        report["tables"][name] = {
            "columns": [
                {"name": column["name"], "type": str(column["type"]), "nullable": column["nullable"]}
                for column in columns
            ],
            "primaryKey": inspector.get_pk_constraint(name).get("constrained_columns", []),
            "indexes": [index.get("column_names", []) for index in inspector.get_indexes(name)],
            "missingRequired": missing,
        }
        if missing:
            report["issues"].append({"table": name, "reason": "missing_columns", "columns": missing})
    if report["issues"]:
        return report

    adapter = MarketInsightMySqlAdapter(engine, set(selected))
    report["incrementalIndexGaps"] = adapter.incremental_index_gaps()
    report["completeThrough"] = adapter.complete_through()
    if report["completeThrough"] is None:
        report["issues"].append({"reason": "no_verified_complete_day"})
    seen_comments = {}
    with engine.connect() as conn:
        if engine.dialect.name == "mysql":
            session = conn.exec_driver_sql(
                "SELECT @@version, @@session.time_zone, @@session.character_set_connection"
            ).one()
            report["session"] = {
                "serverVersion": session[0], "timeZone": session[1], "characterSet": session[2],
            }
        for code in selected:
            stock_ids = tuple(stock_id for stock_id, value in adapter.stock_codes.items() if value == code)
            rows = conn.execute(
                select(adapter.feeds)
                .where(adapter.feeds.c.stock_id.in_(stock_ids))
                .order_by(adapter.feeds.c.feed_id.desc())
                .limit(sample_size)
            ).mappings().all()
            coverage = Counter()
            null_fields = Counter()
            errors = Counter()
            parsed_comments = 0
            for source_row in rows:
                row = dict(source_row)
                try:
                    observed_at = row.get("scraped_at") or row.get("detail_updated_at")
                    if not isinstance(row["posted_at"], datetime) or not isinstance(observed_at, datetime):
                        errors["invalid_feed_timestamp"] += 1
                        continue
                    observation = normalize_feed(
                        row, code, observed_at, source_ticker=adapter.stock_tickers[row["stock_id"]],
                    )
                except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
                    errors["normalization_failed"] += 1
                    continue
                coverage[observation.coverage] += 1
                if observation.feed["raw_json_broken"]:
                    errors["invalid_raw_json"] += 1
                for field in ("title", "content", "author_uid", "author_name", "share_count", "browse_count"):
                    if observation.feed[field] is None:
                        null_fields[field] += 1
                for comment in observation.comments:
                    parsed_comments += 1
                    previous = seen_comments.setdefault(comment["comment_id"], comment["feed_id"])
                    if previous != comment["feed_id"]:
                        errors["comment_id_shared_by_different_feeds"] += 1
                    for field in ("posted_at", "content", "author_uid", "like_count"):
                        if comment[field] is None:
                            null_fields["comment." + field] += 1
            report["samples"][code] = {
                "feeds": len(rows), "parsedComments": parsed_comments,
                "coverage": dict(coverage), "nullFields": dict(null_fields), "errors": dict(errors),
            }
            if not rows or errors:
                report["issues"].append({"code": code, "reason": "sample_missing_or_invalid"})
    report["status"] = "sample_checked" if not report["issues"] else "blocked"
    return report


def configured_symbols_fingerprint(codes: Iterable[str]) -> str:
    """Return the collector/Radar fingerprint for a configured symbol set.

    Both sides hash unique product codes sorted lexicographically and joined by
    ``\n`` without a trailing newline.  The algorithm prefix makes any future
    receipt-format migration explicit.
    """
    normalized = sorted({str(code).strip() for code in codes})
    if not normalized or any(
        not code or "\n" in code or "\r" in code for code in normalized
    ):
        raise ValueError("Configured symbol codes must be non-empty single-line strings")
    payload = "\n".join(normalized).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


class SourceAdapter(Protocol):
    source_id: str

    def high_watermark(
        self, stream: str, through_source_run_id: str | None = None
    ) -> Cursor | None: ...

    def pull_page(
        self, stream: str, after: Cursor | None, through: Cursor | None, limit: int
    ) -> SourcePage: ...

    def pull_feeds_by_id(self, feed_ids: tuple[int, ...]) -> SourcePage: ...

    def complete_through(self, through_source_run_id: str | None = None) -> date | None: ...

    def closing_run(self, hkt_day: date) -> dict | None: ...


def _code_from_ticker(ticker: str) -> str:
    return source_ticker_to_code(ticker) or ""


class MarketInsightMySqlAdapter:
    """Read-only adapter over the existing MarketInsight Futu tables."""

    source_id = "market_insight"

    _required = {
        "futu_comments_stocks": {"stock_id", "ticker"},
        "futu_comments_feeds": {
            "feed_id", "stock_id", "feed_type", "posted_at", "author_uid", "feed_title", "content_text",
            "like_count", "comment_count", "image_count", "raw_json", "scraped_at",
        },
        "futu_comments_users": {
            "user_id", "nick_name", "follower_num", "following_num", "ip_region",
            "self_description", "scraped_at",
        },
    }

    def __init__(self, engine, pool_codes: set[str]):
        self.engine = engine
        metadata = MetaData()
        try:
            self.stocks = Table("futu_comments_stocks", metadata, autoload_with=engine)
            self.feeds = Table("futu_comments_feeds", metadata, autoload_with=engine)
            self.users = Table("futu_comments_users", metadata, autoload_with=engine)
        except Exception as exc:  # reflection errors should explain the source contract, not look transient
            raise SourceSchemaError(f"Cannot reflect MarketInsight source tables: {exc}") from exc
        self.collection_runs = None
        self._run_cache = {}
        try:
            self.collection_runs = Table("futu_comments_collection_runs", metadata, autoload_with=engine)
        except NoSuchTableError:
            pass
        except Exception as exc:
            raise SourceSchemaError(
                f"Cannot reflect MarketInsight collection-run table: {exc}"
            ) from exc
        for table in (self.stocks, self.feeds, self.users):
            missing = self._required[table.name] - set(table.c.keys())
            if missing:
                raise SourceSchemaError(f"{table.name} is missing required columns: {sorted(missing)}")
        if "detail_updated_at" not in self.feeds.c:
            raise SourceSchemaError("futu_comments_feeds is missing required column detail_updated_at")
        with engine.connect() as conn:
            stock_rows = conn.execute(select(self.stocks.c.stock_id, self.stocks.c.ticker)).all()
        self.stock_codes = {row.stock_id: _code_from_ticker(row.ticker) for row in stock_rows}
        self.stock_tickers = {
            row.stock_id: normalize_source_ticker(row.ticker)
            for row in stock_rows
        }
        self.pool_codes = set(pool_codes)
        self.pool_symbols_fingerprint = configured_symbols_fingerprint(self.pool_codes)
        self.pool_stock_ids = tuple(
            stock_id for stock_id, code in self.stock_codes.items() if code in self.pool_codes
        )
        available_codes = self.pool_codes & set(self.stock_codes.values())
        self.available_pool_codes = frozenset(available_codes)
        self.missing_pool_codes = tuple(sorted(self.pool_codes - available_codes))
        # MarketInsight can lag the reviewed Radar catalog while newly listed
        # products are being onboarded.  Synchronize the non-empty intersection
        # and publish the missing set to the target instead of holding every
        # already-covered product offline.
        if not available_codes:
            raise SourceSchemaError("All product pool codes are missing from source")
        self.completion_receipts_available = self.collection_runs is not None
        invalid_tickers = [
            stock_id for stock_id in self.pool_stock_ids
            if source_ticker_to_code(self.stock_tickers.get(stock_id))
            != self.stock_codes.get(stock_id)
        ]
        if invalid_tickers:
            raise SourceSchemaError(
                "Product pool rows contain invalid source tickers for stock_ids: "
                f"{invalid_tickers[:20]}"
            )

    def _stream(self, stream):
        if stream == "feeds":
            return self.feeds, self.feeds.c.scraped_at, self.feeds.c.feed_id
        if stream == "feed_details":
            return self.feeds, self.feeds.c.detail_updated_at, self.feeds.c.feed_id
        if stream == "users":
            return self.users, self.users.c.scraped_at, self.users.c.user_id
        raise ValueError(f"Unknown source stream: {stream}")

    def incremental_index_gaps(self) -> list[str]:
        """Return missing source-side composite indexes for keyset paging."""
        requirements = (
            (self.feeds.name, ("scraped_at", "feed_id")),
            (self.feeds.name, ("detail_updated_at", "feed_id")),
            (self.users.name, ("scraped_at", "user_id")),
        )
        inspector = inspect(self.engine)
        by_table = {}
        gaps = []
        for table_name, columns in requirements:
            if table_name not in by_table:
                by_table[table_name] = [
                    tuple(item.get("column_names") or ())
                    for item in inspector.get_indexes(table_name)
                ]
            if not any(index[:len(columns)] == columns for index in by_table[table_name]):
                gaps.append(f"{table_name}({','.join(columns)})")
        return gaps

    def require_incremental_indexes(self) -> None:
        """Fail before a frequent intraday run can degrade into table scans."""
        gaps = self.incremental_index_gaps()
        if gaps:
            raise SourceSchemaError(
                "MarketInsight is missing required keyset indexes: " + ", ".join(gaps)
            )

    def high_watermark(self, stream, through_source_run_id=None):
        table, timestamp_col, id_col = self._stream(stream)
        query = select(timestamp_col, id_col).where(timestamp_col.isnot(None))
        if through_source_run_id:
            source_run = self._source_run(through_source_run_id)
            boundary = source_run.get("high_watermark_at")
            if boundary is None:
                raise SourceSchemaError(
                    f"Source run {through_source_run_id!r} has no high_watermark_at"
                )
            query = query.where(timestamp_col <= boundary)
        query = query.order_by(timestamp_col.desc(), id_col.desc()).limit(1)
        with self.engine.connect() as conn:
            row = conn.execute(query).first()
        return Cursor(row[0], row[1]) if row else None

    def pull_page(self, stream, after, through, limit):
        if through is None:
            return SourcePage((), after, True)
        table, timestamp_col, id_col = self._stream(stream)
        query = select(table).where(timestamp_col.isnot(None))
        if after is not None:
            query = query.where(or_(timestamp_col > after.observed_at,
                                    and_(timestamp_col == after.observed_at, id_col > after.entity_id)))
        query = query.where(or_(timestamp_col < through.observed_at,
                                and_(timestamp_col == through.observed_at, id_col <= through.entity_id)))
        query = query.order_by(timestamp_col, id_col).limit(limit)
        with self.engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(query).mappings()]
        items = []
        ignored = 0
        for row in rows:
            observed_at = row[timestamp_col.name]
            if stream == "users":
                items.append(UserObservation(
                    {
                        "user_id": str(row["user_id"]),
                        "nick_name": row.get("nick_name"),
                        "follower_num": row.get("follower_num"),
                        "following_num": row.get("following_num"),
                        "ip_region": row.get("ip_region"),
                        "self_description": row.get("self_description"),
                    },
                    observed_at,
                ))
            else:
                code = self.stock_codes[row["stock_id"]]
                if code not in self.pool_codes:
                    ignored += 1
                    continue
                observation = normalize_feed(
                    row,
                    code,
                    observed_at,
                    source_ticker=self.stock_tickers[row["stock_id"]],
                )
                if stream == "feed_details":
                    # Detail runs authoritatively refresh parent text and its
                    # derived body mentions. Counter/comment snapshots still
                    # belong to feeds.
                    observation = replace(observation, comments=())
                items.append(observation)
        cursor = Cursor(rows[-1][timestamp_col.name], rows[-1][id_col.name]) if rows else after
        return SourcePage(tuple(items), cursor, len(rows) < limit, ignored=ignored)

    def pull_feeds_by_id(self, feed_ids):
        """Read the latest feed rows for an explicit repair list.

        This path intentionally normalizes the row as the ``feeds`` stream (and
        therefore includes its comment snapshot), not as ``feed_details`` which
        only refreshes parent text/body mentions. The source database exposes
        no direct reply-page interface, so a truncated embedded snapshot stays
        partial; this method never claims that re-reading the row fetched the
        missing pages.
        """
        requested = tuple(dict.fromkeys(int(feed_id) for feed_id in feed_ids))
        if not requested:
            return SourcePage((), None, True)
        with self.engine.connect() as conn:
            rows = [dict(row) for row in conn.execute(
                select(self.feeds)
                .where(self.feeds.c.feed_id.in_(requested))
                .order_by(self.feeds.c.feed_id)
            ).mappings()]
        items = []
        accepted = set()
        for row in rows:
            code = self.stock_codes[row["stock_id"]]
            if code not in self.pool_codes:
                continue
            observed_at = row.get("scraped_at") or row.get("detail_updated_at")
            if observed_at is None:
                continue
            items.append(normalize_feed(
                row,
                code,
                observed_at,
                source_ticker=self.stock_tickers[row["stock_id"]],
            ))
            accepted.add(int(row["feed_id"]))
        return SourcePage(
            tuple(items),
            None,
            True,
            ignored=len(set(requested) - accepted),
        )

    def _verified_completion_conditions(self, conn, table, claim_conditions):
        """Return proof predicates, or fail closed for an unverifiable legacy claim.

        A source without any full-completion claim may still serve partial runs
        while its receipt schema is being upgraded. Once a row claims a full
        day, however, all proof columns are mandatory.
        """
        missing = COMPLETION_PROOF_COLUMNS - set(table.c.keys())
        if missing:
            claimed_run = conn.execute(
                select(table.c.run_id).where(*claim_conditions).limit(1)
            ).scalar()
            if claimed_run is not None:
                raise SourceSchemaError(
                    "futu_comments_collection_runs has an unverifiable completion claim "
                    f"for {claimed_run!r}; missing completion-proof columns: {sorted(missing)}"
                )
            return None

        configured = table.c.configured_symbol_count
        fingerprint = table.c.configured_symbols_fingerprint
        return (
            configured > 0,
            configured == len(self.pool_codes),
            fingerprint.isnot(None),
            func.length(func.trim(fingerprint)) > 0,
            fingerprint == self.pool_symbols_fingerprint,
            table.c.attempted_symbol_count == configured,
            table.c.succeeded_symbol_count == configured,
        )

    def complete_through(self, through_source_run_id=None):
        if self.collection_runs is None:
            return None
        table = self.collection_runs
        needed = {"run_id", "collection_kind", "status", "complete_through", "finished_at"}
        if needed - set(table.c.keys()):
            raise SourceSchemaError("futu_comments_collection_runs has an incompatible schema")
        claim_conditions = [
            table.c.collection_kind == "comments_all",
            table.c.status == "succeeded",
            table.c.complete_through.isnot(None),
        ]
        if through_source_run_id:
            claim_conditions.append(table.c.run_id == through_source_run_id)
        with self.engine.connect() as conn:
            proof_conditions = self._verified_completion_conditions(
                conn, table, claim_conditions
            )
            if proof_conditions is None:
                return None
            query = select(table.c.complete_through).where(
                *claim_conditions, *proof_conditions
            ).order_by(
                table.c.complete_through.desc(), table.c.finished_at.desc()
            ).limit(1)
            return conn.execute(query).scalar()

    def _source_run(self, run_id):
        if self.collection_runs is None:
            raise SourceSchemaError("futu_comments_collection_runs is required for bounded replay")
        needed = {"run_id", "status", "high_watermark_at"}
        if needed - set(self.collection_runs.c.keys()):
            raise SourceSchemaError("futu_comments_collection_runs has no replay high-watermark")
        if run_id not in self._run_cache:
            with self.engine.connect() as conn:
                row = conn.execute(select(self.collection_runs).where(
                    self.collection_runs.c.run_id == run_id,
                    self.collection_runs.c.status == "succeeded",
                )).mappings().first()
            if row is None:
                raise SourceSchemaError(f"Unknown or unsuccessful source run: {run_id!r}")
            self._run_cache[run_id] = dict(row)
        return self._run_cache[run_id]

    def closing_run(self, hkt_day):
        if self.collection_runs is None:
            return None
        table = self.collection_runs
        needed = {"run_id", "collection_kind", "status", "scheduled_for", "complete_through"}
        if needed - set(table.c.keys()):
            raise SourceSchemaError("futu_comments_collection_runs has an incompatible schema")
        start = datetime.combine(hkt_day, time(19, 0), tzinfo=ZoneInfo("Asia/Hong_Kong"))
        start = start.astimezone(timezone.utc).replace(tzinfo=None)
        end = start + timedelta(hours=1)
        claim_conditions = [
            table.c.collection_kind == "comments_all",
            table.c.status == "succeeded",
            table.c.scheduled_for >= start,
            table.c.scheduled_for < end,
            table.c.complete_through >= hkt_day,
        ]
        with self.engine.connect() as conn:
            proof_conditions = self._verified_completion_conditions(
                conn, table, claim_conditions
            )
            if proof_conditions is None:
                return None
            query = select(table).where(
                *claim_conditions, *proof_conditions
            ).order_by(table.c.scheduled_for.desc(), table.c.run_id.desc()).limit(1)
            row = conn.execute(query).mappings().first()
        return dict(row) if row else None


class MemorySourceAdapter:
    """Deterministic adapter for interface tests and failure recovery tests."""

    source_id = "memory"

    def __init__(self, streams=None, complete_through=None, fail_after_pages=None,
                 closing_runs=None):
        self._streams = {name: list(values) for name, values in (streams or {}).items()}
        self._complete = complete_through
        self._calls = {name: 0 for name in STREAMS}
        self._fail_after = dict(fail_after_pages or {})
        self._closing_runs = dict(closing_runs or {})

    @staticmethod
    def _cursor(item):
        entity_id = item.feed["feed_id"] if isinstance(item, FeedObservation) else item.user["user_id"]
        return Cursor(item.observed_at, entity_id)

    def high_watermark(self, stream, through_source_run_id=None):
        values = self._streams.get(stream, [])
        return self._cursor(values[-1]) if values else None

    def pull_page(self, stream, after, through, limit):
        self._calls[stream] += 1
        if self._fail_after.get(stream) == self._calls[stream]:
            raise RuntimeError(f"simulated {stream} failure")
        values = self._streams.get(stream, [])
        selected = []
        for item in values:
            cursor = self._cursor(item)
            if after and (cursor.observed_at, cursor.entity_id) <= (after.observed_at, after.entity_id):
                continue
            if through and (cursor.observed_at, cursor.entity_id) > (through.observed_at, through.entity_id):
                continue
            selected.append(item)
            if len(selected) >= limit:
                break
        cursor = self._cursor(selected[-1]) if selected else after
        remaining = any(
            (not cursor or (self._cursor(item).observed_at, self._cursor(item).entity_id)
             > (cursor.observed_at, cursor.entity_id))
            and (not through or (self._cursor(item).observed_at, self._cursor(item).entity_id)
                 <= (through.observed_at, through.entity_id))
            for item in values
        )
        return SourcePage(tuple(selected), cursor, not remaining)

    def pull_feeds_by_id(self, feed_ids):
        requested = set(feed_ids)
        latest = {}
        for item in self._streams.get("feeds", []):
            feed_id = int(item.feed["feed_id"])
            if feed_id not in requested:
                continue
            prior = latest.get(feed_id)
            if prior is None or item.observed_at > prior.observed_at:
                latest[feed_id] = item
        items = tuple(latest[feed_id] for feed_id in feed_ids if feed_id in latest)
        return SourcePage(items, None, True, ignored=len(requested - set(latest)))

    def complete_through(self, through_source_run_id=None):
        return self._complete

    def closing_run(self, hkt_day):
        return self._closing_runs.get(hkt_day)
