"""0012 -- strict cashtag comment-to-product routes for AI inputs."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


# New temporal columns created after migration 0010 must declare MySQL's
# fractional precision directly instead of being added to 0010's historical
# ALTER inventory (the table does not exist yet at that point).
DATETIME_COLUMNS = (("comment_product_routes", "updated_at", False),)
DATETIME = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")


def upgrade():
    op.create_table(
        "comment_product_routes",
        sa.Column("comment_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("subject_code", sa.String(10), primary_key=True),
        sa.Column("feed_id", sa.BigInteger(), nullable=False),
        sa.Column("matched_parent", sa.Boolean(), nullable=False),
        sa.Column("matched_comment", sa.Boolean(), nullable=False),
        sa.Column("rule_version", sa.String(40), nullable=False),
        sa.Column("updated_at", DATETIME, nullable=False),
        sa.CheckConstraint(
            "matched_parent = 1 OR matched_comment = 1",
            name="ck_comment_product_routes_has_match",
        ),
    )
    op.create_index(
        "ix_comment_product_routes_subject_feed",
        "comment_product_routes",
        ["subject_code", "feed_id"],
    )
    op.create_index(
        "ix_comment_product_routes_feed",
        "comment_product_routes",
        ["feed_id"],
    )


def downgrade():
    op.drop_index("ix_comment_product_routes_feed", table_name="comment_product_routes")
    op.drop_index(
        "ix_comment_product_routes_subject_feed",
        table_name="comment_product_routes",
    )
    op.drop_table("comment_product_routes")
