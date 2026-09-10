"""Database-backed retries; leased claims allow multiple application workers."""
import asyncio
import logging
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import or_, and_
from sqlalchemy.exc import IntegrityError
from app.database import SessionLocal
from app.models import IntegrationJob, Lead, GoogleSheetConnection, MetaPageConnection, SheetDeliveryLock

logger = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enqueue(db, org_id, kind, key, payload):
    if db.query(IntegrationJob.id).filter(IntegrationJob.job_key == key).first():
        return
    try:
        with db.begin_nested():
            db.add(IntegrationJob(org_id=org_id, kind=kind, job_key=key, payload=payload, next_attempt_at=now()))
            db.flush()
    except IntegrityError:
        pass  # A concurrent delivery already queued this event.


def enqueue_sheet(db, connection, lead, *, refresh=False):
    suffix = ':' + uuid.uuid4().hex if refresh else ''
    enqueue(db, lead.org_id, 'sheets', f'sheets:{connection.id}:{lead.id}{suffix}',
            {'connection_id': connection.id, 'lead_id': lead.id})


def acquire_sheet_lock(db, connection):
    destination = hashlib.sha256(f'{connection.spreadsheet_id}\n{connection.sheet_name}'.encode()).hexdigest()
    token = uuid.uuid4().hex
    if not db.get(SheetDeliveryLock, destination):
        try:
            with db.begin_nested():
                db.add(SheetDeliveryLock(destination=destination, expires_at=now()))
                db.flush()
        except IntegrityError:
            pass
    claimed = db.query(SheetDeliveryLock).filter(
        SheetDeliveryLock.destination == destination, SheetDeliveryLock.expires_at <= now()
    ).update({'token': token, 'expires_at': now() + timedelta(minutes=10)}, synchronize_session=False)
    db.commit()
    return (destination, token) if claimed else None


async def process_one():
    from app.routers.webhooks import process_meta_body, _sync_to_google_sheet, sheet_matches_lead, SheetLayoutError
    with SessionLocal() as db:
        eligible = and_(IntegrationJob.status.in_(['pending', 'processing']), IntegrationJob.next_attempt_at <= now())
        job = db.query(IntegrationJob).filter(eligible).order_by(IntegrationJob.id).first()
        if not job:
            return False
        claimed = db.query(IntegrationJob).filter(IntegrationJob.id == job.id, eligible).update({
            IntegrationJob.status: 'processing', IntegrationJob.next_attempt_at: now() + timedelta(minutes=10),
            IntegrationJob.attempts: IntegrationJob.attempts + 1,
        }, synchronize_session=False)
        db.commit()
        if not claimed:
            return True
        db.refresh(job)
        sheet_lock = None
        try:
            if job.kind == 'meta':
                page_id = str(job.payload['entry'][0]['id'])
                conns = db.query(MetaPageConnection).filter(MetaPageConnection.page_id == page_id, MetaPageConnection.status == 'active').all()
                if {c.org_id for c in conns} != {job.org_id}:
                    raise RuntimeError('Facebook page connection changed; reconnect the correct organization')
                await asyncio.wait_for(process_meta_body(job.payload, db), timeout=120)
            elif job.kind == 'meta_import':
                await asyncio.wait_for(_import_form_leads(job, db), timeout=600)
            else:
                connection = db.query(GoogleSheetConnection).filter(GoogleSheetConnection.id == job.payload['connection_id'], GoogleSheetConnection.org_id == job.org_id, GoogleSheetConnection.status == 'active').first()
                lead = db.query(Lead).filter(Lead.id == job.payload['lead_id'], Lead.org_id == job.org_id).first()
                if not connection or not lead:
                    raise RuntimeError('Lead or Google Sheets connection no longer available')
                if not sheet_matches_lead(connection, lead):
                    job.status, job.last_error = 'completed', None
                    db.commit()
                    return True
                sheet_lock = acquire_sheet_lock(db, connection)
                if not sheet_lock:
                    job.status = 'pending'
                    job.attempts -= 1
                    job.next_attempt_at = now() + timedelta(seconds=10)
                    db.commit()
                    return True
                # Resolve ORM relationships on this thread before calling Google.
                _ = lead.organization, lead.owner
                if lead.campaign:
                    _ = lead.campaign.assigned_user
                await asyncio.to_thread(_sync_to_google_sheet, connection.spreadsheet_id, connection.sheet_name, lead)
            job.status, job.last_error = 'completed', None
        except Exception as exc:
            db.rollback()
            job = db.get(IntegrationJob, job.id)
            job.status = 'failed' if job.attempts >= 5 else 'pending'
            # Never persist external exception strings: request URLs may contain access tokens.
            job.last_error = ('Meta sync failed. Check page connection and permissions.' if job.kind == 'meta' else 'Sheets sync failed. Check credentials, sharing and worksheet name.')
            if isinstance(exc, SheetLayoutError):
                job.status, job.last_error = 'failed', str(exc)
            job.next_attempt_at = now() + timedelta(seconds=min(3600, 30 * 2 ** job.attempts))
            logger.warning('Integration job %s failed on attempt %s', job.id, job.attempts)
        finally:
            if sheet_lock:
                destination, token = sheet_lock
                db.query(SheetDeliveryLock).filter(SheetDeliveryLock.destination == destination,
                    SheetDeliveryLock.token == token).update({'expires_at': now(), 'token': None}, synchronize_session=False)
        db.commit()
        return True


