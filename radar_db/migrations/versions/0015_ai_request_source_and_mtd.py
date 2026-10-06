"""0015 -- AI request source (manual vs scheduled) and the month-to-date range.

The 08:00 daily job and the admin page share one queue (ADR-0031).  Each row
records who asked for it, and ``mtd`` joins the five fixed ranges so a daily
run can publish every page range.
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

RANGES_V1 = "range_key IN ('d1','d2','d7','d14','d30')"
RANGES_V2 = "range_key IN ('d1','d2','d7','d14','d30','mtd')"


def upgrade():
    with op.batch_alter_table("ai_analysis_requests") as batch:
        batch.add_column(
            sa.Column(
                "requested_by",
                sa.String(20),
                nullable=False,
                server_default="admin",
            )
        )
        batch.drop_constraint("ck_ai_analysis_requests_range", type_="check")
        batch.create_check_constraint("ck_ai_analysis_requests_range", RANGES_V2)
        batch.create_check_constraint(
            "ck_ai_analysis_requests_requested_by",
            "requested_by IN ('admin','schedule')",
        )


def downgrade():
    op.execute("DELETE FROM ai_analysis_requests WHERE range_key = 'mtd'")
    with op.batch_alter_table("ai_analysis_requests") as batch:
        batch.drop_constraint("ck_ai_analysis_requests_requested_by", type_="check")
        batch.drop_constraint("ck_ai_analysis_requests_range", type_="check")
        batch.create_check_constraint("ck_ai_analysis_requests_range", RANGES_V1)
        batch.drop_column("requested_by")
