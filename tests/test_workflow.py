from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.main import app
from app.database import Base, get_db
from app.models import Organization, User, UserRole, Lead, Campaign, GoogleSheetConnection, MetaPageConnection, IntegrationJob
from app.services.auth import get_current_user
from app.services.integration_queue import process_one
from app.schema_upgrade import upgrade_workflow


@pytest.fixture
def fixture():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    db.add_all([Organization(id=1, name='One', slug='one'), Organization(id=2, name='Two', slug='two')])
    db.add_all([User(id=1, org_id=1, name='Admin', email='admin@test.com', hashed_password='x', role=UserRole.admin), User(id=2, org_id=1, name='Agent', email='agent@test.com', hashed_password='x', role=UserRole.agent), User(id=3, org_id=2, name='Other', email='other@test.com', hashed_password='x', role=UserRole.admin)])
    db.add(Campaign(id=1, org_id=1, name='Campaign', assigned_user_id=2))
    db.add_all([Lead(id=1, org_id=1, campaign_id=1, fb_lead_id='one', name='Lead', created_at=datetime(2026, 1, 1)), Lead(id=2, org_id=2, fb_lead_id='two', name='Private')])
    db.add(GoogleSheetConnection(id=1, org_id=1, user_id=1, campaign_id=1, spreadsheet_id='sheet', spreadsheet_url='https://docs.google.com/spreadsheets/d/sheet', sheet_name='Sheet1'))
    db.add(MetaPageConnection(id=1, org_id=1, user_id=1, page_id='page', page_name='Page', access_token='secret'))
    db.commit()
    old = dict(app.dependency_overrides)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: db.get(User, 1)
    client = TestClient(app)
    yield client, db, factory
    app.dependency_overrides.clear()
    app.dependency_overrides.update(old)
    db.close()
    engine.dispose()


def test_archive_preserves_leads_sheets_and_can_restore(fixture):
    client, db, _ = fixture
    assert client.delete('/campaigns/1').json()['status'] == 'archived'
    assert db.get(Lead, 1) and db.get(GoogleSheetConnection, 1)
    assert not db.get(Campaign, 1).is_active
    assert client.patch('/campaigns/1', json={'is_active': True}).json()['is_active']


def test_follow_up_timezone_completion_and_timeline(fixture):
    client, db, _ = fixture
    result = client.patch('/workflow/leads/1', json={'follow_up_at': '2026-09-10T10:00:00+05:30', 'owner_id': 2})
    assert result.status_code == 200
    assert db.get(Lead, 1).follow_up_at == datetime(2026, 9, 10, 4, 30)
    assert result.json()['owner_name'] == 'Agent'
    assert client.get('/workflow/follow-ups', params={'before': '2026-09-11T00:00:00Z'}).json()['total'] == 1
    assert client.post('/workflow/leads/1/activities', json={'kind': 'follow_up_completed', 'detail': 'Called customer'}).status_code == 200
    assert client.get('/workflow/follow-ups', params={'before': '2026-09-11T00:00:00Z'}).json()['total'] == 0
    rows = client.get('/workflow/leads/1/activities').json()
    assert len(rows) == 3 and all(r['actor_name'] == 'Admin' for r in rows)


def test_owner_and_activity_tenant_isolation(fixture):
    client, db, _ = fixture
    assert client.patch('/workflow/leads/1', json={'owner_id': 3}).status_code == 400
    assert client.patch('/workflow/leads/2', json={'owner_id': 1}).status_code == 404
    assert client.get('/workflow/leads/2/activities').status_code == 404
    app.dependency_overrides[get_current_user] = lambda: db.get(User, 2)
    assert client.patch('/workflow/leads/1', json={'owner_id': 2}).status_code == 403
    assert client.post('/leads/simulate', json={'name': 'X', 'email': 'x@test.com', 'phone': '123', 'campaign_name': 'X', 'form_name': 'X'}).status_code == 403
    assert client.get('/workflow/integration-jobs').status_code == 403


def test_explicit_owner_overrides_campaign_and_manual_agent_lead_visible(fixture):
    client, db, _ = fixture
    client.patch('/workflow/leads/1', json={'owner_id': 1})
    app.dependency_overrides[get_current_user] = lambda: db.get(User, 2)
    assert client.get('/leads/1').status_code == 404
    result = client.post('/leads', json={'name': 'Manual lead'}).json()
    assert client.get(f"/leads/{result['id']}").status_code == 200


def test_status_and_notes_record_actor_and_response_metrics(fixture):
    client, db, _ = fixture
    assert client.patch('/leads/1/status', json={'status': 'contacted', 'notes': 'Interested'}).status_code == 200
    rows = client.get('/workflow/leads/1/activities').json()
    assert {r['kind'] for r in rows} == {'status', 'note'}
    assert db.get(Lead, 1).first_contacted_at is not None
    assert client.get('/workflow/analytics').json()['response_samples'] == 1
    assert client.patch('/workflow/leads/1', json={'follow_up_at': '2026-09-10T10:00:00'}).status_code == 422


