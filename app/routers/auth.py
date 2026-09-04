from typing import Optional
from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Organization, UserRole
from app.services.auth import (
    hash_password, verify_password, create_access_token, get_current_user
)

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterRequest(BaseModel):
    org_name: str
    user_name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    org_id: int
    org_name: str
    org_slug: str
    email: str
    name: str
    role: str

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


@router.post("/register", response_model=TokenResponse)
def register_organization(payload: RegisterRequest, db: Session = Depends(get_db)):
    # Check if email exists
    existing_user = db.query(User).filter(User.email == payload.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Generate org slug
    slug = payload.org_name.lower().replace(" ", "-").replace("/", "")
    existing_org = db.query(Organization).filter(Organization.slug == slug).first()
    if existing_org:
        slug = f"{slug}-{db.query(Organization).count() + 1}"

    # Create Organization
    org = Organization(
        name=payload.org_name,
        slug=slug,
    )
    db.add(org)
    db.commit()
    db.refresh(org)

    # Create Admin User
    user = User(
        org_id=org.id,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        name=payload.user_name,
        role=UserRole.admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"user_id": user.id, "org_id": org.id})

    user_out = UserOut(
        id=user.id,
        org_id=org.id,
        org_name=org.name,
        org_slug=org.slug,
        email=user.email,
        name=user.name,
        role=user.role.value,
    )

    return TokenResponse(access_token=token, user=user_out)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Incorrect email or password")

    org = db.query(Organization).filter(Organization.id == user.org_id).first()

    token = create_access_token({"user_id": user.id, "org_id": org.id})

    user_out = UserOut(
        id=user.id,
        org_id=org.id,
        org_name=org.name if org else "Default Org",
        org_slug=org.slug if org else "default",
        email=user.email,
        name=user.name,
        role=user.role.value,
    )

    return TokenResponse(access_token=token, user=user_out)


@router.get("/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    org = db.query(Organization).filter(Organization.id == current_user.org_id).first()
    return UserOut(
        id=current_user.id,
        org_id=current_user.org_id,
        org_name=org.name if org else "Default Org",
        org_slug=org.slug if org else "default",
        email=current_user.email,
        name=current_user.name,
        role=current_user.role.value,
    )
