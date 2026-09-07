from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Campaign, User, UserRole
from app.services.auth import get_current_user

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


# ─── Schemas ───────────────────────────────────────────────────────────────────

class CampaignCreate(BaseModel):
    name: str
    description: Optional[str] = None
    meta_form_id: Optional[str] = None
    meta_ad_account_id: Optional[str] = None
    assigned_user_id: Optional[int] = None


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    meta_form_id: Optional[str] = None
    meta_ad_account_id: Optional[str] = None
    assigned_user_id: Optional[int] = None
    is_active: Optional[bool] = None


class SheetConnectionOut(BaseModel):
    id: int
    spreadsheet_url: str
    sheet_name: str
    status: str

    class Config:
        from_attributes = True


class CampaignOut(BaseModel):
    id: int
    org_id: int
    name: str
    description: Optional[str] = None
    meta_form_id: Optional[str] = None
    meta_ad_account_id: Optional[str] = None
    assigned_user_id: Optional[int] = None
    assigned_user_name: Optional[str] = None
    is_active: bool
    lead_count: int = 0
    google_sheets: List[SheetConnectionOut] = []

    class Config:
        from_attributes = True


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _require_admin(current_user: User):
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin access required")


def _campaign_out(c: Campaign, db: Session) -> CampaignOut:
    from app.models import Lead
    count = db.query(Lead).filter(
        Lead.org_id == c.org_id,
        (Lead.campaign_id == c.id) | (Lead.campaign_name == c.name)
    ).count()

    sheets = [
        SheetConnectionOut(
            id=s.id,
            spreadsheet_url=s.spreadsheet_url,
            sheet_name=s.sheet_name or "Sheet1",
            status=s.status
        )
        for s in (c.google_sheets or [])
        if s.status == "active"
    ]

    return CampaignOut(
        id=c.id,
        org_id=c.org_id,
        name=c.name,
        description=c.description,
        meta_form_id=c.meta_form_id,
        meta_ad_account_id=c.meta_ad_account_id,
        assigned_user_id=c.assigned_user_id,
        assigned_user_name=c.assigned_user.name if c.assigned_user else None,
        is_active=c.is_active,
        lead_count=count,
        google_sheets=sheets
    )


# ─── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[CampaignOut])
def list_campaigns(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Admin: sees all campaigns in the org.
    Agent: sees only campaigns assigned to them.
    Auto-discovers and registers Meta campaigns present in Lead table.
    """
    from app.models import Lead

    # 1. Auto-discover distinct campaign names from Lead table
    distinct_campaigns = db.query(Lead.campaign_name).filter(
        Lead.org_id == current_user.org_id,
        Lead.campaign_name != None,
        Lead.campaign_name != ""
    ).distinct().all()

    existing_names = set(
        c[0] for c in db.query(Campaign.name).filter(Campaign.org_id == current_user.org_id).all()
    )

    created_any = False
    for row in distinct_campaigns:
        camp_name = row[0]
        if camp_name and camp_name not in existing_names:
            new_c = Campaign(
                org_id=current_user.org_id,
                name=camp_name,
                description="Auto-created from Meta Lead Ads",
                is_active=True
            )
            db.add(new_c)
            existing_names.add(camp_name)
            created_any = True

    if created_any:
        db.commit()

    # 2. Query campaigns for current user
    q = db.query(Campaign).filter(Campaign.org_id == current_user.org_id)
    if current_user.role == UserRole.agent:
        q = q.filter(Campaign.assigned_user_id == current_user.id)
    campaigns = q.order_by(Campaign.created_at.desc()).all()
    return [_campaign_out(c, db) for c in campaigns]


@router.post("", response_model=CampaignOut)
def create_campaign(
    payload: CampaignCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)

    # Validate assigned user belongs to the same org
    if payload.assigned_user_id:
        agent = db.query(User).filter(
            User.id == payload.assigned_user_id,
            User.org_id == current_user.org_id
        ).first()
        if not agent:
            raise HTTPException(status_code=400, detail="Assigned user not found in your organization")

    campaign = Campaign(
        org_id=current_user.org_id,
        name=payload.name,
        description=payload.description,
        meta_form_id=payload.meta_form_id,
        meta_ad_account_id=payload.meta_ad_account_id,
        assigned_user_id=payload.assigned_user_id,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return _campaign_out(campaign, db)


@router.patch("/{campaign_id}", response_model=CampaignOut)
def update_campaign(
    campaign_id: int,
    payload: CampaignUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.org_id == current_user.org_id
    ).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")

    if payload.assigned_user_id is not None:
        agent = db.query(User).filter(
            User.id == payload.assigned_user_id,
            User.org_id == current_user.org_id
        ).first()
        if not agent:
            raise HTTPException(status_code=400, detail="Assigned user not found in your organization")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(campaign, field, value)

    db.commit()
    db.refresh(campaign)
    return _campaign_out(campaign, db)


@router.delete("/{campaign_id}")
def delete_campaign(
    campaign_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    campaign = db.query(Campaign).filter(
        Campaign.id == campaign_id,
        Campaign.org_id == current_user.org_id
    ).first()
    if not campaign:
        raise HTTPException(status_code=404, detail="Campaign not found")
    db.delete(campaign)
    db.commit()
    return {"status": "deleted", "id": campaign_id}
