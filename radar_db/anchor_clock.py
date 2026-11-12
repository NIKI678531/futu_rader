"""System-clock anchor mode shared by the API and worker.

Production can opt into ``RADAR_ANCHOR_MODE=system`` when calendar ranges are
expected to follow the current Hong Kong date instead of source-completion
receipts.  Demo and test environments keep the data-backed behavior by
default so frozen fixtures remain reproducible.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time

from sqlalchemy import insert, select, update

from . import clock
from .schema import meta_kv
from .time_windows import HKT


def system_anchor_enabled() -> bool:
    return os.getenv("RADAR_ANCHOR_MODE", "data").strip().lower() == "system"


def current_hkt_date(at: datetime | None = None) -> date:
    moment = at or clock.now(HKT)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=HKT)
    else:
        moment = moment.astimezone(HKT)
    return moment.date()


def publish_system_anchor(engine, *, at: datetime | None = None) -> date | None:
    """Persist today's HKT date when system-anchor mode is enabled.

    Writes occur only when the date changes, so the API's metadata cache is not
    invalidated by every worker heartbeat.
    """

    if not system_anchor_enabled():
        return None
    day = current_hkt_date(at)
    values = {
        "anchor": day.isoformat(),
        "anchor_ts": datetime.combine(day, time.max).replace(microsecond=0).isoformat(" "),
    }
    with engine.begin() as conn:
        existing = dict(
            conn.execute(
                select(meta_kv.c.k, meta_kv.c.v).where(meta_kv.c.k.in_(values))
            ).all()
        )
        for key, value in values.items():
            if existing.get(key) == value:
                continue
            changed = conn.execute(
                update(meta_kv).where(meta_kv.c.k == key).values(v=value)
            ).rowcount
            if not changed:
                conn.execute(insert(meta_kv).values(k=key, v=value))
    return day
