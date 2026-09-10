"""Additive upgrade shared by the development bootstrap and Alembic."""
from sqlalchemy import inspect, text


def upgrade_workflow(connection):
    if 'google_sheet_connections' in inspect(connection).get_table_names():
        sheet_columns = {c['name'] for c in inspect(connection).get_columns('google_sheet_connections')}
        if 'campaign_id' not in sheet_columns:
            connection.execute(text('ALTER TABLE google_sheet_connections ADD COLUMN campaign_id INTEGER REFERENCES campaigns(id)'))
    columns = {c['name'] for c in inspect(connection).get_columns('leads')}
    for name, definition in {
        'owner_id': 'INTEGER REFERENCES users(id)',
        'follow_up_at': 'DATETIME',
        'first_contacted_at': 'DATETIME',
    }.items():
        if name not in columns:
            connection.execute(text(f'ALTER TABLE leads ADD COLUMN {name} {definition}'))
    indexes = {i['name'] for i in inspect(connection).get_indexes('leads')}
    for name in ('owner_id', 'follow_up_at'):
        if f'ix_leads_{name}' not in indexes:
            connection.execute(text(f'CREATE INDEX ix_leads_{name} ON leads ({name})'))
