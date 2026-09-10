"""One database identity per organization and source submission."""
from sqlalchemy.exc import IntegrityError
from app.models import Lead, Campaign


def resolve_campaign(db, lead):
    if lead.campaign_id:
        return
    raw = lead.raw_data or {}
    meta_id = str(raw.get('campaign_id') or '')
    if not meta_id and not lead.campaign_name:
        return
    query = db.query(Campaign).filter(Campaign.org_id == lead.org_id)
    campaign = query.filter(Campaign.meta_campaign_id == meta_id).first() if meta_id else None
    if not campaign and lead.campaign_name:
        candidates = query.filter(Campaign.name == lead.campaign_name).all()
        candidates = [c for c in candidates if not meta_id or c.meta_campaign_id in (None, '', meta_id)]
        if len(candidates) == 1:
            campaign = candidates[0]
        elif not candidates:
            campaign = Campaign(org_id=lead.org_id, name=lead.campaign_name, meta_campaign_id=meta_id or None)
            db.add(campaign)
            db.flush()
    if campaign:
        if meta_id and not campaign.meta_campaign_id:
            campaign.meta_campaign_id = meta_id
        lead.campaign_id = campaign.id


def insert_lead_once(db, lead):
    """Return (lead, inserted), recovering only a source-identity conflict.

    A savepoint keeps other imported leads intact if another request wins the
    insert race. The unique database index is the final arbiter, not this read.
    """
    lead.fb_lead_id = str(lead.fb_lead_id or '').strip()
    if not lead.fb_lead_id:
        raise ValueError('A source submission ID is required')
    identity = (Lead.org_id == lead.org_id, Lead.fb_lead_id == lead.fb_lead_id)
    existing = db.query(Lead).filter(*identity).first()
    if existing:
        return existing, False
    from app.services.timezones import utc_naive
    lead.created_at = utc_naive(lead.created_at)
    resolve_campaign(db, lead)
    try:
        with db.begin_nested():
            db.add(lead)
            db.flush()
    except IntegrityError:
        # A locking/current read sees the winning row under MySQL repeatable-read.
        existing = db.query(Lead).filter(*identity).with_for_update().first()
        if existing:
            return existing, False
        raise
    return lead, True
