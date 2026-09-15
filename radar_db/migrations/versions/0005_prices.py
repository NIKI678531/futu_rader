import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "price_instruments",
        sa.Column("code", sa.String(10), primary_key=True),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column("exchange", sa.String(20), nullable=False),
        sa.Column("name", sa.String(255)),
        sa.Column("timezone", sa.String(40), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "price_bars",
        sa.Column("code", sa.String(10), primary_key=True),
        sa.Column("provider", sa.String(20), primary_key=True),
        sa.Column("interval", sa.String(10), primary_key=True),
        sa.Column("timestamp", sa.DateTime(), primary_key=True),
        sa.Column("adjustment", sa.String(30), primary_key=True),
        sa.Column("session_date", sa.String(10), nullable=False),
        *(sa.Column(field, sa.Numeric(20, 8), nullable=False) for field in ("open", "high", "low", "close")),
        sa.Column("volume", sa.BigInteger()),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "price_syncs",
        sa.Column("code", sa.String(10), primary_key=True),
        sa.Column("interval", sa.String(10), primary_key=True),
        sa.Column("date_from", sa.String(10), primary_key=True),
        sa.Column("date_to", sa.String(10), primary_key=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reason", sa.String(80)),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )


def downgrade():
    for table in ("price_syncs", "price_bars", "price_instruments"):
        op.drop_table(table)