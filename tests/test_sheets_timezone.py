from copy import deepcopy
from datetime import datetime, date, timezone
from unittest.mock import patch

import pytest
from gspread.utils import a1_to_rowcol
from sqlalchemy import text, inspect

from tests.test_workflow import fixture
from app.main import app
from app.models import Lead, Organization, User, Campaign, GoogleSheetConnection, IntegrationJob, LeadStatus
from app.services.auth import get_current_user
from app.services.timezones import day_bounds
from app.services.integration_queue import acquire_sheet_lock, enqueue_sheet, process_one
from app.services.lead_ingestion import insert_lead_once
from app.routers.webhooks import _upsert_sheet_lead, build_sheet_headers, SheetLayoutError, sync_lead_to_google_sheets


class Worksheet:
    def __init__(self, rows=None):
        self.rows = deepcopy(rows or [])
        self.col_count = 6
        self.insertions = 0
        self.lose_response = False

    def get_all_values(self):
        return deepcopy(self.rows)

    def add_cols(self, count):
        self.col_count += count

    def update(self, *, range_name, values, value_input_option):
        assert range_name == 'A1' and value_input_option == 'RAW'
        if self.rows:
            self.rows[0] = list(values[0])
        else:
            self.rows = deepcopy(values)

    def insert_row(self, row, *, index, value_input_option):
        assert value_input_option == 'RAW'
        self.rows.insert(index - 1, list(row))
        self.insertions += 1
        if self.lose_response:
            self.lose_response = False
            raise TimeoutError('Response lost after write')

    def batch_update(self, updates, *, value_input_option):
        assert value_input_option == 'RAW'
        for update in updates:
            row, col = a1_to_rowcol(update['range'])
            while len(self.rows[row - 1]) < col:
                self.rows[row - 1].append('')
            self.rows[row - 1][col - 1] = update['values'][0][0]

    def record(self, row=1):
        return dict(zip(self.rows[0], self.rows[row]))


def test_retry_after_lost_response_updates_one_row_and_preserves_user_cells(fixture):
    _, db, _ = fixture
    lead = db.get(Lead, 1)
    lead.phone = '+919876543210'
    lead.notes = '=SUM(1,2)'
    sheet = Worksheet()
    sheet.lose_response = True
    with pytest.raises(TimeoutError):
        _upsert_sheet_lead(sheet, lead)
    sheet.rows[0].append('My formula')
    sheet.rows[1].append('=1+1')
    lead.status = LeadStatus.qualified
    _upsert_sheet_lead(sheet, lead)
    assert sheet.insertions == 1
    assert sheet.record()['CRM Marker'] == 'crm:1:1'
    assert sheet.record()['Status'] == 'qualified'
    assert sheet.record()['Phone'] == '+919876543210'
    assert sheet.record()['Notes'] == '=SUM(1,2)'
    assert sheet.record()['My formula'] == '=1+1'
    assert sheet.record()['Date'] == '2026-01-01 05:30:00+05:30'


def test_mixed_forms_extend_headers_without_shifting_old_answers(fixture):
    _, db, _ = fixture
    lead = db.get(Lead, 1)
    lead.raw_data = {'form_id': 'A', 'field_data': [{'name': 'budget', 'values': ['100']}]}
    sheet = Worksheet()
    _upsert_sheet_lead(sheet, lead)
    marker_column = sheet.rows[0].index('CRM Marker')
    other = Lead(id=3, org_id=1, fb_lead_id='three', organization=lead.organization,
        raw_data={'form_id': 'B', 'field_data': [{'name': 'location', 'values': ['Kochi', 'Dubai']},
            {'name': 'Status', 'values': ['Question answer']}]})
    _upsert_sheet_lead(sheet, other)
    assert sheet.rows[0].index('CRM Marker') == marker_column
    assert sheet.record()['Question: location'] == 'Kochi; Dubai'
    assert sheet.record()['Question: Status'] == 'Question answer'
    assert sheet.record(2)['Question: budget'] == '100'
    assert sheet.record(2)['CRM Marker'] == 'crm:1:1'


@pytest.mark.parametrize('headers', [['Name'], ['Name', 'CRM Marker']])
def test_legacy_rows_are_preserved_and_reported(fixture, headers):
    _, db, _ = fixture
    sheet = Worksheet([headers, ['Existing customer']])
    before = deepcopy(sheet.rows)
    with pytest.raises(SheetLayoutError, match='empty tab'):
        _upsert_sheet_lead(sheet, db.get(Lead, 1))
    assert sheet.rows == before


