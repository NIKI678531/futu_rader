"""Publish the first complete data anchor before production services become ready.

Schema migration deliberately does not invent data. A new production database
therefore needs one real MarketInsight sync, bounded by a successful
``comments_all`` receipt, before range APIs can answer honestly. Kubernetes
runs this module after Alembic and retries the init container until the source
can publish that anchor.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from zoneinfo import ZoneInfo

from sqlalchemy import select

import clock
from radar_db import make_engine
from radar_db.schema import meta_kv


HKT = ZoneInfo("Asia/Hong_Kong")


def _anchor(engine) -> str | None:
    with engine.connect() as conn:
        value = conn.execute(
            select(meta_kv.c.v).where(meta_kv.c.k == "anchor")
        ).scalar_one_or_none()
    text = str(value).strip() if value is not None else ""
    return text or None


def ensure_anchor(engine, *, today: date | None = None, sync=None) -> dict:
    """Run the normal source sync once when the target has no complete anchor."""

    existing = _anchor(engine)
    if existing:
        return {"status": "ready", "anchor": existing, "sync": "skipped"}

    if sync is None:
        from jobs.daily import sync_step

        sync = sync_step
    sync_result = sync(today or clock.now(HKT).date())
    published = _anchor(engine)
    if published:
        return {"status": "ready", "anchor": published, "sync": sync_result}
    return {
        "status": "blocked",
        "anchor": None,
        "sync": sync_result,
        "reason": "anchor_missing_after_sync",
    }


def _safe_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    text = re.sub(
        r"([a-z][a-z0-9+.-]*://[^:/@\s]+:)[^@\s]+@",
        r"\1<REDACTED>@",
        text,
        flags=re.IGNORECASE,
    )
    return text[:500]


def main() -> int:
    engine = None
    try:
        engine = make_engine()
        result = ensure_anchor(engine)
    except Exception as exc:  # noqa: BLE001 - init-container boundary
        result = {
            "status": "failed",
            "reason": "bootstrap_sync_failed",
            "errorType": type(exc).__name__,
            "error": _safe_error(exc),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()

    print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
