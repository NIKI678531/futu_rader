"""0011 -- parent-feed source ticker and strict cashtag mention facts."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("feeds") as batch:
        batch.add_column(sa.Column("source_ticker", sa.String(32)))

    op.create_table(
        "feed_mentions",
        sa.Column("feed_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("raw_ticker", sa.String(32), primary_key=True),
        sa.Column("market", sa.String(8)),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "occurrences > 0",
            name="ck_feed_mentions_occurrences_positive",
        ),
    )
    op.create_index(
        "ix_feed_mentions_ticker_feed",
        "feed_mentions",
        ["raw_ticker", "feed_id"],
    )


def downgrade():
    op.drop_index("ix_feed_mentions_ticker_feed", table_name="feed_mentions")
    op.drop_table("feed_mentions")
    with op.batch_alter_table("feeds") as batch:
        batch.drop_column("source_ticker")
