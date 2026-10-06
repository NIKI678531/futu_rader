"""0013 -- database-backed HKEX product catalog and snapshots."""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

DATETIME = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
LONGTEXT = sa.Text().with_variant(mysql.LONGTEXT(charset="utf8mb4"), "mysql")
DATETIME_COLUMNS = (
    ("product_catalog_snapshots", "fetched_at", False),
    ("product_catalog_entries", "first_seen_at", False),
    ("product_catalog_entries", "last_seen_at", False),
    ("product_catalog_entries", "reviewed_at", True),
)


def upgrade():
    op.create_table(
        "product_catalog_snapshots",
        sa.Column("snapshot_id", sa.String(64), primary_key=True),
        sa.Column("fetched_at", DATETIME, nullable=False),
        sa.Column("source_url", sa.String(500), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text()),
    )
    op.create_index("ix_product_catalog_snapshots_fetched", "product_catalog_snapshots", ["fetched_at"])
    op.create_table(
        "product_catalog_entries",
        sa.Column("code", sa.String(10), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("issuer", sa.String(255), nullable=False),
        sa.Column("ownership", sa.String(10), nullable=False),
        sa.Column("own_code", sa.String(10)),
        sa.Column("sector", sa.String(20), nullable=False),
        sa.Column("sector_name", sa.String(80), nullable=False),
        sa.Column("struct", sa.String(80), nullable=False),
        sa.Column("listing_date", sa.Date(), nullable=False),
        sa.Column("southbound", sa.Boolean(), nullable=False),
        sa.Column("benchmark", sa.String(500)),
        sa.Column("security_type", sa.String(120)),
        sa.Column("product_subcategory", sa.String(160)),
        sa.Column("management_style", sa.String(120)),
        sa.Column("replication_method", sa.String(120)),
        sa.Column("management_fee", sa.String(80)),
        sa.Column("asset_class", sa.String(120)),
        sa.Column("geographic_focus", sa.String(160)),
        sa.Column("currencies_json", sa.Text(), nullable=False),
        sa.Column("aliases_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("match_reason", sa.Text()),
        sa.Column("source_snapshot_id", sa.String(64)),
        sa.Column("source_json", LONGTEXT),
        sa.Column("first_seen_at", DATETIME, nullable=False),
        sa.Column("last_seen_at", DATETIME, nullable=False),
        sa.Column("reviewed_at", DATETIME),
        sa.Column("reviewed_by", sa.String(120)),
        sa.Column("review_note", sa.Text()),
    )
    op.create_index("ix_product_catalog_entries_status", "product_catalog_entries", ["status"])
    op.create_index("ix_product_catalog_entries_owner", "product_catalog_entries", ["ownership", "status"])

    # The migration itself establishes the reviewed 135-product baseline.  A
    # deployment therefore never has an empty catalog between schema upgrade
    # and the first 07:00 HKEX discovery run.
    from radar_db.product_catalog import (
        CATALOG_POLICY_VERSION,
        _entry_values,
        catalog_digest,
        load_products,
    )
    from radar_db.schema import meta_kv, product_catalog_entries

    bind = op.get_bind()
    seeded_at = datetime.now(UTC).replace(tzinfo=None)
    products = load_products()
    bind.execute(
        product_catalog_entries.insert(),
        [_entry_values(product, seeded_at) for product in products],
    )
    bind.execute(meta_kv.delete().where(meta_kv.c.k.in_((
        "product_catalog_policy_version", "product_catalog_revision",
    ))))
    bind.execute(meta_kv.insert().values(
        k="product_catalog_policy_version", v=CATALOG_POLICY_VERSION,
    ))
    bind.execute(meta_kv.insert().values(
        k="product_catalog_revision", v=catalog_digest(products),
    ))


def downgrade():
    meta = sa.table("meta_kv", sa.column("k", sa.String()), sa.column("v", sa.Text()))
    op.execute(meta.delete().where(meta.c.k.in_((
        "product_catalog_policy_version", "product_catalog_revision",
        "product_catalog_last_sync_at", "product_catalog_last_sync_status",
        "product_catalog_activated_at",
    ))))
    op.drop_index("ix_product_catalog_entries_owner", table_name="product_catalog_entries")
    op.drop_index("ix_product_catalog_entries_status", table_name="product_catalog_entries")
    op.drop_table("product_catalog_entries")
    op.drop_index("ix_product_catalog_snapshots_fetched", table_name="product_catalog_snapshots")
    op.drop_table("product_catalog_snapshots")
