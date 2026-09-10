"""Add meta_campaign_id to campaigns for Meta campaign status sync."""
from alembic import op
import sqlalchemy as sa

revision = 'b423_meta_campaign_id'
down_revision = 'b422_unique_leads'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'meta_campaign_id' not in {c['name'] for c in inspector.get_columns('campaigns')}:
        op.add_column('campaigns', sa.Column('meta_campaign_id', sa.String(255), nullable=True))
    if 'ix_campaigns_meta_campaign_id' not in {i['name'] for i in inspector.get_indexes('campaigns')}:
        op.create_index('ix_campaigns_meta_campaign_id', 'campaigns', ['meta_campaign_id'])


def downgrade():
    op.drop_index('ix_campaigns_meta_campaign_id', table_name='campaigns')
    op.drop_column('campaigns', 'meta_campaign_id')
