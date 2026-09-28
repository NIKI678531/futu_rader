"""0010 -- preserve microseconds in every MySQL timestamp column.

SQLite already preserves the Python ``datetime`` value used by local runs.  A
bare MySQL ``DATETIME`` does not: it rounds/truncates to whole seconds.  That is
unsafe for the collector's ``(timestamp, stable_id)`` cursors and for
append-only observations, so production uses ``DATETIME(6)`` consistently.

The migration is deliberately a no-op on SQLite.  Rebuilding every large table
through Alembic batch mode would add risk without changing its storage format.
"""

from alembic import op
from sqlalchemy.dialects import mysql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


# Keep this explicit: it doubles as the reviewable inventory of temporal
# columns present at revision 0009.  The metadata test catches future DateTime
# columns that are not represented here.
DATETIME_COLUMNS = (
    ("src_feeds", "posted_at", False),
    ("src_feeds", "scraped_at", False),
    ("src_users", "scraped_at", False),
    ("feeds", "posted_at", False),
    ("feeds", "source_observed_at", True),
    ("comments", "posted_at", True),
    ("ingestion_runs", "started_at", False),
    ("ingestion_runs", "finished_at", True),
    ("collector_checkpoints", "cursor_at", True),
    ("collector_checkpoints", "updated_at", False),
    ("ai_daily_budget", "updated_at", False),
    ("feed_counter_observations", "observed_at", False),
    ("annotation_runs", "started_at", False),
    ("annotation_runs", "finished_at", True),
    ("annotation_jobs", "claimed_at", True),
    ("annotation_jobs", "lease_until", True),
    ("annotation_jobs", "created_at", False),
    ("annotation_jobs", "updated_at", False),
    ("worker_events", "ts", False),
    ("runtime_leases", "expires_at", False),
    ("source_snapshots", "observed_at", False),
    ("analysis_scopes", "date_from", False),
    ("analysis_scopes", "date_to", False),
    ("analysis_scopes", "created_at", False),
    ("synthesis_outputs", "created_at", False),
    ("annotations", "created_at", False),
    ("review_decisions", "reviewed_at", False),
    ("price_instruments", "verified_at", False),
    ("price_bars", "timestamp", False),
    ("price_bars", "fetched_at", False),
    ("price_syncs", "updated_at", False),
)


def _is_mysql() -> bool:
    return op.get_context().dialect.name == "mysql"


def _alter_all(*, new_type, old_type) -> None:
    for table_name, column_name, nullable in DATETIME_COLUMNS:
        op.alter_column(
            table_name,
            column_name,
            existing_type=old_type,
            type_=new_type,
            existing_nullable=nullable,
        )


def upgrade():
    if not _is_mysql():
        return
    _alter_all(
        new_type=mysql.DATETIME(fsp=6),
        old_type=mysql.DATETIME(),
    )


def downgrade():
    if not _is_mysql():
        return
    _alter_all(
        new_type=mysql.DATETIME(),
        old_type=mysql.DATETIME(fsp=6),
    )
