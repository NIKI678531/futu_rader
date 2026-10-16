"""0017 -- preserve full market symbols in body mentions.

Catalog product codes fit in ten characters, but body cashtags also contain
non-catalog symbols such as ``HSICURRENT.HK``.  MySQL truncated those symbols
in the composite primary key, so a repeated source row could collide with the
already-truncated value and abort an otherwise idempotent bootstrap page.
"""

import sqlalchemy as sa
from alembic import op


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("mentions") as batch:
        batch.alter_column(
            "code",
            existing_type=sa.String(10),
            type_=sa.String(32),
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table("mentions") as batch:
        batch.alter_column(
            "code",
            existing_type=sa.String(32),
            type_=sa.String(10),
            existing_nullable=False,
        )
