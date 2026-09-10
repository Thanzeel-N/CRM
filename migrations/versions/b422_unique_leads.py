"""Consolidate exact duplicate leads and enforce source uniqueness."""
from alembic import op
from app.services.lead_deduplication import repair_and_enforce_unique_sources

revision = 'b422_unique_leads'
down_revision = 'b421_workflow'
branch_labels = None
depends_on = None


def upgrade():
    repair_and_enforce_unique_sources(op.get_bind())


def downgrade():
    # Recovery snapshots remain available; duplicate leads are not reintroduced.
    op.drop_index('uq_leads_org_source', table_name='leads')
