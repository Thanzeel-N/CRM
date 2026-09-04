import enum

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, Enum, JSON, Boolean
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class UserRole(str, enum.Enum):
    admin = "admin"
    agent = "agent"


class LeadStatus(str, enum.Enum):
    new = "new"
    contacted = "contacted"
    qualified = "qualified"
    converted = "converted"
    lost = "lost"


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, index=True, nullable=False)
    whatsapp_phone_number_id = Column(String, nullable=True)
    whatsapp_access_token = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    users = relationship("User", back_populates="organization", cascade="all, delete-orphan")
    leads = relationship("Lead", back_populates="organization", cascade="all, delete-orphan")
    campaigns = relationship("Campaign", back_populates="organization", cascade="all, delete-orphan")
    meta_pages = relationship("MetaPageConnection", back_populates="organization", cascade="all, delete-orphan")
    google_sheets = relationship("GoogleSheetConnection", back_populates="organization", cascade="all, delete-orphan")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    name = Column(String, nullable=False)
    role = Column(Enum(UserRole), default=UserRole.admin, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="users")
    assigned_campaigns = relationship("Campaign", back_populates="assigned_user", foreign_keys="Campaign.assigned_user_id")
    meta_page_connections = relationship("MetaPageConnection", back_populates="user", cascade="all, delete-orphan")


class MetaPageConnection(Base):
    __tablename__ = "meta_page_connections"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False) # Who connected it
    page_id = Column(String, index=True, nullable=False)
    page_name = Column(String, nullable=False)
    access_token = Column(String, nullable=False)
    connected_forms = Column(JSON, default=list) # List of connected form IDs
    status = Column(String, default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="meta_pages")
    user = relationship("User", back_populates="meta_page_connections")


class GoogleSheetConnection(Base):
    __tablename__ = "google_sheet_connections"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False) # Who connected it
    spreadsheet_url = Column(String, nullable=False)
    spreadsheet_id = Column(String, nullable=False)
    sheet_name = Column(String, default="Sheet1")
    status = Column(String, default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="google_sheets")
    user = relationship("User")


class Campaign(Base):
    """A Meta Ad Campaign tracked in the CRM. Each campaign can be assigned to one staff agent."""
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String, nullable=False)                    # e.g. "Summer Promo 2026"
    meta_form_id = Column(String, nullable=True)             # Meta Lead Form ID
    meta_ad_account_id = Column(String, nullable=True)       # Meta Ad Account ID
    description = Column(Text, nullable=True)
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # Assigned agent
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="campaigns")
    assigned_user = relationship("User", back_populates="assigned_campaigns", foreign_keys=[assigned_user_id])
    leads = relationship("Lead", back_populates="campaign", cascade="all, delete-orphan")


class Lead(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True, default=1)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=True, index=True)  # NEW
    fb_lead_id = Column(String, index=True, nullable=False)
    name = Column(String)
    email = Column(String, index=True)
    phone = Column(String, index=True)
    campaign_name = Column(String)   # Keep for backward compat + unlinked leads
    form_name = Column(String)
    raw_data = Column(JSON)
    status = Column(Enum(LeadStatus), default=LeadStatus.new, nullable=False)
    notes = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    organization = relationship("Organization", back_populates="leads")
    campaign = relationship("Campaign", back_populates="leads")
    status_history = relationship("LeadStatusHistory", back_populates="lead", cascade="all, delete-orphan")
    whatsapp_messages = relationship("WhatsAppMessage", back_populates="lead", cascade="all, delete-orphan")


class LeadStatusHistory(Base):
    __tablename__ = "lead_status_history"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    old_status = Column(String)
    new_status = Column(String)
    changed_at = Column(DateTime(timezone=True), server_default=func.now())

    lead = relationship("Lead", back_populates="status_history")


class WhatsAppMessage(Base):
    __tablename__ = "whatsapp_messages"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    direction = Column(String)  # "outbound" or "inbound"
    message_text = Column(Text)
    wa_message_id = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    lead = relationship("Lead", back_populates="whatsapp_messages")
