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

@router.post("/connect")
async def connect_google_sheet(
    spreadsheet_url: str = Body(...),
    sheet_name: str = Body(default="Sheet1"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    spreadsheet_id = extract_spreadsheet_id(spreadsheet_url)
    if not spreadsheet_id:
        raise HTTPException(status_code=400, detail="Invalid Google Sheets URL")

    # Check if existing connection
    conn = db.query(GoogleSheetConnection).filter(
        GoogleSheetConnection.org_id == current_user.org_id
    ).first()

    if conn:
        conn.spreadsheet_url = spreadsheet_url
        conn.spreadsheet_id = spreadsheet_id
        conn.sheet_name = sheet_name
        conn.status = "active"
        conn.user_id = current_user.id
    else:
        conn = GoogleSheetConnection(
            org_id=current_user.org_id,
            user_id=current_user.id,
            spreadsheet_url=spreadsheet_url,
            spreadsheet_id=spreadsheet_id,
            sheet_name=sheet_name,
            status="active"
        )
        db.add(conn)
        
    db.commit()
    return {"status": "success", "message": "Google Sheet connected"}

@router.get("/status")
async def get_connection_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    conn = db.query(GoogleSheetConnection).filter(
        GoogleSheetConnection.org_id == current_user.org_id
    ).first()
    
    if not conn or conn.status != "active":
        return {"connected": False}
        
    return {
        "connected": True,
        "spreadsheet_url": conn.spreadsheet_url,
        "sheet_name": conn.sheet_name
    }

@router.post("/disconnect")
async def disconnect_google_sheet(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    conn = db.query(GoogleSheetConnection).filter(
        GoogleSheetConnection.org_id == current_user.org_id
    ).first()
    
    if conn:
        db.delete(conn)
        db.commit()
        
    return {"status": "disconnected"}