async def _import_form_leads(job, db):
    """Paginate all historical leads from one Meta form and insert them into the CRM."""
    import httpx
    import dateutil.parser
    from app.services.lead_ingestion import insert_lead_once
    from app.services.meta import parse_field_data, extract_field, PHONE_FIELD_NAMES, EMAIL_FIELD_NAMES, NAME_FIELD_NAMES

    payload = job.payload
    page_id = payload['page_id']
    form_id = payload['form_id']
    form_name = payload.get('form_name') or f'Form #{form_id}'

    conn = db.query(MetaPageConnection).filter(
        MetaPageConnection.page_id == page_id,
        MetaPageConnection.org_id == job.org_id,
        MetaPageConnection.status == 'active',
    ).first()
    if not conn:
        raise RuntimeError('Meta page connection no longer active; reconnect before retrying')

    access_token = conn.access_token
    FB_API_BASE = 'https://graph.facebook.com/v20.0'
    url_leads = f'{FB_API_BASE}/{form_id}/leads'
    params_leads = {
        'access_token': access_token,
        'fields': 'id,created_time,field_data,campaign_id,campaign_name,form_id',
        'limit': 100,
    }

    imported = 0
    skipped = 0

    async with httpx.AsyncClient(timeout=30) as client:
        while url_leads:
            resp = await client.get(url_leads, params=params_leads)
            data = resp.json()
            if 'error' in data:
                raise RuntimeError(f"Meta API error fetching leads for form {form_id}: {data['error'].get('message')}")

            for l in data.get('data', []):
                fb_lead_id = l.get('id')
                if not fb_lead_id:
                    continue

                fields = parse_field_data(l.get('field_data', []))
                created_at_val = None
                if 'created_time' in l:
                    try:
                        created_at_val = dateutil.parser.parse(l['created_time'])
                    except Exception:
                        pass

                target_form_id = str(l.get('form_id') or form_id)
                from app.models import Lead
                new_lead = Lead(
                    org_id=job.org_id,
                    fb_lead_id=fb_lead_id,
                    name=extract_field(fields, *NAME_FIELD_NAMES),
                    email=extract_field(fields, *EMAIL_FIELD_NAMES),
                    phone=extract_field(fields, *PHONE_FIELD_NAMES),
                    campaign_name=l.get('campaign_name'),
                    form_name=form_name,
                    raw_data={**l, 'form_id': target_form_id},
                    created_at=created_at_val,
                )
                new_lead, inserted = insert_lead_once(db, new_lead)
                if inserted:
                    from app.routers.webhooks import sync_lead_to_google_sheets
                    sync_lead_to_google_sheets(db, new_lead, commit=False)
                    imported += 1
                else:
                    skipped += 1

            url_leads = data.get('paging', {}).get('next')
            params_leads = None

    db.commit()
    logger.info('meta_import form=%s org=%s: imported=%s skipped=%s', form_id, job.org_id, imported, skipped)


def enqueue_import(db, org_id, page_id, form_id, form_name):
    """Enqueue a background historical lead import for one Meta form."""
    import uuid
    key = f'meta_import:{org_id}:{page_id}:{form_id}'
    enqueue(db, org_id, 'meta_import', key, {
        'page_id': page_id,
        'form_id': form_id,
        'form_name': form_name,
    })


async def worker():
    while True:
        try:
            if await process_one():
                continue
        except Exception:
            logger.exception('Integration queue polling failed')
        await asyncio.sleep(10)
