"""0020 -- restore the source-backed complete-day anchor."""

from datetime import date, datetime, time

import sqlalchemy as sa
from alembic import op


revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def _day(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _put(bind, table, key, value):
    changed = bind.execute(
        table.update().where(table.c.k == key).values(v=value)
    ).rowcount
    if not changed:
        bind.execute(table.insert().values(k=key, v=value))


def upgrade():
    bind = op.get_bind()
    meta_kv = sa.table(
        "meta_kv",
        sa.column("k", sa.String(100)),
        sa.column("v", sa.Text()),
    )
    values = dict(bind.execute(sa.select(meta_kv.c.k, meta_kv.c.v)).all())
    trusted = _day(values.get("source_complete_through")) or _day(
        values.get("source_observed_anchor")
    )
    if trusted is None:
        return

    anchor_changed = _day(values.get("anchor")) != trusted
    _put(bind, meta_kv, "anchor", trusted.isoformat())
    _put(
        bind,
        meta_kv,
        "anchor_ts",
        datetime.combine(trusted, time.max)
        .replace(microsecond=0)
        .isoformat(" "),
    )
    if anchor_changed:
        _put(bind, meta_kv, "anchor_repair_required", "1")


def downgrade():
    # This migration repairs metadata written by the removed system-clock mode.
    # The incorrect value is intentionally not restored on downgrade.
    pass