def test_connect_form_filter_backfill_and_canonical_url(fixture):
    client, db, _ = fixture
    lead = db.get(Lead, 1)
    lead.raw_data = {'form_id': 'A'}
    lead.form_name = 'Form A'
    db.add(Lead(org_id=1, campaign_id=1, fb_lead_id='B', raw_data={'form_id': 'B'}, form_name='Form B'))
    db.commit()
    assert {f['id'] for f in client.get('/integrations/google-sheets/forms?campaign_id=1').json()} == {'A', 'B'}
    payload = {'spreadsheet_url': 'https://docs.google.com/spreadsheets/d/new-sheet/edit#gid=1',
               'sheet_name': 'Form A', 'campaign_id': 1, 'form_id': 'A'}
    response = client.post('/integrations/google-sheets/connect', json=payload)
    assert response.status_code == 200
    assert response.json()['queued_leads'] == 1
    conn_id = response.json()['id']
    assert [j.payload['lead_id'] for j in db.query(IntegrationJob)] == [1]
    payload['spreadsheet_url'] = 'https://docs.google.com/spreadsheets/d/new-sheet/edit?gid=99'
    assert client.post('/integrations/google-sheets/connect', json=payload).json()['id'] == conn_id
    payload['form_id'] = 'B'
    assert client.post('/integrations/google-sheets/connect', json=payload).status_code == 409
    payload['form_id'] = 'unknown'
    assert client.post('/integrations/google-sheets/connect', json=payload).status_code == 400


def test_live_routing_uses_campaign_and_stable_form_id(fixture):
    _, db, _ = fixture
    lead = db.get(Lead, 1)
    lead.raw_data = {'form_id': 'A'}
    db.get(GoogleSheetConnection, 1).form_id = 'B'
    db.add(GoogleSheetConnection(org_id=1, user_id=1, campaign_id=1, form_id='A', spreadsheet_id='a', spreadsheet_url='a', sheet_name='A'))
    db.flush()
    sync_lead_to_google_sheets(db, lead)
    jobs = db.query(IntegrationJob).all()
    assert len(jobs) == 1 and jobs[0].payload['connection_id'] != 1


def test_sheet_lock_serializes_same_destination_across_connections(fixture):
    _, db, _ = fixture
    connection = db.get(GoogleSheetConnection, 1)
    assert acquire_sheet_lock(db, connection)
    alias = GoogleSheetConnection(spreadsheet_id=connection.spreadsheet_id, sheet_name=connection.sheet_name)
    assert acquire_sheet_lock(db, alias) is None
    alias.sheet_name = 'Another tab'
    assert acquire_sheet_lock(db, alias)


@pytest.mark.asyncio
async def test_sheet_layout_failure_is_actionable_and_releases_lock(fixture):
    _, db, factory = fixture
    sync_lead_to_google_sheets(db, db.get(Lead, 1))
    with patch('app.services.integration_queue.SessionLocal', factory), patch(
        'app.routers.webhooks._sync_to_google_sheet', side_effect=SheetLayoutError('Select a new empty tab')):
        await process_one()
    db.expire_all()
    job = db.query(IntegrationJob).one()
    assert job.status == 'failed' and job.last_error == 'Select a new empty tab'
    assert acquire_sheet_lock(db, db.get(GoogleSheetConnection, 1))


def test_status_owner_followup_changes_queue_sheet_updates(fixture):
    client, db, _ = fixture
    assert client.patch('/leads/1/status', json={'status': 'qualified', 'notes': 'Interested'}).status_code == 200
    assert client.patch('/workflow/leads/1', json={'owner_id': 2, 'follow_up_local': '2026-09-12T10:00'}).status_code == 200
    assert db.query(IntegrationJob).count() == 2
    assert db.get(Lead, 1).follow_up_at == datetime(2026, 9, 12, 4, 30)


def test_regional_date_filters_include_indian_midnight_boundary(fixture):
    client, db, _ = fixture
    db.get(Lead, 1).created_at = datetime(2026, 9, 10, 18, 30)
    db.add(Lead(org_id=1, fb_lead_id='earlier', created_at=datetime(2026, 9, 10, 18, 29, 59)))
    db.commit()
    rows = client.get('/leads?date_from=2026-09-11&date_to=2026-09-11').json()
    assert [row['id'] for row in rows] == [1]
    assert client.get('/leads?date_from=bad').status_code == 400
    assert day_bounds(date(2026, 9, 11))[0] == datetime(2026, 9, 10, 18, 30)


