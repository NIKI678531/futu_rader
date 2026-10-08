"""0016 -- allow the partial current-day (d0) analysis range.

The admin UI and worker share ``ai_analysis_requests``.  Extending only the
application enum would still make production databases reject d0 at INSERT,
so the durable queue constraint is migrated in the same change.
"""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

RANGES_V2 = "range_key IN ('d1','d2','d7','d14','d30','mtd')"
RANGES_V3 = "range_key IN ('d0','d1','d2','d7','d14','d30','mtd')"


def upgrade():
    with op.batch_alter_table("ai_analysis_requests") as batch:
        batch.drop_constraint("ck_ai_analysis_requests_range", type_="check")
        batch.create_check_constraint("ck_ai_analysis_requests_range", RANGES_V3)


def downgrade():
    op.execute("DELETE FROM ai_analysis_requests WHERE range_key = 'd0'")
    with op.batch_alter_table("ai_analysis_requests") as batch:
        batch.drop_constraint("ck_ai_analysis_requests_range", type_="check")
        batch.create_check_constraint("ck_ai_analysis_requests_range", RANGES_V2)
