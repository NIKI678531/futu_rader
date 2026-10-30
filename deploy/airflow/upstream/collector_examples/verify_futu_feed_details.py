"""Fail a feed-detail run when a monitored account still has preview-only rows.

This verifier is deliberately read-only.  The collector is responsible for
fetching detail pages; this process only proves that every recent post selected
by the canonical KOL/official roster has a successful detail timestamp and a
body before Airflow records a successful receipt.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import MetaData, Table, create_engine, func, or_, select
from sqlalchemy.engine import make_url


def load_scope(path: str | Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    active = payload.get("activeRoster")
    raw_ids = payload.get("userIds")
    replacements = payload.get("replacements", [])
    if not isinstance(active, list) or not isinstance(raw_ids, dict):
        raise ValueError("KOL roster must contain activeRoster and userIds")

    aliases = {}
    for row in replacements:
        name = str(row.get("name") or "").strip()
        values = tuple(
            str(value).strip() for value in row.get("aliases", []) if str(value).strip()
        )
        if not name or not values:
            raise ValueError("KOL roster contains an invalid replacement")
        aliases[name] = values

    author_ids = []
    source_names = []
    for raw_name in active:
        name = str(raw_name).strip()
        if not name:
            raise ValueError("KOL roster contains a blank active name")
        for source_name in (name, *aliases.get(name, ())):
            if source_name not in source_names:
                source_names.append(source_name)
        for raw_id in raw_ids.get(name, []):
            author_id = str(raw_id).strip()
            if not author_id.isdigit():
                raise ValueError(f"Invalid Futu user ID for active KOL {name!r}")
            if author_id not in author_ids:
                author_ids.append(author_id)
    if not author_ids and not source_names:
        raise ValueError("KOL roster contains no active source identities")
    return tuple(author_ids), tuple(source_names)


def load_official_names(path: str | Path) -> tuple[str, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("officials")
    if not isinstance(rows, list):
        raise ValueError("Official roster must contain officials")
    names = []
    shorts = set()
    for row in rows:
        short = str(row.get("short") or "").strip()
        raw_names = row.get("names")
        if not short or short in shorts or not isinstance(raw_names, list):
            raise ValueError("Official roster contains an invalid account")
        shorts.add(short)
        values = [str(value).strip() for value in raw_names if str(value).strip()]
        if not values:
            raise ValueError(f"Official roster contains no source names for {short!r}")
        for name in values:
            if name not in names:
                names.append(name)
    if not names:
        raise ValueError("Official roster contains no source identities")
    return tuple(names)


def resolve_official_user_ids(engine, roster: str | Path) -> tuple[str, ...]:
    """Resolve stable IDs from already collected official posts without guessing."""
    names = load_official_names(roster)
    metadata = MetaData()
    feeds = Table("futu_comments_feeds", metadata, autoload_with=engine)
    with engine.connect() as connection:
        values = connection.execute(
            select(feeds.c.author_uid)
            .where(
                feeds.c.author_name.in_(names),
                feeds.c.author_uid.is_not(None),
                func.length(func.trim(feeds.c.author_uid)) > 0,
            )
            .distinct()
        ).scalars()
        ids = tuple(dict.fromkeys(str(value).strip() for value in values))
    invalid = [value for value in ids if not value.isdigit()]
    if invalid:
        raise ValueError("Official source contains invalid Futu user IDs")
    return ids


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def audit(
    engine,
    roster: str | Path,
    *,
    recent_days: int,
    before: datetime | None = None,
    official_roster: str | Path | None = None,
):
    if recent_days < 1 or recent_days > 90:
        raise ValueError("recent_days must be between 1 and 90")
    author_ids, source_names = load_scope(roster)
    if official_roster is not None:
        official_names = load_official_names(official_roster)
        official_ids = resolve_official_user_ids(engine, official_roster)
        author_ids = tuple(dict.fromkeys((*author_ids, *official_ids)))
        source_names = tuple(dict.fromkeys((*source_names, *official_names)))
    before = _utc_naive(before or datetime.now(timezone.utc))
    after = before - timedelta(days=recent_days)

    metadata = MetaData()
    feeds = Table("futu_comments_feeds", metadata, autoload_with=engine)
    identity = or_(
        feeds.c.author_uid.in_(author_ids),
        feeds.c.author_name.in_(source_names),
    )
    window = (
        identity,
        feeds.c.posted_at >= after,
        feeds.c.posted_at < before,
    )
    incomplete = or_(
        feeds.c.detail_updated_at.is_(None),
        feeds.c.content_text.is_(None),
        func.length(func.trim(feeds.c.content_text)) == 0,
    )
    with engine.connect() as connection:
        total = connection.execute(
            select(func.count()).select_from(feeds).where(*window)
        ).scalar_one()
        missing = connection.execute(
            select(func.count()).select_from(feeds).where(*window, incomplete)
        ).scalar_one()
        sample = [
            {
                "feedId": row.feed_id,
                "authorUid": row.author_uid,
                "authorName": row.author_name,
                "postedAt": row.posted_at.isoformat(sep=" ") if row.posted_at else None,
                "detailUpdatedAt": (
                    row.detail_updated_at.isoformat(sep=" ")
                    if row.detail_updated_at else None
                ),
            }
            for row in connection.execute(
                select(
                    feeds.c.feed_id,
                    feeds.c.author_uid,
                    feeds.c.author_name,
                    feeds.c.posted_at,
                    feeds.c.detail_updated_at,
                )
                .where(*window, incomplete)
                .order_by(feeds.c.posted_at.desc(), feeds.c.feed_id.desc())
                .limit(50)
            )
        ]
    return {
        "status": "ok" if missing == 0 else "incomplete",
        "from": after.isoformat(sep=" "),
        "to": before.isoformat(sep=" "),
        "activeAuthorIds": len(author_ids),
        "activeSourceNames": len(source_names),
        "posts": total,
        "complete": total - missing,
        "incomplete": missing,
        "incompletePosts": sample,
    }


def source_engine(url: str):
    parsed = make_url(url)
    if parsed.drivername in {"mysql", "mysql+mysqldb"}:
        parsed = parsed.set(drivername="mysql+pymysql")
    return create_engine(parsed, future=True, pool_pre_ping=True, hide_parameters=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify complete monitored-account feed bodies")
    parser.add_argument("--db", required=True)
    parser.add_argument("--roster")
    parser.add_argument("--official-roster")
    parser.add_argument("--print-official-author-ids", action="store_true")
    parser.add_argument("--recent-days", type=int, default=30)
    args = parser.parse_args(argv)
    engine = source_engine(args.db)
    try:
        if args.print_official_author_ids:
            if not args.official_roster:
                parser.error("--print-official-author-ids requires --official-roster")
            print(" ".join(resolve_official_user_ids(engine, args.official_roster)))
            return 0
        if not args.roster:
            parser.error("audit requires --roster")
        result = audit(
            engine,
            args.roster,
            recent_days=args.recent_days,
            official_roster=args.official_roster,
        )
    finally:
        engine.dispose()
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
