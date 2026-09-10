"""Add follow-ups, ownership, activity history and durable integration jobs."""
from alembic import op
from app.database import Base
from app import models  # register all tables
from app.schema_upgrade import upgrade_workflow

revision = 'b421_workflow'
down_revision = '88ecb46cc873'
branch_labels = None
depends_on = None


def upgrade():
    # The original baseline revision is empty; support both fresh and existing DBs.
    bind = op.get_bind()
    Base.metadata.create_all(bind)
    upgrade_workflow(bind)


def downgrade():
    raise RuntimeError('This additive migration preserves lead history; restore a backup to downgrade.')
