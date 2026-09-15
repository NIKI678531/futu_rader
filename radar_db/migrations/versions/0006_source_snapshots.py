import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "source_snapshots",
        sa.Column("feed_id", sa.BigInteger(), primary_key=True),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.Text().with_variant(mysql.LONGTEXT(), "mysql"), nullable=False),
        sa.Column("observed_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    op.drop_table("source_snapshots")