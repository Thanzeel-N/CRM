import enum

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, Enum, JSON, Boolean, Index
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
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, index=True, nullable=False)
    whatsapp_phone_number_id = Column(String(255), nullable=True)
    whatsapp_access_token = Column(String(512), nullable=True)
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
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), default=UserRole.admin, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="users")
    assigned_campaigns = relationship("Campaign", back_populates="assigned_user", foreign_keys="Campaign.assigned_user_id")
    meta_page_connections = relationship("MetaPageConnection", back_populates="user", cascade="all, delete-orphan")
    google_sheet_connections = relationship("GoogleSheetConnection", back_populates="user", cascade="all, delete-orphan")


class MetaPageConnection(Base):
    __tablename__ = "meta_page_connections"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False) # Who connected it
    page_id = Column(String(255), index=True, nullable=False)
    page_name = Column(String(255), nullable=False)
    access_token = Column(String(512), nullable=False)
    connected_forms = Column(JSON, default=list) # List of connected form IDs
    status = Column(String(50), default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="meta_pages")
    user = relationship("User", back_populates="meta_page_connections")


class GoogleSheetConnection(Base):
    __tablename__ = "google_sheet_connections"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False) # Who connected it
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=True, index=True) # Linked campaign
    spreadsheet_url = Column(String(512), nullable=False)
    spreadsheet_id = Column(String(255), nullable=False)
    sheet_name = Column(String(255), default="Sheet1")
    status = Column(String(50), default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="google_sheets")
    user = relationship("User", back_populates="google_sheet_connections")
    campaign = relationship("Campaign", back_populates="google_sheets")


class Campaign(Base):
    """A Meta Ad Campaign tracked in the CRM. Each campaign can be assigned to one staff agent."""
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)                    # e.g. "Summer Promo 2026"
    meta_form_id = Column(String(255), nullable=True)             # Meta Lead Form ID
    meta_ad_account_id = Column(String(255), nullable=True)       # Meta Ad Account ID
    meta_campaign_id = Column(String(255), nullable=True, index=True)  # Meta Ad Campaign ID
    meta_status = Column(String(50), nullable=True)                   # Live Meta status: ACTIVE, PAUSED, DELETED, ARCHIVED, etc.
    description = Column(Text, nullable=True)
    assigned_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # Assigned agent
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    organization = relationship("Organization", back_populates="campaigns")
    assigned_user = relationship("User", back_populates="assigned_campaigns", foreign_keys=[assigned_user_id])
    leads = relationship("Lead", back_populates="campaign", cascade="all, delete-orphan")
    google_sheets = relationship("GoogleSheetConnection", back_populates="campaign", cascade="all, delete-orphan")


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (Index('uq_leads_org_source', 'org_id', 'fb_lead_id', unique=True),)

    id = Column(Integer, primary_key=True, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=True, index=True)  # NEW
    fb_lead_id = Column(String(255), index=True, nullable=False)
    name = Column(String(255))
    email = Column(String(255), index=True)
    phone = Column(String(50), index=True)
    campaign_name = Column(String(255))   # Keep for backward compat + unlinked leads
    form_name = Column(String(255))
    raw_data = Column(JSON)
    status = Column(Enum(LeadStatus), default=LeadStatus.new, nullable=False)
    notes = Column(Text, default="")
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    follow_up_at = Column(DateTime(timezone=True), nullable=True, index=True)
    first_contacted_at = Column(DateTime(timezone=True), nullable=True)
    owner = relationship("User", foreign_keys=[owner_id])
    @property
    def owner_name(self):
        return self.owner.name if self.owner else None

    activities = relationship("LeadActivity", cascade="all, delete-orphan")
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
    old_status = Column(String(50))
    new_status = Column(String(50))
    changed_at = Column(DateTime(timezone=True), server_default=func.now())

    lead = relationship("Lead", back_populates="status_history")


class WhatsAppMessage(Base):
    __tablename__ = "whatsapp_messages"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    direction = Column(String(20))  # "outbound" or "inbound"
    message_text = Column(Text)
    wa_message_id = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    lead = relationship("Lead", back_populates="whatsapp_messages")


class LeadActivity(Base):
    __tablename__ = "lead_activities"
    id = Column(Integer, primary_key=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False, index=True)
    actor_name = Column(String(255), nullable=False)
    kind = Column(String(50), nullable=False)
    detail = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class LeadDuplicateArchive(Base):
    """Recovery snapshots for exact duplicate rows removed during migration."""
    __tablename__ = 'lead_duplicate_archive'
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, nullable=False, index=True)
    original_lead_id = Column(Integer, nullable=False)
    canonical_lead_id = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False)
    archived_at = Column(DateTime, server_default=func.now())


class IntegrationJob(Base):
    __tablename__ = "integration_jobs"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    job_key = Column(String(255), unique=True, nullable=False)
    kind = Column(String(30), nullable=False)
    payload = Column(JSON, nullable=False)
    status = Column(String(30), default="pending", nullable=False, index=True)
    attempts = Column(Integer, default=0, nullable=False)
    last_error = Column(Text, nullable=True)
    next_attempt_at = Column(DateTime, server_default=func.now(), nullable=False)
    created_at = Column(DateTime, server_default=func.now())
