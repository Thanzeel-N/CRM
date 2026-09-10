"""Database-backed retries; leased claims allow multiple application workers."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy import or_, and_
from sqlalchemy.exc import IntegrityError
from app.database import SessionLocal
from app.models import IntegrationJob, Lead, GoogleSheetConnection, MetaPageConnection

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


async def process_one():
    from app.routers.webhooks import process_meta_body, _sync_to_google_sheet
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
        try:
            if job.kind == 'meta':
                page_id = str(job.payload['entry'][0]['id'])
                conns = db.query(MetaPageConnection).filter(MetaPageConnection.page_id == page_id, MetaPageConnection.status == 'active').all()
                if {c.org_id for c in conns} != {job.org_id}:
                    raise RuntimeError('Facebook page connection changed; reconnect the correct organization')
                await asyncio.wait_for(process_meta_body(job.payload, db), timeout=120)
            else:
                connection = db.query(GoogleSheetConnection).filter(GoogleSheetConnection.id == job.payload['connection_id'], GoogleSheetConnection.org_id == job.org_id, GoogleSheetConnection.status == 'active').first()
                lead = db.query(Lead).filter(Lead.id == job.payload['lead_id'], Lead.org_id == job.org_id).first()
                if not connection or not lead:
                    raise RuntimeError('Lead or Google Sheets connection no longer available')
                await asyncio.to_thread(_sync_to_google_sheet, connection.spreadsheet_id, connection.sheet_name, lead)
            job.status, job.last_error = 'completed', None
        except Exception:
            db.rollback()
            job = db.get(IntegrationJob, job.id)
            job.status = 'failed' if job.attempts >= 5 else 'pending'
            # Never persist external exception strings: request URLs may contain access tokens.
            job.last_error = ('Meta sync failed. Check page connection and permissions.' if job.kind == 'meta' else 'Sheets sync failed. Check credentials, sharing and worksheet name.')
            job.next_attempt_at = now() + timedelta(seconds=min(3600, 30 * 2 ** job.attempts))
            logger.warning('Integration job %s failed on attempt %s', job.id, job.attempts)
        db.commit()
        return True


async def worker():
    while True:
        try:
            if await process_one():
                continue
        except Exception:
            logger.exception('Integration queue polling failed')
        await asyncio.sleep(10)
