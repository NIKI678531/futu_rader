from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_annotations_supersedes", "annotations", ["supersedes_id"])
    op.create_index("ix_annotations_kind_target", "annotations", ["kind", "target_type"])
    op.create_index("ix_synthesis_supersedes", "synthesis_outputs", ["supersedes_id"])


def downgrade():
    op.drop_index("ix_synthesis_supersedes", table_name="synthesis_outputs")
    op.drop_index("ix_annotations_kind_target", table_name="annotations")
    op.drop_index("ix_annotations_supersedes", table_name="annotations")