"""抽取范围表、产品×区间生成物表，以及 `annotation_jobs.scope_id`。

Revision ID: 0003
Revises: 0002

## 为什么要有 scope

`annotation_runs` 记的是一次执行；「3033 与 7226、6 月 1 日到 8 月 25 日、评论任务」是一个
**业务窗口**，可能分几次执行、可能中断续跑。覆盖率（候选多少、剔了多少、标了多少）要挂在
窗口上。`annotation_jobs.scope_id` 让 `run(scope_id=…)` 只领本窗口的任务 —— 没有它，
跑 3033 近 7 天会把队列里别的产品、别的日期一起领走。

## 为什么生成物不进 annotations

判定单元不同：`annotations` 是「一条评论 × 一只产品」，生成物是「一只产品 × 一个区间」。
硬塞进去要把 `target_id`（BigInteger）挪用成产品代码、把 `subject_code` 挪用成区间，
两列都失去原义。`input_fingerprint` 覆盖参与生成的标注 id 集合与事实 JSON —— 底层一变
指纹就变，旧生成物自动失效。

## SQLite 加列

`op.add_column` 在 SQLite 上是原生支持的（不需要 batch）；索引单独建。
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

AUTO_PK = sa.BigInteger().with_variant(sa.Integer, "sqlite")
NO_SUBJECT = ""


def upgrade():
    op.add_column("annotation_jobs", sa.Column("scope_id", sa.String(40)))
    op.create_index("ix_annotation_jobs_scope_id", "annotation_jobs", ["scope_id"])

    op.create_table(
        "analysis_scopes",
        sa.Column("scope_id", sa.String(40), primary_key=True),
        sa.Column("task", sa.String(40), nullable=False),
        sa.Column("codes_json", sa.Text, nullable=False),
        sa.Column("date_from", sa.DateTime, nullable=False),
        sa.Column("date_to", sa.DateTime, nullable=False),
        sa.Column("time_basis", sa.String(20), nullable=False),
        sa.Column("with_baseline", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("taxonomy_version", sa.String(40), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("stats_json", sa.Text),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )

    op.create_table(
        "synthesis_outputs",
        sa.Column("synthesis_id", AUTO_PK, primary_key=True, autoincrement=True),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("range_key", sa.String(10), nullable=False),
        sa.Column("anchor", sa.String(10), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("subkey", sa.String(40), nullable=False, server_default=NO_SUBJECT),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("value_json", sa.Text, nullable=False),
        sa.Column("evidence_ids_json", sa.Text),
        sa.Column("run_id", sa.String(40), nullable=False),
        sa.Column("review_state", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("supersedes_id", sa.BigInteger),
        sa.UniqueConstraint("code", "range_key", "anchor", "kind", "subkey",
                            "input_fingerprint", name="uq_synthesis_unit"),
    )
    op.create_index("ix_synthesis_lookup", "synthesis_outputs",
                    ["code", "range_key", "anchor", "kind"])


def downgrade():
    op.drop_index("ix_synthesis_lookup", table_name="synthesis_outputs")
    op.drop_table("synthesis_outputs")
    op.drop_table("analysis_scopes")
    op.drop_index("ix_annotation_jobs_scope_id", table_name="annotation_jobs")
    with op.batch_alter_table("annotation_jobs") as batch:
        batch.drop_column("scope_id")
