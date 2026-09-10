"""Form routing, region timezones and serialized worksheet delivery."""
from alembic import op
from app.schema_upgrade import upgrade_workflow
from app.models import SheetDeliveryLock

revision = 'b424_sheets_timezone'
down_revision = 'b423_meta_campaign_id'
branch_labels = None
depends_on = None


def upgrade():
    upgrade_workflow(op.get_bind())
    SheetDeliveryLock.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table('sheet_delivery_locks')
    op.drop_column('google_sheet_connections', 'form_id')
    op.drop_column('organizations', 'timezone')
