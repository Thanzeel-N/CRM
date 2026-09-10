from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock, MagicMock
import pytest
from sqlalchemy import text, select, func
from sqlalchemy.exc import IntegrityError
from app.models import Lead, LeadDuplicateArchive, LeadActivity, LeadStatusHistory, WhatsAppMessage, IntegrationJob, LeadStatus
from app.services.lead_deduplication import repair_and_enforce_unique_sources
from app.services.lead_ingestion import insert_lead_once
from tests.test_workflow import fixture


def test_repair_33_rows_to_11_and_preserve_history(fixture):
    client, db, factory = fixture
    db.execute(text('DROP INDEX uq_leads_org_source'))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.get(Lead, 1).created_at = now
    duplicate_id = None
    for i in range(11):
        source_id = 'one' if i == 0 else f'lead-{i}'
        for copy in range(3):
            if i == 0 and copy == 0:
                continue
            lead = Lead(org_id=1, fb_lead_id=source_id, name=f'Person {i}', created_at=now,
                        notes=f'Note from copy {copy}', status=LeadStatus.qualified if copy == 2 else LeadStatus.new)
            db.add(lead)
            db.flush()
            if i == 0 and copy == 2:
                duplicate_id = lead.id
    db.add(LeadActivity(lead_id=duplicate_id, actor_name='Agent', kind='note', detail='Keep this activity'))
    db.add(LeadStatusHistory(lead_id=duplicate_id, old_status='new', new_status='qualified'))
    db.add(WhatsAppMessage(lead_id=duplicate_id, direction='inbound', message_text='Keep this history'))
    db.add_all([
        IntegrationJob(org_id=1, kind='sheets', job_key='sheets:1:1', payload={'connection_id':1,'lead_id':1}, status='pending'),
        IntegrationJob(org_id=1, kind='sheets', job_key=f'sheets:1:{duplicate_id}', payload={'connection_id':1,'lead_id':duplicate_id}, status='completed'),
    ])
    db.commit()
    assert client.get('/leads/stats').json()['new_today'] == 33
    with factory.kw['bind'].begin() as connection:
        assert repair_and_enforce_unique_sources(connection) == {'groups': 11, 'duplicates_removed': 22}
    db.expire_all()
    assert client.get('/leads/stats').json()['new_today'] == 11
    response = client.get('/leads')
    assert response.headers['X-Total-Count'] == '11'
    assert len(response.json()) == 11
    assert db.query(LeadDuplicateArchive).count() == 33
    assert db.query(LeadActivity).one().lead_id == 1
    assert db.query(LeadStatusHistory).one().lead_id == 1
    assert db.query(WhatsAppMessage).one().lead_id == 1
    assert db.get(Lead, 1).status == LeadStatus.qualified
    assert 'Note from copy 1' in db.get(Lead, 1).notes
    assert 'Note from copy 2' in db.get(Lead, 1).notes
    assert all(j.status == 'completed' for j in db.query(IntegrationJob).all())
    assert db.get(Lead, 2).org_id == 2  # Other organization stays intact.
    with factory.kw['bind'].begin() as connection:
        assert repair_and_enforce_unique_sources(connection)['duplicates_removed'] == 0


def test_three_imports_insert_only_once(fixture):
    _, db, _ = fixture
    counts = []
    for attempt in range(3):
        added = 0
        for i in range(11):
            _, inserted = insert_lead_once(db, Lead(org_id=1, fb_lead_id=f'meta-{i}', name='Person'))
            added += inserted
        db.commit()
        counts.append(added)
    assert counts == [11, 0, 0]


def test_unique_identity_is_scoped_to_organization_not_contact(fixture):
    _, db, _ = fixture
    _, inserted = insert_lead_once(db, Lead(org_id=2, fb_lead_id='one', phone='123', email='shared@example.com'))
    assert inserted
    for source in ['submission-a', 'submission-b']:
        _, inserted = insert_lead_once(db, Lead(org_id=1, fb_lead_id=source, phone='123', email='shared@example.com'))
        assert inserted
    db.commit()
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.add(Lead(org_id=1, fb_lead_id='one'))
            db.flush()


def test_insert_race_recovers_without_rolling_back_batch(fixture):
    _, db, _ = fixture
    other = Lead(org_id=1, fb_lead_id='unrelated-new')
    db.add(other)
    db.flush()
    query = db.query
    stale_lookup = MagicMock()
    stale_lookup.filter.return_value.first.return_value = None
    # Simulate a read that missed the row another importer just committed.
    with patch.object(db, 'query', side_effect=[stale_lookup, query(Lead)]):
        lead, inserted = insert_lead_once(db, Lead(org_id=1, fb_lead_id='one'))
    assert not inserted and lead.id == 1
    db.commit()
    assert db.query(Lead).filter_by(fb_lead_id='unrelated-new').count() == 1


def test_manual_meta_sync_counts_repeated_payload_once(fixture):
    client, db, _ = fixture
    from app.models import MetaPageConnection
    db.get(MetaPageConnection, 1).connected_forms = [{'id':'form','name':'Form'}]
    db.commit()
    rows = [{'id':f'sync-{i}', 'created_time':'2026-09-10T08:00:00Z', 'field_data':[]} for i in range(11)]
    response = MagicMock()
    response.json.return_value = {'data': rows + rows + rows}
    with patch('app.routers.meta_oauth.httpx.AsyncClient') as mocked:
        client_mock = AsyncMock()
        mocked.return_value.__aenter__.return_value = client_mock
        client_mock.get.return_value = response
        first = client.post('/integrations/facebook/sync')
        second = client.post('/integrations/facebook/sync')
    assert first.status_code == 200
    assert first.json()['sync_results']['leads_imported'] == 11
    assert first.json()['sync_results']['duplicates_skipped'] == 22
    assert second.json()['sync_results']['leads_imported'] == 0
    assert db.query(Lead).filter(Lead.fb_lead_id.like('sync-%')).count() == 11