def notification(page='page'):
    return {'entry': [{'id': page, 'changes': [{'value': {'leadgen_id': 'meta-new'}}]}]}


def test_unknown_and_ambiguous_pages_never_queue(fixture):
    client, db, _ = fixture
    assert client.post('/webhooks/meta', json=notification('unknown')).status_code == 200
    assert db.query(IntegrationJob).count() == 0
    db.add(MetaPageConnection(org_id=2, user_id=3, page_id='page', page_name='Other page', access_token='other'))
    db.commit()
    client.post('/webhooks/meta', json=notification())
    assert db.query(IntegrationJob).count() == 0


@pytest.mark.asyncio
async def test_meta_retry_persists_then_recovers_without_duplicates(fixture):
    client, db, factory = fixture
    client.post('/webhooks/meta', json=notification())
    client.post('/webhooks/meta', json=notification())
    assert db.query(IntegrationJob).count() == 1
    with patch('app.services.integration_queue.SessionLocal', factory), patch('app.routers.webhooks.fetch_lead_details', AsyncMock(side_effect=RuntimeError('secret-token'))):
        assert await process_one()
    db.expire_all()
    job = db.query(IntegrationJob).one()
    assert job.status == 'pending' and job.attempts == 1
    assert 'secret-token' not in job.last_error
    assert db.query(Lead).count() == 2
    client.post(f'/workflow/integration-jobs/{job.id}/retry')
    details = {'form_id': 'form', 'campaign_name': 'Campaign', 'field_data': [{'name': 'full_name', 'values': ['Real lead']}]}
    with patch('app.services.integration_queue.SessionLocal', factory), patch('app.routers.webhooks.fetch_lead_details', AsyncMock(return_value=details)):
        assert await process_one()
    db.expire_all()
    assert db.get(IntegrationJob, job.id).status == 'completed'
    lead = db.query(Lead).filter_by(fb_lead_id='meta-new').one()
    assert lead.campaign_id == 1 and lead.org_id == 1
    assert db.query(IntegrationJob).filter_by(kind='sheets').count() == 1
    client.post('/webhooks/meta', json=notification())
    assert db.query(Lead).count() == 3


@pytest.mark.asyncio
async def test_sheets_failure_is_visible_and_can_recover(fixture):
    client, db, factory = fixture
    from app.routers.webhooks import sync_lead_to_google_sheets
    sync_lead_to_google_sheets(db, db.get(Lead, 1))
    with patch('app.services.integration_queue.SessionLocal', factory), patch('app.routers.webhooks._sync_to_google_sheet', side_effect=RuntimeError('failed')):
        await process_one()
    db.expire_all()
    job = db.query(IntegrationJob).one()
    assert client.get('/workflow/integration-jobs').json()[0]['last_error']
    client.post(f'/workflow/integration-jobs/{job.id}/retry')
    with patch('app.services.integration_queue.SessionLocal', factory), patch('app.routers.webhooks._sync_to_google_sheet') as sync:
        await process_one()
        sync.assert_called_once()
    db.expire_all()
    assert db.get(IntegrationJob, job.id).status == 'completed'


def test_sync_activity_pagination_preserves_history_and_tenant_scope(fixture):
    client, db, _ = fixture
    for index in range(12):
        db.add(IntegrationJob(org_id=1, job_key=f'page-{index}', kind='sheets', payload={}, status='failed'))
    db.add(IntegrationJob(org_id=2, job_key='private-job', kind='sheets', payload={}, status='failed'))
    db.add(IntegrationJob(org_id=1, job_key='done-job', kind='sheets', payload={}, status='completed'))
    db.commit()
    pages = [client.get(f'/workflow/integration-jobs?paginated=true&limit=5&offset={offset}').json() for offset in (0, 5, 10)]
    assert [len(page['items']) for page in pages] == [5, 5, 2]
    assert all(page['total'] == 12 for page in pages)
    ids = [row['id'] for page in pages for row in page['items']]
    assert len(set(ids)) == 12 and ids == sorted(ids, reverse=True)
    assert client.get('/workflow/integration-jobs?offset=-1').status_code == 422
    assert db.query(IntegrationJob).count() == 14


def test_upgrade_existing_database_preserves_leads():
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE leads (id INTEGER PRIMARY KEY, name TEXT)'))
        connection.execute(text("INSERT INTO leads (name) VALUES ('Keep me')"))
        upgrade_workflow(connection)
        upgrade_workflow(connection)
        assert connection.execute(text('SELECT name FROM leads')).scalar() == 'Keep me'
        assert {'owner_id', 'follow_up_at', 'first_contacted_at'} <= {c['name'] for c in inspect(connection).get_columns('leads')}
