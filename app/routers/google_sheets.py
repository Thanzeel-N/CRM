import logging
import re
from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
import gspread
from google.oauth2.service_account import Credentials

from app.database import get_db
from app.models import User, UserRole, GoogleSheetConnection, Lead, Campaign, MetaPageConnection
from app.services.auth import get_current_user

logger = logging.getLogger(__name__)

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
    form_id: Optional[str] = Field(default=None, max_length=255)

class GoogleSheetConnectionOut(BaseModel):
    id: int
    spreadsheet_url: str
    sheet_name: str
    campaign_id: Optional[int] = None
    form_id: Optional[str] = None
    campaign_name: str = "Org-wide (All Campaigns)"
    status: str

    class Config:
        from_attributes = True


def available_forms(db, org_id, campaign_id=None):
    query = db.query(Lead).filter(Lead.org_id == org_id)
    known_ids = set()
    if campaign_id is not None:
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id, Campaign.org_id == org_id).first()
        if not campaign:
            raise HTTPException(400, 'Campaign not found in your organization')
        query = query.filter(Lead.campaign_id == campaign_id)
        known_ids.update(str(fid) for fid in (campaign.meta_form_ids or []))
        if campaign.meta_form_id:
            known_ids.add(campaign.meta_form_id)
    forms = {}
    for lead in query.all():
        fid = str((lead.raw_data or {}).get('form_id') or '')
        if fid:
            forms[fid] = lead.form_name or fid
            known_ids.add(fid)
    for page in db.query(MetaPageConnection).filter(MetaPageConnection.org_id == org_id, MetaPageConnection.status == 'active'):
        for form in page.connected_forms or []:
            fid = str(form.get('id') if isinstance(form, dict) else form)
            if campaign_id is None or fid in known_ids:
                forms[fid] = (form.get('name') if isinstance(form, dict) else None) or forms.get(fid) or fid
    for fid in known_ids:
        forms.setdefault(fid, fid)
    return [{'id': fid, 'name': name} for fid, name in sorted(forms.items(), key=lambda item: (item[1], item[0]))]


@router.get('/forms')
def sheet_forms(campaign_id: Optional[int] = None, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current_user.role != UserRole.admin:
        raise HTTPException(403, 'Admin access required')
    return available_forms(db, current_user.org_id, campaign_id)


@router.get('/config')
def sheet_configuration(current_user: User = Depends(get_current_user)):
    if current_user.role != UserRole.admin:
        raise HTTPException(403, 'Admin access required')
    from app.config import settings
    from pathlib import Path
    import json
    email = None
    try:
        if settings.google_sheets_credentials_file:
            email = json.loads(Path(settings.google_sheets_credentials_file).read_text())['client_email']
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return {'configured': bool(email), 'service_account_email': email}

@router.post("/connect")
async def connect_google_sheet(
    payload: GoogleSheetConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != UserRole.admin:
        raise HTTPException(403, 'Admin access required')
    payload.sheet_name = payload.sheet_name.strip() or 'Sheet1'
    payload.form_id = (payload.form_id or '').strip() or None
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

    if payload.form_id and payload.form_id not in {f['id'] for f in available_forms(db, current_user.org_id, payload.campaign_id)}:
        raise HTTPException(400, 'Select a form belonging to the selected campaign or organization')

    # URLs with different gid/query parameters still identify the same destination.
    conn = db.query(GoogleSheetConnection).filter(
        GoogleSheetConnection.org_id == current_user.org_id,
        GoogleSheetConnection.spreadsheet_id == spreadsheet_id,
        GoogleSheetConnection.sheet_name == payload.sheet_name,
    ).first()

    if conn:
        if conn.campaign_id != payload.campaign_id or conn.form_id != payload.form_id:
            raise HTTPException(409, 'This tab already has a different campaign/form rule. Select a separate tab.')
        conn.status = "active"
        conn.spreadsheet_id = spreadsheet_id
        conn.user_id = current_user.id
    else:
        conn = GoogleSheetConnection(
            org_id=current_user.org_id,
            user_id=current_user.id,
            campaign_id=payload.campaign_id,
            form_id=payload.form_id,
            spreadsheet_url=payload.spreadsheet_url,
            spreadsheet_id=spreadsheet_id,
            sheet_name=payload.sheet_name or "Sheet1",
            status="active"
        )
        db.add(conn)
        
    db.commit()
    db.refresh(conn)

    # All backfills use the same durable, serialized queue as live updates.
    from app.routers.webhooks import sheet_matches_lead
    from app.services.integration_queue import enqueue_sheet
    query = db.query(Lead).filter(Lead.org_id == current_user.org_id)
    if payload.campaign_id:
        query = query.filter(Lead.campaign_id == payload.campaign_id)
    existing_leads = query.order_by(Lead.id).all()

    queued = 0
    for lead in existing_leads:
        if sheet_matches_lead(conn, lead):
            enqueue_sheet(db, conn, lead, refresh=True)
            queued += 1
    db.commit()

    return {
        "status": "success",
        "message": "Google Sheet connected; delivery is queued. Check Sync activity for errors.",
        "id": conn.id,
        "backfilled_leads": 0,
        "queued_leads": queued,
        "backfill_error": None,
    }

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
            form_id=c.form_id,
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
    if current_user.role != UserRole.admin:
        raise HTTPException(403, 'Admin access required')
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
    if current_user.role != UserRole.admin:
        raise HTTPException(403, 'Admin access required')
    conns = db.query(GoogleSheetConnection).filter(
        GoogleSheetConnection.org_id == current_user.org_id
    ).all()
    
    for conn in conns:
        db.delete(conn)
    db.commit()
        
    return {"status": "disconnected"}
