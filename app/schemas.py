from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

from app.models import LeadStatus


class LeadOut(BaseModel):
    id: int
    org_id: int
    campaign_id: Optional[int] = None
    fb_lead_id: str
    name: Optional[str]
    email: Optional[str]
    phone: Optional[str]
    campaign_name: Optional[str]
    form_name: Optional[str]
    status: LeadStatus
    notes: Optional[str]
    raw_data: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class LeadCreate(BaseModel):
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    campaign_name: Optional[str] = "Manual Entry"
    form_name: Optional[str] = "Direct CRM Input"
    notes: Optional[str] = None
    status: Optional[LeadStatus] = LeadStatus.new


class LeadSimulateRequest(BaseModel):
    name: str
    email: str
    phone: str
    campaign_name: str
    form_name: str
    platform: Optional[str] = "Meta Lead Ads"


class LeadStatusUpdate(BaseModel):
    status: LeadStatus
    notes: Optional[str] = None


class WhatsAppSendRequest(BaseModel):
    lead_id: int
    message: str


class WhatsAppMessageOut(BaseModel):
    id: int
    lead_id: int
    direction: str
    message_text: str
    wa_message_id: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class LeadStats(BaseModel):
    total_leads: int
    new_leads: int
    contacted_leads: int
    qualified_leads: int
    converted_leads: int
    lost_leads: int
    conversion_rate: float
    new_today: int
    campaign_counts: Dict[str, int]

