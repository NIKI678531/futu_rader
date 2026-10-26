"""0018 -- retain structured product identity for body mentions.

Off-pool tickers cannot be classified safely from their spelling.  Futu's
rich-text stock segment already identifies the instrument type, so retain
that source fact and let account pages display only confirmed ETFs.
"""

import sqlalchemy as sa
from alembic import op


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("mentions") as batch:
        batch.add_column(sa.Column("display_symbol", sa.String(64), nullable=True))
        batch.add_column(sa.Column("display_name", sa.String(300), nullable=True))
        batch.add_column(sa.Column("instrument_type_v2", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("sub_instrument_type_v2", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("mentions") as batch:
        batch.drop_column("sub_instrument_type_v2")
        batch.drop_column("instrument_type_v2")
        batch.drop_column("display_name")
        batch.drop_column("display_symbol")