def test_all_leads_and_adjacent_ist_days_preserve_every_lead(fixture):
    client, db, _ = fixture
    db.get(Lead, 1).created_at = datetime(2026, 9, 11, 0)
    for index in range(76):
        # 62 submissions on September 11 IST; 15 after midnight on September 12.
        instant = datetime(2026, 9, 11, 12 if index < 61 else 20)
        db.add(Lead(org_id=1, fb_lead_id=f'boundary-{index}', created_at=instant))
    db.commit()
    for legacy_zone in ('UTC', 'Asia/Kolkata', 'America/New_York'):
        db.get(Organization, 1).timezone = legacy_zone
        db.commit()
        all_rows = client.get('/leads')
        today = client.get('/leads?date_from=2026-09-11&date_to=2026-09-11').json()
        tomorrow = client.get('/leads?date_from=2026-09-12&date_to=2026-09-12').json()
        assert all_rows.headers['X-Total-Count'] == '77'
        assert len(today) == 62 and len(tomorrow) == 15
        assert {r['id'] for r in today + tomorrow} == {r['id'] for r in all_rows.json()}
        assert client.get('/leads/stats').json()['total_leads'] == 77


def test_fixed_india_timezone_ignores_legacy_settings(fixture):
    client, db, _ = fixture
    db.get(Organization, 1).timezone = 'America/New_York'
    db.commit()
    assert client.get('/org/settings').json()['timezone'] == 'Asia/Kolkata'
    assert client.patch('/org/settings', json={'timezone': 'Fake/Zone'}).status_code == 422
    assert client.patch('/org/settings', json={'timezone': 'America/New_York'}).status_code == 422
    assert client.patch('/workflow/leads/1', json={'follow_up_local': '2026-07-01T10:00'}).status_code == 200
    assert db.get(Lead, 1).follow_up_at == datetime(2026, 7, 1, 4, 30)
    start, end = day_bounds(date(2026, 3, 8), 'America/New_York')
    assert (end - start).total_seconds() == 23 * 3600
    app.dependency_overrides[get_current_user] = lambda: db.get(User, 2)
    assert client.patch('/org/settings', json={'name': 'Changed'}).status_code == 403
    assert client.post('/integrations/google-sheets/connect', json={'spreadsheet_url': 'https://docs.google.com/spreadsheets/d/x'}).status_code == 403


def test_import_normalizes_utc_and_keeps_same_contact_submissions(fixture):
    _, db, _ = fixture
    for fid in ('A', 'B'):
        lead, added = insert_lead_once(db, Lead(org_id=1, fb_lead_id=fid, phone='123', campaign_name='Campaign',
            created_at=datetime.fromisoformat('2026-09-11T10:00:00+05:30'), raw_data={'form_id': fid}))
        assert added and lead.campaign_id == 1
        assert lead.created_at == datetime(2026, 9, 11, 4, 30)


def test_additive_schema_upgrade_sets_india_without_changing_timestamps(fixture):
    _, db, factory = fixture
    from app.schema_upgrade import upgrade_workflow
    with factory.kw['bind'].begin() as connection:
        connection.execute(text('ALTER TABLE organizations DROP COLUMN timezone'))
        connection.execute(text('ALTER TABLE google_sheet_connections DROP COLUMN form_id'))
        upgrade_workflow(connection)
        upgrade_workflow(connection)
        assert connection.execute(text('SELECT timezone FROM organizations WHERE id=1')).scalar() == 'Asia/Kolkata'
        assert str(connection.execute(text('SELECT created_at FROM leads WHERE id=1')).scalar()).startswith('2026-01-01 00:00:00')


def test_today_counts_and_followups_use_organization_day(fixture):
    client, db, _ = fixture
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 9, 10, 19, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)
    lead = db.get(Lead, 1)
    lead.created_at = datetime(2026, 9, 10, 18, 30)
    lead.follow_up_at = datetime(2026, 9, 11, 18, 29)
    db.add(Lead(org_id=1, fb_lead_id='tomorrow', created_at=datetime(2026, 9, 11, 18, 30), follow_up_at=datetime(2026, 9, 11, 18, 30)))
    db.commit()
    with patch('app.routers.leads.datetime', FrozenDateTime), patch('app.routers.workflow.datetime', FrozenDateTime):
        assert client.get('/leads/stats').json()['new_today'] == 1
        assert [row['id'] for row in client.get('/workflow/follow-ups').json()['items']] == [1]


def test_manual_lead_creation_queues_matching_sheet(fixture):
    client, db, _ = fixture
    response = client.post('/leads', json={'name': 'Manual', 'campaign_name': 'Campaign'})
    assert response.status_code == 200
    assert response.json()['campaign_id'] == 1
    assert db.query(IntegrationJob).one().payload['lead_id'] == response.json()['id']
