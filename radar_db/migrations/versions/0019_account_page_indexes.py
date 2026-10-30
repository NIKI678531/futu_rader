"""0019 -- indexes for year-long account reads and comment clocks."""

from alembic import op


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_feeds_author_uid_posted",
        "feeds",
        ["author_uid", "posted_at", "feed_id"],
    )
    op.create_index(
        "ix_feeds_author_name_posted",
        "feeds",
        ["author_name", "posted_at", "feed_id"],
    )
    op.create_index(
        "ix_comments_feed_posted",
        "comments",
        ["feed_id", "posted_at"],
    )


def downgrade():
    op.drop_index("ix_comments_feed_posted", table_name="comments")
    op.drop_index("ix_feeds_author_name_posted", table_name="feeds")
    op.drop_index("ix_feeds_author_uid_posted", table_name="feeds")
