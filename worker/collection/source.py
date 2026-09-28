"""Source adapters for the internal collection seam."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import MetaData, Table, and_, func, or_, select
from sqlalchemy.exc import NoSuchTableError

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


class SourceAdapter(Protocol):
    source_id: str

    def high_watermark(
        self, stream: str, through_source_run_id: str | None = None
    ) -> Cursor | None: ...

    def pull_page(
        self, stream: str, after: Cursor | None, through: Cursor | None, limit: int
    ) -> SourcePage: ...

    def complete_through(self, through_source_run_id: str | None = None) -> date | None: ...

    def closing_run(self, hkt_day: date) -> dict | None: ...


def _code_from_ticker(ticker: str) -> str:
    value = ticker.strip()
    if value.upper().startswith("HK."):
        value = value.split(".", 1)[1]
    elif value.upper().endswith(".HK"):
        value = value.rsplit(".", 1)[0]
    head = value.split(".", 1)[0]
    return head.lstrip("0") or "0"


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
        self.pool_codes = set(pool_codes)
        self.pool_stock_ids = tuple(
            stock_id for stock_id, code in self.stock_codes.items() if code in self.pool_codes
        )
        missing_codes = self.pool_codes - set(self.stock_codes.values())
        if missing_codes:
            raise SourceSchemaError(f"Product pool codes missing from source: {sorted(missing_codes)}")

    def _stream(self, stream):
        if stream == "feeds":
            return self.feeds, self.feeds.c.scraped_at, self.feeds.c.feed_id
        if stream == "feed_details":
            return self.feeds, self.feeds.c.detail_updated_at, self.feeds.c.feed_id
        if stream == "users":
            return self.users, self.users.c.scraped_at, self.users.c.user_id
        raise ValueError(f"Unknown source stream: {stream}")

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
                observation = normalize_feed(row, code, observed_at)
                if stream == "feed_details":
                    # Detail runs are allowed to improve text only. Counter/comment snapshots belong to feeds.
                    observation = replace(observation, comments=(), mentions=())
                items.append(observation)
        cursor = Cursor(rows[-1][timestamp_col.name], rows[-1][id_col.name]) if rows else after
        return SourcePage(tuple(items), cursor, len(rows) < limit, ignored=ignored)

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
            fingerprint.isnot(None),
            func.length(func.trim(fingerprint)) > 0,
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

    def __init__(self, streams=None, complete_through=None, fail_after_pages=None, closing_runs=None):
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

    def complete_through(self, through_source_run_id=None):
        return self._complete

    def closing_run(self, hkt_day):
        return self._closing_runs.get(hkt_day)
