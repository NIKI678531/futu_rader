"""0014 -- durable database queue for manual AI analysis requests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

DATETIME = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
DATETIME_COLUMNS = (
    ("ai_analysis_requests", "requested_at", False),
    ("ai_analysis_requests", "started_at", True),
    ("ai_analysis_requests", "finished_at", True),
    ("ai_analysis_requests", "lease_until", True),
)


def upgrade():
    op.create_table(
        "ai_analysis_requests",
        sa.Column("request_id", sa.String(40), primary_key=True),
        sa.Column("range_key", sa.String(10), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("active_key", sa.String(10)),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("requested_at", DATETIME, nullable=False),
        sa.Column("started_at", DATETIME),
        sa.Column("finished_at", DATETIME),
        sa.Column("lease_owner", sa.String(40)),
        sa.Column("lease_until", DATETIME),
        sa.Column("result_json", sa.Text()),
        sa.Column("error_summary", sa.Text()),
        sa.UniqueConstraint(
            "active_key", name="uq_ai_analysis_requests_active_range"
        ),
        sa.CheckConstraint(
            "range_key IN ('d1','d2','d7','d14','d30')",
            name="ck_ai_analysis_requests_range",
        ),
        sa.CheckConstraint(
            "status IN ('queued','running','succeeded','partial','failed')",
            name="ck_ai_analysis_requests_status",
        ),
    )
    op.create_index(
        "ix_ai_analysis_requests_queue",
        "ai_analysis_requests",
        ["status", "priority", "requested_at"],
    )


def downgrade():
    op.drop_index(
        "ix_ai_analysis_requests_queue", table_name="ai_analysis_requests"
    )
    op.drop_table("ai_analysis_requests")
