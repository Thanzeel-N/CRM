from datetime import datetime, timezone
from typing import Optional, Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, AwareDatetime, NaiveDatetime
from zoneinfo import ZoneInfo
from dateutil.tz import datetime_exists, datetime_ambiguous
from app.services.timezones import day_bounds, utc_naive, DEFAULT_TIMEZONE
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Lead, LeadActivity, User, UserRole, Campaign, LeadStatus, IntegrationJob
from app.services.auth import get_current_user
from app.routers.leads import _base_lead_query
from app.schemas import LeadOut

router = APIRouter(tags=['workflow'])


class WorkflowUpdate(BaseModel):
    owner_id: Optional[int] = None
    follow_up_at: Optional[AwareDatetime] = None
    follow_up_local: Optional[NaiveDatetime] = None


class ActivityCreate(BaseModel):
    kind: Literal['call', 'note', 'follow_up_completed']
    detail: str = Field(min_length=1, max_length=4000)


def visible_lead(db, user, lead_id):
    lead = _base_lead_query(db, user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(404, 'Lead not found')
    return lead


def activity(db, lead, user, kind, detail):
    db.add(LeadActivity(lead_id=lead.id, actor_name=user.name, kind=kind, detail=detail))


@router.get('/workflow/owners')
def owners(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    query = db.query(User).filter(User.org_id == user.org_id, User.is_active == True)
    if user.role != UserRole.admin:
        query = query.filter(User.id == user.id)
    return [{'id': u.id, 'name': u.name} for u in query.all()]


@router.patch('/workflow/leads/{lead_id}', response_model=LeadOut)
def update_workflow(lead_id: int, payload: WorkflowUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    lead = visible_lead(db, user, lead_id)
    values = payload.model_dump(exclude_unset=True)
    if 'follow_up_local' in values and 'follow_up_at' in values:
        raise HTTPException(400, 'Submit either local time or an explicit UTC/offset time')
    if 'follow_up_local' in values:
        due = payload.follow_up_local
        if due:
            due = due.replace(tzinfo=ZoneInfo(DEFAULT_TIMEZONE))
            if not datetime_exists(due) or datetime_ambiguous(due):
                raise HTTPException(400, 'This time is skipped or repeated by daylight saving. Choose another time or submit an explicit offset.')
        payload.follow_up_at = due
        values['follow_up_at'] = due
    if 'owner_id' in values:
        if user.role != UserRole.admin:
            raise HTTPException(403, 'Only admins can assign lead owners')
        owner = None
        if payload.owner_id is not None:
            owner = db.query(User).filter(User.id == payload.owner_id, User.org_id == user.org_id, User.is_active == True).first()
            if not owner:
                raise HTTPException(400, 'Select an active owner in your organization')
        if lead.owner_id != payload.owner_id:
            activity(db, lead, user, 'owner', 'Owner: ' + (owner.name if owner else 'Campaign assignment'))
            lead.owner_id = payload.owner_id
    if 'follow_up_at' in values:
        due = payload.follow_up_at.astimezone(timezone.utc).replace(tzinfo=None) if payload.follow_up_at else None
        if lead.follow_up_at != due:
            activity(db, lead, user, 'follow_up', f'Follow-up scheduled for {due.isoformat()}Z' if due else 'Follow-up cleared')
            lead.follow_up_at = due
    from app.routers.webhooks import sync_lead_to_google_sheets
    sync_lead_to_google_sheets(db, lead, commit=False, refresh=True)
    db.commit()
    db.refresh(lead)
    return lead


@router.get('/workflow/leads/{lead_id}/activities')
def activities(lead_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    visible_lead(db, user, lead_id)
    return db.query(LeadActivity).filter(LeadActivity.lead_id == lead_id).order_by(LeadActivity.created_at.desc(), LeadActivity.id.desc()).limit(200).all()


@router.post('/workflow/leads/{lead_id}/activities')
def add_activity(lead_id: int, payload: ActivityCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    lead = visible_lead(db, user, lead_id)
    if not payload.detail.strip():
        raise HTTPException(400, 'Enter an activity description')
    activity(db, lead, user, payload.kind, payload.detail.strip())
    if payload.kind == 'follow_up_completed':
        lead.follow_up_at = None
    if payload.kind == 'call' and lead.first_contacted_at is None:
        lead.first_contacted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    from app.routers.webhooks import sync_lead_to_google_sheets
    sync_lead_to_google_sheets(db, lead, commit=False, refresh=True)
    db.commit()
    return {'status': 'saved'}


@router.get('/workflow/follow-ups')
def follow_ups(before: Optional[AwareDatetime] = None, offset: int = Query(0, ge=0), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if before is None:
        region = DEFAULT_TIMEZONE
        _, end = day_bounds(datetime.now(ZoneInfo(region)).date(), region)
        before = end.replace(tzinfo=timezone.utc)
    query = _base_lead_query(db, user).filter(Lead.follow_up_at < before.astimezone(timezone.utc).replace(tzinfo=None), Lead.status.notin_([LeadStatus.converted, LeadStatus.lost]))
    return {'total': query.count(), 'items': [LeadOut.model_validate(l) for l in query.order_by(Lead.follow_up_at, Lead.id).offset(offset).limit(50).all()]}


@router.get('/workflow/analytics')
def analytics(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    leads = _base_lead_query(db, user).all()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    campaign_names = {c.id: c.name for c in db.query(Campaign).filter(Campaign.org_id == user.org_id)}
    campaign_owners = {c.id: c.assigned_user_id for c in db.query(Campaign).filter(Campaign.org_id == user.org_id)}
    users = {u.id: u.name for u in db.query(User).filter(User.org_id == user.org_id)}
    campaigns, agents, delays = {}, {}, []
    overdue = 0
    for lead in leads:
        if lead.follow_up_at and lead.follow_up_at.replace(tzinfo=None) < now and lead.status not in (LeadStatus.converted, LeadStatus.lost):
            overdue += 1
        if lead.first_contacted_at:
            delays.append(max(0, (lead.first_contacted_at.replace(tzinfo=None) - lead.created_at.replace(tzinfo=None)).total_seconds() / 3600))
        owner_id = lead.owner_id or campaign_owners.get(lead.campaign_id)
        for groups, key, name in ((campaigns, lead.campaign_id or lead.campaign_name, campaign_names.get(lead.campaign_id) or lead.campaign_name or 'Unassigned'), (agents, owner_id, users.get(owner_id, 'Unassigned'))):
            row = groups.setdefault(key, {'name': name, 'total': 0, 'converted': 0})
            row['total'] += 1
            row['converted'] += int(lead.status == LeadStatus.converted)
    for groups in (campaigns, agents):
        for row in groups.values():
            row['conversion_rate'] = round(row['converted'] / row['total'] * 100, 1)
    return {'overdue': overdue, 'average_response_hours': round(sum(delays) / len(delays), 1) if delays else None, 'response_samples': len(delays), 'campaigns': list(campaigns.values()), 'agents': list(agents.values())}


@router.get('/workflow/integration-jobs')
def jobs(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role != UserRole.admin:
        raise HTTPException(403, 'Admin access required')
    rows = db.query(IntegrationJob).filter(IntegrationJob.org_id == user.org_id, IntegrationJob.status != 'completed').order_by(IntegrationJob.id.desc()).limit(100).all()
    return [{'id': j.id, 'kind': j.kind, 'status': j.status, 'attempts': j.attempts, 'last_error': j.last_error, 'next_attempt_at': j.next_attempt_at} for j in rows]


@router.post('/workflow/integration-jobs/{job_id}/retry')
def retry_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if user.role != UserRole.admin:
        raise HTTPException(403, 'Admin access required')
    job = db.query(IntegrationJob).filter(IntegrationJob.id == job_id, IntegrationJob.org_id == user.org_id).first()
    if not job:
        raise HTTPException(404, 'Job not found')
    if job.status not in ('failed', 'pending'):
        raise HTTPException(409, 'Job is already running or completed')
    job.status, job.attempts, job.next_attempt_at = 'pending', 0, datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return {'status': 'queued'}
