import re
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
import gspread
from google.oauth2.service_account import Credentials

from app.database import get_db
from app.models import User, GoogleSheetConnection
from app.services.auth import get_current_user

router = APIRouter(prefix="/integrations/google-sheets", tags=["google_sheets"])

# For this SaaS, we would ideally have a service account JSON file.
# For local development or if no file exists, we'll gracefully handle it.
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

def extract_spreadsheet_id(url: str) -> str:
    # Example: https://docs.google.com/spreadsheets/d/1BxiMVs0Xra5nZtWbKQnDgtY2iI6M/edit
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', url)
    if match:
        return match.group(1)
    return ""

from typing import Optional, List
from pydantic import BaseModel, Field

class GoogleSheetConnectRequest(BaseModel):
    spreadsheet_url: str
    sheet_name: str = Field(default="Sheet1")
    campaign_id: Optional[int] = None

class GoogleSheetConnectionOut(BaseModel):
    id: int
    spreadsheet_url: str
    sheet_name: str
    campaign_id: Optional[int] = None
    campaign_name: str = "Org-wide (All Campaigns)"
    status: str

    class Config:
        from_attributes = True

@router.post("/connect")
async def connect_google_sheet(
    payload: GoogleSheetConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    spreadsheet_id = extract_spreadsheet_id(payload.spreadsheet_url)
    if not spreadsheet_id:
        raise HTTPException(status_code=400, detail="Invalid Google Sheets URL")

    # Validate campaign_id if provided
    if payload.campaign_id:
        from app.models import Campaign
        camp = db.query(Campaign).filter(
            Campaign.id == payload.campaign_id,
            Campaign.org_id == current_user.org_id
        ).first()
        if not camp:
            raise HTTPException(status_code=400, detail="Campaign not found in your organization")

    # Check if exact connection (url + sheet_name + campaign_id) already exists
    conn = db.query(GoogleSheetConnection).filter(
        GoogleSheetConnection.org_id == current_user.org_id,
        GoogleSheetConnection.spreadsheet_url == payload.spreadsheet_url,
        GoogleSheetConnection.sheet_name == payload.sheet_name,
        GoogleSheetConnection.campaign_id == payload.campaign_id
    ).first()

    if conn:
        conn.status = "active"
        conn.spreadsheet_id = spreadsheet_id
        conn.user_id = current_user.id
    else:
        conn = GoogleSheetConnection(
            org_id=current_user.org_id,
            user_id=current_user.id,
            campaign_id=payload.campaign_id,
            spreadsheet_url=payload.spreadsheet_url,
            spreadsheet_id=spreadsheet_id,
            sheet_name=payload.sheet_name or "Sheet1",
            status="active"
        )
        db.add(conn)
        
    db.commit()
    db.refresh(conn)

    # Backfill existing leads that match this connection's campaign
    from app.models import Lead
    from app.routers.webhooks import sync_lead_to_google_sheets

    query = db.query(Lead).filter(Lead.org_id == current_user.org_id)
    if payload.campaign_id:
        query = query.filter(Lead.campaign_id == payload.campaign_id)
    existing_leads = query.order_by(Lead.id).all()

    backfilled = 0
    for lead in existing_leads:
        sync_lead_to_google_sheets(db, lead, commit=False)
        backfilled += 1
    db.commit()

    return {"status": "success", "message": "Google Sheet connected successfully", "id": conn.id, "backfilled_leads": backfilled}

@router.get("/connections", response_model=List[GoogleSheetConnectionOut])
async def list_google_sheet_connections(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    conns = db.query(GoogleSheetConnection).filter(
        GoogleSheetConnection.org_id == current_user.org_id,
        GoogleSheetConnection.status == "active"
    ).order_by(GoogleSheetConnection.created_at.desc()).all()

    result = []
    for c in conns:
        c_name = c.campaign.name if c.campaign else "Org-wide (All Campaigns)"
        result.append(GoogleSheetConnectionOut(
            id=c.id,
            spreadsheet_url=c.spreadsheet_url,
            sheet_name=c.sheet_name or "Sheet1",
            campaign_id=c.campaign_id,
            campaign_name=c_name,
            status=c.status
        ))
    return result

@router.get("/status")
async def get_connection_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    conns = db.query(GoogleSheetConnection).filter(
        GoogleSheetConnection.org_id == current_user.org_id,
        GoogleSheetConnection.status == "active"
    ).all()
    
    if not conns:
        return {"connected": False, "count": 0}
        
    first = conns[0]
    return {
        "connected": True,
        "count": len(conns),
        "spreadsheet_url": first.spreadsheet_url,
        "sheet_name": first.sheet_name
    }

@router.delete("/connections/{conn_id}")
async def delete_google_sheet_connection(
    conn_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    conn = db.query(GoogleSheetConnection).filter(
        GoogleSheetConnection.id == conn_id,
        GoogleSheetConnection.org_id == current_user.org_id
    ).first()
    
    if not conn:
        raise HTTPException(status_code=404, detail="Google Sheet connection not found")
        
    db.delete(conn)
    db.commit()
    return {"status": "success", "message": "Google Sheet connection deleted"}

@router.post("/disconnect")
async def disconnect_google_sheet(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    conns = db.query(GoogleSheetConnection).filter(
        GoogleSheetConnection.org_id == current_user.org_id
    ).all()
    
    for conn in conns:
        db.delete(conn)
    db.commit()
        
    return {"status": "disconnected"}
