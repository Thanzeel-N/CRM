"""Repair exact source duplicates, preserving recovery snapshots and history."""
import json
from datetime import datetime, timezone
from sqlalchemy import MetaData, Table, select, func, inspect, Index
from app.models import LeadDuplicateArchive

INDEX_NAME = 'uq_leads_org_source'


def repair_and_enforce_unique_sources(connection):
    inspector = inspect(connection)
    for index in inspector.get_indexes('leads') + inspector.get_unique_constraints('leads'):
        if index.get('unique', True) and index.get('column_names') == ['org_id', 'fb_lead_id']:
            return {'groups': 0, 'duplicates_removed': 0}
    LeadDuplicateArchive.__table__.create(connection, checkfirst=True)
    metadata = MetaData()
    leads = Table('leads', metadata, autoload_with=connection)
    archive = LeadDuplicateArchive.__table__
    groups = connection.execute(select(leads.c.org_id, leads.c.fb_lead_id).group_by(
        leads.c.org_id, leads.c.fb_lead_id).having(func.count() > 1)).all()
    # Empty IDs cannot establish identity; stop instead of guessing which people match.
    if any(not source_id or not str(source_id).strip() for _, source_id in groups):
        raise ValueError('Duplicate empty source IDs require manual review')
    tables = set(inspector.get_table_names())
    children = [Table(name, metadata, autoload_with=connection) for name in (
        'lead_status_history', 'lead_activities', 'whatsapp_messages') if name in tables]
    jobs = Table('integration_jobs', metadata, autoload_with=connection) if 'integration_jobs' in tables else None
    removed = 0
    for org_id, source_id in groups:
        rows = [dict(row) for row in connection.execute(select(leads).where(
            leads.c.org_id == org_id, leads.c.fb_lead_id == source_id).order_by(leads.c.id)).mappings()]
        keeper = rows[0]
        keeper_id = keeper['id']
        duplicate_ids = [r['id'] for r in rows[1:]]
        for row in rows:
            connection.execute(archive.insert().values(org_id=org_id, original_lead_id=row['id'],
                canonical_lead_id=keeper_id, snapshot=json.loads(json.dumps(row, default=str))))
        recent = sorted(rows, key=lambda r: (str(r.get('updated_at') or r.get('created_at') or ''), r['id']), reverse=True)
        updates = {}
        for field in ('name', 'email', 'phone', 'campaign_id', 'campaign_name', 'form_name', 'owner_id', 'raw_data'):
            if field in leads.c:
                updates[field] = next((r[field] for r in recent if r.get(field) not in (None, '', {})), keeper.get(field))
        if 'status' in leads.c:
            updates['status'] = next((r['status'] for r in recent if r.get('status') not in (None, 'new')), keeper.get('status'))
        if 'notes' in leads.c:
            notes = list(dict.fromkeys(r['notes'] for r in rows if r.get('notes')))
            updates['notes'] = '\n\n'.join(notes)
        for field in ('created_at', 'first_contacted_at', 'follow_up_at'):
            values = [r[field] for r in rows if r.get(field)]
            if field in leads.c and values:
                updates[field] = min(values)
        if 'updated_at' in leads.c:
            updates['updated_at'] = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(leads.update().where(leads.c.id == keeper_id).values(**updates))
        for child in children:
            connection.execute(child.update().where(child.c.lead_id.in_(duplicate_ids)).values(lead_id=keeper_id))
        if jobs is not None:
            for job in connection.execute(select(jobs).where(jobs.c.org_id == org_id, jobs.c.kind == 'sheets')).mappings().all():
                payload = job['payload']
                if not isinstance(payload, dict) or payload.get('lead_id') not in duplicate_ids:
                    continue
                payload = {**payload, 'lead_id': keeper_id}
                key = f"sheets:{payload['connection_id']}:{keeper_id}"
                existing = connection.execute(select(jobs.c.id).where(jobs.c.job_key == key)).scalar()
                if existing:
                    # Preserve delivery history; prevent duplicate queued appends.
                    if job['status'] == 'completed':
                        connection.execute(jobs.update().where(jobs.c.id == existing).values(status='completed'))
                    connection.execute(jobs.update().where(jobs.c.id == job['id']).values(
                        payload=payload, status='completed', last_error='Duplicate delivery consolidated during lead repair'))
                else:
                    connection.execute(jobs.update().where(jobs.c.id == job['id']).values(payload=payload, job_key=key))
        connection.execute(leads.delete().where(leads.c.id.in_(duplicate_ids)))
        removed += len(duplicate_ids)
    Index(INDEX_NAME, leads.c.org_id, leads.c.fb_lead_id, unique=True).create(connection)
    return {'groups': len(groups), 'duplicates_removed': removed}
