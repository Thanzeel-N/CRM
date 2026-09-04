from typing import Optional, List
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Organization, User
from app.services.auth import get_current_user

router = APIRouter(prefix="/org", tags=["org"])


class OrgSettingsOut(BaseModel):
    id: int
    org_name: str = ""
    name: str
    slug: str
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
    name: Optional[str] = None
    whatsapp_phone_number_id: Optional[str] = None
    whatsapp_access_token: Optional[str] = None


@router.get("/settings", response_model=OrgSettingsOut)
def get_org_settings(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    org = db.query(Organization).filter(Organization.id == current_user.org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


@router.patch("/settings", response_model=OrgSettingsOut)
def update_org_settings(
    payload: OrgSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    org = db.query(Organization).filter(Organization.id == current_user.org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")

    if payload.name: org.name = payload.name
    if payload.whatsapp_phone_number_id is not None: org.whatsapp_phone_number_id = payload.whatsapp_phone_number_id
    if payload.whatsapp_access_token is not None: org.whatsapp_access_token = payload.whatsapp_access_token

    db.commit()
    db.refresh(org)
    return org


@router.get("/list", response_model=List[OrgSettingsOut])
def list_organizations(db: Session = Depends(get_db)):
    """Public helper for tenant switcher demo."""
    return db.query(Organization).all()
