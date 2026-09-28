"""0009 —— 在线采集控制面、每日 AI 预算与帖子计数观察。

历史 feeds 没有在线观察时间，也无法可靠判断评论分页是否完整。因此迁移只把覆盖状态
回填为 ``unknown``，绝不能由 ``comments_parsed`` 猜成 complete。SQLite 与 MySQL 8
共用迁移；CHECK 约束只使用两边都支持的基础 SQL 表达式。
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("feeds") as batch:
        batch.add_column(sa.Column("source_observed_at", sa.DateTime()))
        batch.add_column(
            sa.Column(
                "comment_coverage_status",
                sa.String(24),
                nullable=False,
                server_default="unknown",
            )
        )
        batch.create_index("ix_feeds_comment_coverage", ["comment_coverage_status"])
        batch.create_check_constraint(
            "ck_feeds_comment_coverage_status",
            "comment_coverage_status IN "
            "('complete','partial','retryable_incomplete','unknown')",
        )

    op.create_table(
        "ingestion_runs",
        sa.Column("run_id", sa.String(64), primary_key=True),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("source_run_id", sa.String(250)),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime()),
        sa.Column("high_watermark_json", sa.Text()),
        sa.Column("complete_through", sa.Date()),
        sa.Column("cursor_before_json", sa.Text()),
        sa.Column("cursor_after_json", sa.Text()),
        sa.Column("counts_json", sa.Text()),
        sa.Column("error_summary", sa.Text()),
        sa.Column("data_revision", sa.String(64)),
        sa.UniqueConstraint("source", "source_run_id", name="uq_ingestion_runs_source_run"),
        sa.CheckConstraint(
            "status IN ('running','succeeded','failed','dry_run')",
            name="ck_ingestion_runs_status",
        ),
    )
    op.create_index(
        "ix_ingestion_runs_status_finished",
        "ingestion_runs",
        ["status", "finished_at"],
    )
    op.create_index(
        "ix_ingestion_runs_complete_through",
        "ingestion_runs",
        ["complete_through"],
    )

    op.create_table(
        "collector_checkpoints",
        sa.Column("source", sa.String(40), primary_key=True),
        sa.Column("stream", sa.String(40), primary_key=True),
        sa.Column("partition_key", sa.String(80), primary_key=True),
        sa.Column("cursor_at", sa.DateTime()),
        sa.Column("cursor_id", sa.String(100)),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_run_id", sa.String(64)),
        sa.Column("config_hash", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "(cursor_at IS NULL AND cursor_id IS NULL) OR "
            "(cursor_at IS NOT NULL AND cursor_id IS NOT NULL)",
            name="ck_collector_checkpoints_cursor_pair",
        ),
    )
    op.create_index(
        "ix_collector_checkpoints_updated_at",
        "collector_checkpoints",
        ["updated_at"],
    )

    op.create_table(
        "ai_daily_budget",
        sa.Column("budget_date", sa.Date(), primary_key=True),
        sa.Column("policy_hash", sa.String(64), nullable=False),
        sa.Column("request_limit", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("requests_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("request_limit >= 0", name="ck_ai_daily_budget_limit"),
        sa.CheckConstraint(
            "requests_used >= 0 AND requests_used <= request_limit",
            name="ck_ai_daily_budget_used",
        ),
        sa.CheckConstraint(
            "status IN ('open','exhausted','closed')",
            name="ck_ai_daily_budget_status",
        ),
    )
    op.create_index(
        "ix_ai_daily_budget_status_date",
        "ai_daily_budget",
        ["status", "budget_date"],
    )

    op.create_table(
        "feed_counter_observations",
        sa.Column("feed_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("observed_at", sa.DateTime(), primary_key=True),
        sa.Column("like_count", sa.Integer()),
        sa.Column("comment_count", sa.Integer()),
        sa.Column("image_count", sa.Integer()),
        sa.Column("share_count", sa.Integer()),
        sa.Column("browse_count", sa.Integer()),
        sa.Column("is_settled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source_run_id", sa.String(250)),
        sa.CheckConstraint(
            "(like_count IS NULL OR like_count >= 0) AND "
            "(comment_count IS NULL OR comment_count >= 0) AND "
            "(image_count IS NULL OR image_count >= 0) AND "
            "(share_count IS NULL OR share_count >= 0) AND "
            "(browse_count IS NULL OR browse_count >= 0)",
            name="ck_feed_counter_observations_nonnegative",
        ),
    )
    op.create_index(
        "ix_feed_counter_observations_settled",
        "feed_counter_observations",
        ["feed_id", "is_settled", "observed_at"],
    )


def downgrade():
    op.drop_index(
        "ix_feed_counter_observations_settled",
        table_name="feed_counter_observations",
    )
    op.drop_table("feed_counter_observations")
    op.drop_index("ix_ai_daily_budget_status_date", table_name="ai_daily_budget")
    op.drop_table("ai_daily_budget")
    op.drop_index(
        "ix_collector_checkpoints_updated_at",
        table_name="collector_checkpoints",
    )
    op.drop_table("collector_checkpoints")
    op.drop_index("ix_ingestion_runs_complete_through", table_name="ingestion_runs")
    op.drop_index("ix_ingestion_runs_status_finished", table_name="ingestion_runs")
    op.drop_table("ingestion_runs")
    with op.batch_alter_table("feeds") as batch:
        batch.drop_constraint("ck_feeds_comment_coverage_status", type_="check")
        batch.drop_index("ix_feeds_comment_coverage")
        batch.drop_column("comment_coverage_status")
        batch.drop_column("source_observed_at")
