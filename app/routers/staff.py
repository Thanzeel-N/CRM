from typing import List, Optional
from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, UserRole, Campaign
from app.services.auth import get_current_user, hash_password

router = APIRouter(prefix="/staff", tags=["staff"])


# ─── Schemas ───────────────────────────────────────────────────────────────────

class InviteStaffRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: str = "agent"


class StaffOut(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    assigned_campaigns: List[dict] = []

    class Config:
        from_attributes = True


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _require_admin(current_user: User):
    if current_user.role != UserRole.admin:
        raise HTTPException(status_code=403, detail="Admin access required")


def _staff_out(user: User, db: Session) -> StaffOut:
    campaigns = db.query(Campaign).filter(
        Campaign.assigned_user_id == user.id
    ).all()
    return StaffOut(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role.value,
        is_active=user.is_active,
        assigned_campaigns=[{"id": c.id, "name": c.name} for c in campaigns],
    )


# ─── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[StaffOut])
def list_staff(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all users (staff) in the current organization."""
    _require_admin(current_user)
    users = db.query(User).filter(User.org_id == current_user.org_id).order_by(User.created_at).all()
    return [_staff_out(u, db) for u in users]


@router.post("/invite", response_model=StaffOut)
def invite_staff(
    payload: InviteStaffRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new agent user in the current organization."""
    _require_admin(current_user)

    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Validate role
    try:
        role = UserRole(payload.role)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid role, must be 'admin' or 'agent'")

    user = User(
        org_id=current_user.org_id,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        name=payload.name,
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _staff_out(user, db)


@router.patch("/{user_id}/deactivate")
def deactivate_staff(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Deactivate a staff member (soft delete)."""
    _require_admin(current_user)
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

    user = db.query(User).filter(
        User.id == user_id,
        User.org_id == current_user.org_id
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.is_active = False
    db.commit()
    return {"status": "deactivated", "id": user_id}


@router.patch("/{user_id}/activate")
def activate_staff(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    user = db.query(User).filter(
        User.id == user_id,
        User.org_id == current_user.org_id
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_active = True
    db.commit()
    return {"status": "activated", "id": user_id}
