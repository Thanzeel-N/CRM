from typing import Optional, List
from pydantic import BaseModel, field_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Organization, User, UserRole
from app.services.auth import get_current_user

router = APIRouter(prefix="/org", tags=["org"])


class OrgSettingsOut(BaseModel):
    id: int
    org_name: str = ""
    name: str
    slug: str
    timezone: str = 'Asia/Kolkata'
    whatsapp_phone_number_id: Optional[str] = None
    whatsapp_access_token: Optional[str] = None

    @classmethod
    def from_orm_with_alias(cls, org):
        return cls(
            id=org.id,
            org_name=org.name,
            name=org.name,
            slug=org.slug,
            whatsapp_phone_number_id=org.whatsapp_phone_number_id,
            whatsapp_access_token=org.whatsapp_access_token,
        )

    class Config:
        from_attributes = True


class OrgSettingsUpdate(BaseModel):
    timezone: Optional[str] = None
    name: Optional[str] = None
    whatsapp_phone_number_id: Optional[str] = None
    whatsapp_access_token: Optional[str] = None

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        if value is not None:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError('Select a valid region timezone')
        return value


@router.get('/timezones')
def timezones(current_user: User = Depends(get_current_user)):
    return sorted(available_timezones())


@router.get("/settings", response_model=OrgSettingsOut)
def get_org_settings(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    org = db.query(Organization).filter(Organization.id == current_user.org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    result = OrgSettingsOut.model_validate(org)
    if current_user.role != UserRole.admin:
        result.whatsapp_access_token = None
    return result


@router.patch("/settings", response_model=OrgSettingsOut)
def update_org_settings(
    payload: OrgSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != UserRole.admin:
        raise HTTPException(403, 'Admin access required')
    org = db.query(Organization).filter(Organization.id == current_user.org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    if payload.name: org.name = payload.name
    if payload.timezone is not None and payload.timezone != org.timezone:
        org.timezone = payload.timezone
        from app.models import Lead
        from app.routers.webhooks import sync_lead_to_google_sheets
        for lead in db.query(Lead).filter(Lead.org_id == org.id):
            sync_lead_to_google_sheets(db, lead, commit=False, refresh=True)
    if payload.whatsapp_phone_number_id is not None: org.whatsapp_phone_number_id = payload.whatsapp_phone_number_id
    if payload.whatsapp_access_token is not None: org.whatsapp_access_token = payload.whatsapp_access_token

    db.commit()
    db.refresh(org)
    return org


@router.get("/list", response_model=List[OrgSettingsOut])
def list_organizations(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Returns organizations visible to the authenticated user."""
    return db.query(Organization).filter(Organization.id == current_user.org_id).all()
