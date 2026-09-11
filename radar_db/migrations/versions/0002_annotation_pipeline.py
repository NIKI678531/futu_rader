"""AI 标注管线的五张表，取代 ADR-0010 的单表形态。

Revision ID: 0002
Revises: 0001

## 为什么是 drop 而不是 alter

旧 `annotations` 表有 0 行（本机 5.27 GB 的库里实测），且它缺的不是一两列 ——
判定单元、四个版本、证据、复核状态全都没有，主键类型在 SQLite 下还是坏的（§10.3）。
把它 alter 成新形状要走七八步 batch 操作，而搬运的是零行数据。

`downgrade()` 会重建旧表，但**旧数据回不来**（新表的行在旧结构里无处安放：
subject_code、run_id、evidence 都没有对应的列）。这是有意的：这不是一次可逆的
数据迁移，是一次结构替换。回滚的意义仅限于「让 schema 回到旧形状」。

## SQLite 主键（runbook §10.3）

`AUTO_PK = BigInteger().with_variant(Integer, "sqlite")`。SQLite 的自动行号只认精确的
`INTEGER PRIMARY KEY`；`BigInteger` 渲染成 `BIGINT`，那不是 rowid 别名 ——
autoincrement 会**静默失效**，插第二行就撞主键。MySQL 侧仍要 BIGINT（标注行数会远超
int 范围）。这是旧表的实际缺陷，不是假想的。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

AUTO_PK = sa.BigInteger().with_variant(sa.Integer, "sqlite")
MEDIUMTEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(charset="utf8mb4"), "mysql")

# 帖子级标注不针对某只产品。不能用 NULL 表示 —— SQL 的唯一约束里 NULL 互不相等，
# 两个 NULL 行都能插进去，幂等就废了。
NO_SUBJECT = ""


def upgrade():
    op.drop_index("ix_annotations_target", table_name="annotations")
    op.drop_table("annotations")

    op.create_table(
        "annotation_runs",
        sa.Column("run_id", sa.String(40), primary_key=True),
        sa.Column("task", sa.String(40), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        # 供应商**返回的**模型名，不是我们请求的那个：网关会做别名转发。
        sa.Column("model_id", sa.String(80), nullable=False),
        sa.Column("model_revision", sa.String(80)),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("taxonomy_version", sa.String(40), nullable=False),
        sa.Column("schema_version", sa.String(40), nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("finished_at", sa.DateTime),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("input_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer, nullable=False, server_default="0"),
        # 用量取不到写 NULL，不写 0 —— 写 0 会让成本报表少算而看不出来（铁律 2）。
        sa.Column("token_input", sa.BigInteger),
        sa.Column("token_output", sa.BigInteger),
        sa.Column("token_reasoning", sa.BigInteger),
        sa.Column("estimated_cost", sa.Float),
    )
    op.create_index("ix_runs_task_started", "annotation_runs", ["task", "started_at"])

    op.create_table(
        "annotation_jobs",
        sa.Column("job_id", AUTO_PK, primary_key=True, autoincrement=True),
        sa.Column("target_type", sa.String(10), nullable=False),
        sa.Column("target_id", sa.BigInteger, nullable=False),
        sa.Column("subject_code", sa.String(10), nullable=False,
                  server_default=NO_SUBJECT),
        sa.Column("task", sa.String(40), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("claimed_at", sa.DateTime),
        sa.Column("lease_until", sa.DateTime),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
        # 重复排队 = 重复付费。
        sa.UniqueConstraint("target_type", "target_id", "subject_code", "task",
                            "input_hash", name="uq_jobs_target_input"),
    )
    op.create_index("ix_jobs_claimable", "annotation_jobs",
                    ["status", "priority", "job_id"])

    op.create_table(
        "annotations",
        sa.Column("annotation_id", AUTO_PK, primary_key=True, autoincrement=True),
        sa.Column("target_type", sa.String(10), nullable=False),
        sa.Column("target_id", sa.BigInteger, nullable=False),
        # 判定单元的第二半：一条评论可以对 A 积极、对 B 消极（runbook §10.1）。
        sa.Column("subject_code", sa.String(10), nullable=False,
                  server_default=NO_SUBJECT),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("value_json", sa.Text, nullable=False),
        # **校准后**的概率。模型自报的 confidence 不是概率（§11.1 末条），未校准写 NULL。
        sa.Column("calibrated_confidence", sa.Float),
        sa.Column("run_id", sa.String(40), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("review_state", sa.String(20), nullable=False,
                  server_default="pending"),
        sa.Column("created_at", sa.DateTime, nullable=False),
        # 重跑的新行指向被它取代的旧行。**不删旧行**：回滚时要能回到上一版。
        sa.Column("supersedes_id", sa.BigInteger),
        sa.UniqueConstraint("target_type", "target_id", "subject_code", "kind",
                            "input_hash", "run_id", name="uq_annotations_unit"),
    )
    op.create_index("ix_annotations_target", "annotations",
                    ["target_type", "target_id", "kind"])
    op.create_index("ix_annotations_subject", "annotations",
                    ["subject_code", "kind", "review_state"])
    op.create_index("ix_annotations_run", "annotations", ["run_id"])

    op.create_table(
        "annotation_evidence",
        sa.Column("evidence_id", AUTO_PK, primary_key=True, autoincrement=True),
        sa.Column("annotation_id", sa.BigInteger, nullable=False, index=True),
        sa.Column("source_target_type", sa.String(10), nullable=False),
        sa.Column("source_target_id", sa.BigInteger, nullable=False),
        # 偏移由程序在原文里定位后回填，不采信模型自报的位置 —— Gate 0 首次真实调用
        # 就复现了模型把证据改写成转述。
        sa.Column("start_offset", sa.Integer),
        sa.Column("end_offset", sa.Integer),
        sa.Column("quote_text", sa.Text, nullable=False),
        sa.Column("quote_hash", sa.String(64), nullable=False),
    )
    op.create_index("ix_evidence_source", "annotation_evidence",
                    ["source_target_type", "source_target_id"])

    op.create_table(
        "review_decisions",
        sa.Column("decision_id", AUTO_PK, primary_key=True, autoincrement=True),
        sa.Column("annotation_id", sa.BigInteger, nullable=False, index=True),
        sa.Column("reviewer", sa.String(80), nullable=False),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("corrected_value_json", sa.Text),
        sa.Column("reason_code", sa.String(40)),
        sa.Column("reviewed_at", sa.DateTime, nullable=False),
    )


def downgrade():
    for name in ("review_decisions", "annotation_evidence", "annotations",
                 "annotation_jobs", "annotation_runs"):
        op.drop_table(name)

    op.create_table(
        "annotations",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("target_type", sa.String(10), nullable=False),
        sa.Column("target_id", sa.BigInteger, nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("confidence", sa.Float),
        sa.Column("model", sa.String(60), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_annotations_target", "annotations",
                    ["target_type", "target_id", "kind"])
