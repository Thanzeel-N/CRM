"""One database identity per organization and source submission."""
from sqlalchemy.exc import IntegrityError
from app.models import Lead


def insert_lead_once(db, lead):
    """Return (lead, inserted), recovering only a source-identity conflict.

    A savepoint keeps other imported leads intact if another request wins the
    insert race. The unique database index is the final arbiter, not this read.
    """
    lead.fb_lead_id = str(lead.fb_lead_id).strip()
    if not lead.fb_lead_id:
        raise ValueError('A source submission ID is required')
    identity = (Lead.org_id == lead.org_id, Lead.fb_lead_id == lead.fb_lead_id)
    existing = db.query(Lead).filter(*identity).first()
    if existing:
        return existing, False
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
