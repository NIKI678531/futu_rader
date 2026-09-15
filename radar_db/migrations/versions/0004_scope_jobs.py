import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    links = op.create_table(
        "analysis_scope_jobs",
        sa.Column("scope_id", sa.String(40), primary_key=True),
        sa.Column("job_id", sa.BigInteger(), primary_key=True),
    )
    jobs = sa.table("annotation_jobs", sa.column("scope_id"), sa.column("job_id"))
    op.get_bind().execute(links.insert().from_select(
        ["scope_id", "job_id"], sa.select(jobs.c.scope_id, jobs.c.job_id).where(jobs.c.scope_id.isnot(None)),
    ))
    op.create_table(
        "runtime_leases",
        sa.Column("name", sa.String(80), primary_key=True),
        sa.Column("owner", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("runtime_leases")
    op.drop_table("analysis_scope_jobs")