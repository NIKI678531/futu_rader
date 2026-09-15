"""0008 —— 漏斗分段列 `annotation_jobs.stage` 与事件流表 `worker_events`（ADR-0021）。

`stage` 的回填规则：帖子与 KOL 评论任务没有学生模型，一律 `llm`；评论任务
（`comment_product`）保持 server_default 的 `student`。已经 `done` 的评论任务也标成
`student` 没有关系 —— stage 只影响**还会被领取**的任务，done 的不再被任何人领。

SQLite 加 NOT NULL 列必须带 server_default，Alembic 的 batch 模式会走「建新表拷数据」，
5 GB 库上这一步以秒计（annotation_jobs 只有几十万行）。
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("annotation_jobs") as batch:
        batch.add_column(sa.Column("stage", sa.String(10), nullable=False, server_default="student"))
        batch.create_index("ix_jobs_task_stage_status", ["task", "stage", "status"])
    jobs = sa.table("annotation_jobs", sa.column("task"), sa.column("stage"))
    op.get_bind().execute(
        jobs.update().where(jobs.c.task != "comment_product").values(stage="llm")
    )

    op.create_table(
        "worker_events",
        sa.Column("event_id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("level", sa.String(10), nullable=False),
        sa.Column("stage", sa.String(12), nullable=False),
        sa.Column("code", sa.String(10)),
        sa.Column("scope_id", sa.String(40)),
        sa.Column("run_id", sa.String(40)),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data_json", sa.Text()),
    )
    op.create_index("ix_worker_events_ts", "worker_events", ["ts"])


def downgrade():
    op.drop_index("ix_worker_events_ts", table_name="worker_events")
    op.drop_table("worker_events")
    with op.batch_alter_table("annotation_jobs") as batch:
        batch.drop_index("ix_jobs_task_stage_status")
        batch.drop_column("stage")
