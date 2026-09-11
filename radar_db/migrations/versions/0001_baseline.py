"""瘦库基线 —— 迁移引入之前由 `create_all()` 建出来的那套表。

Revision ID: 0001
Revises:

这一版**不改变任何东西**，它只是把「在此之前就已经存在的 schema」写下来，好让后面的
迁移有一个共同的起点。

## 已经有库的环境要 stamp，不要 upgrade

本机那个 5.27 GB 的库是 `create_all()` 建的，表已经在了，但 `alembic_version` 表还没有。
对它直接 `upgrade` 会在 `CREATE TABLE feeds` 上炸掉。正确做法是先告诉 Alembic
「你已经在 0001 了」：

    alembic -c radar_db/alembic.ini stamp 0001
    alembic -c radar_db/alembic.ini upgrade head

全新的空库直接 `upgrade head` 即可。

## 类型在这里重新定义了一遍，不是从 schema.py 导入的

迁移是**冻结在时间里**的：它描述的是当时那一刻的 schema。从 `radar_db.schema` 导入
`LONGTEXT` 之类的别名，会让这份历史随着今天的代码一起漂移 —— 某天有人把 LONGTEXT 改成
别的，这份 0001 就会追溯地变成另一回事，而它早就在生产上跑过了。
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

LONGTEXT = sa.Text().with_variant(mysql.LONGTEXT(charset="utf8mb4"), "mysql")
MEDIUMTEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(charset="utf8mb4"), "mysql")


def upgrade():
    # ── 第一层：dump 镜像 ──────────────────────────────────────────────
    op.create_table(
        "src_stocks",
        sa.Column("stock_id", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column("ticker", sa.String(20), nullable=False, unique=True),
        sa.Column("market", sa.String(10), nullable=False),
        sa.Column("instrument_type", sa.String(20), nullable=False),
        sa.Column("name_zh", sa.String(200)),
        sa.Column("name_en", sa.String(200)),
    )
    op.create_table(
        "src_feeds",
        sa.Column("feed_id", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column("stock_id", sa.BigInteger, nullable=False, index=True),
        sa.Column("feed_type", sa.Integer, nullable=False),
        sa.Column("posted_at", sa.DateTime, nullable=False, index=True),
        sa.Column("author_uid", sa.String(40)),
        sa.Column("author_name", sa.String(200)),
        sa.Column("feed_title", MEDIUMTEXT),
        sa.Column("content_text", MEDIUMTEXT),
        sa.Column("like_count", sa.Integer, nullable=False),
        sa.Column("comment_count", sa.Integer, nullable=False),
        sa.Column("image_count", sa.Integer, nullable=False),
        sa.Column("raw_json", LONGTEXT, nullable=False),
        sa.Column("scraped_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "src_users",
        sa.Column("user_id", sa.String(40), primary_key=True),
        sa.Column("nick_name", sa.String(200)),
        sa.Column("follower_num", sa.Integer),
        sa.Column("following_num", sa.Integer),
        sa.Column("home_visitor_num", sa.Integer),
        sa.Column("sns_gender", sa.Integer),
        sa.Column("ip_region", sa.String(80)),
        sa.Column("self_description", MEDIUMTEXT),
        sa.Column("scraped_at", sa.DateTime, nullable=False),
    )

    # ── 第二层：ETL 事实表（ADR-0009） ───────────────────────────────
    op.create_table(
        "feeds",
        sa.Column("feed_id", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column("code", sa.String(10), nullable=False, index=True),
        sa.Column("posted_at", sa.DateTime, nullable=False, index=True),
        sa.Column("feed_type", sa.Integer, nullable=False),
        sa.Column("author_uid", sa.String(40), index=True),
        sa.Column("author_name", sa.String(200), index=True),
        sa.Column("title", MEDIUMTEXT),
        sa.Column("content", MEDIUMTEXT),
        sa.Column("like_count", sa.Integer, nullable=False),
        sa.Column("comment_count", sa.Integer, nullable=False),
        sa.Column("image_count", sa.Integer, nullable=False),
        sa.Column("share_count", sa.Integer),
        sa.Column("browse_count", sa.Integer),
        sa.Column("comments_parsed", sa.Integer),
        sa.Column("comments_truncated", sa.Boolean),
        sa.Column("original_lang", sa.Integer),
        sa.Column("raw_json_broken", sa.Boolean, nullable=False, default=False),
    )
    op.create_index("ix_feeds_code_posted", "feeds", ["code", "posted_at"])

    op.create_table(
        "comments",
        sa.Column("comment_id", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column("feed_id", sa.BigInteger, nullable=False, index=True),
        sa.Column("posted_at", sa.DateTime, index=True),
        sa.Column("author_uid", sa.String(40), index=True),
        sa.Column("author_name", sa.String(200)),
        sa.Column("content", MEDIUMTEXT),
        sa.Column("like_count", sa.Integer),
        sa.Column("reply_to_comment_id", sa.BigInteger),
    )
    op.create_table(
        "mentions",
        sa.Column("feed_id", sa.BigInteger, primary_key=True, autoincrement=False),
        sa.Column("code", sa.String(10), primary_key=True),
        sa.Column("source", sa.String(10), primary_key=True),
        sa.Column("in_pool", sa.Boolean, nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(40), primary_key=True),
        sa.Column("nick_name", sa.String(200), index=True),
        sa.Column("follower_num", sa.Integer),
        sa.Column("following_num", sa.Integer),
        sa.Column("ip_region", sa.String(80)),
        sa.Column("self_description", MEDIUMTEXT),
    )

    # ── ADR-0010 原定的单表标注（0002 会把它换掉） ───────────────────
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

    op.create_table(
        "meta_kv",
        sa.Column("k", sa.String(60), primary_key=True),
        sa.Column("v", sa.Text),
    )


def downgrade():
    for name in ("meta_kv", "annotations", "users", "mentions", "comments",
                 "feeds", "src_users", "src_feeds", "src_stocks"):
        op.drop_table(name)
