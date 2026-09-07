import io
import time
import random
from datetime import datetime, date
from typing import Optional, List

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import Lead, LeadStatus, LeadStatusHistory, WhatsAppMessage, User, Campaign, UserRole
from app.schemas import (
    LeadOut, LeadCreate, LeadStatusUpdate, WhatsAppSendRequest,
    WhatsAppMessageOut, LeadStats, LeadSimulateRequest
)
from app.services.whatsapp import send_whatsapp_message
from app.services.auth import get_current_user

router = APIRouter(prefix="/leads", tags=["leads"])


def _base_lead_query(db: Session, current_user: User):
    """
    Returns a scoped query:
    - Admin: all leads in their org
    - Agent: only leads from campaigns assigned to them
    """
    q = db.query(Lead).filter(Lead.org_id == current_user.org_id)

    if current_user.role == UserRole.agent:
        # Get IDs of campaigns assigned to this agent
        assigned_campaign_ids = [
            c.id for c in db.query(Campaign).filter(
                Campaign.assigned_user_id == current_user.id,
                Campaign.org_id == current_user.org_id
            ).all()
        ]
        if not assigned_campaign_ids:
            # Agent has no assigned campaigns — return empty
            q = q.filter(Lead.id == -1)
        else:
            q = q.filter(Lead.campaign_id.in_(assigned_campaign_ids))

    return q


@router.get("/forms")
def list_lead_forms(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Return a list of unique form details/names associated with this org's leads and connections."""
    from app.models import MetaPageConnection
    
    # 1. Gather form_id -> form_name map from Meta connections
    form_map = {}
    conns = db.query(MetaPageConnection).filter(
        MetaPageConnection.org_id == current_user.org_id
    ).all()
    for c in conns:
        if c.connected_forms and isinstance(c.connected_forms, list):
            for item in c.connected_forms:
                if isinstance(item, dict) and item.get("id") and item.get("name"):
                    form_map[str(item["id"])] = item["name"]

    # 2. Backfill existing leads where form_name is raw form ID
    if form_map:
        numeric_leads = db.query(Lead).filter(
            Lead.org_id == current_user.org_id,
            Lead.form_name.in_(list(form_map.keys()))
        ).all()
        if numeric_leads:
            for l in numeric_leads:
                if l.form_name in form_map:
                    l.form_name = form_map[l.form_name]
            db.commit()

    # 3. Query distinct form names
    forms_db = db.query(Lead.form_name).filter(
        Lead.org_id == current_user.org_id,
        Lead.form_name != None
    ).distinct().all()
    
    result = []
    seen = set()
    for f in forms_db:
        val = f[0]
        if val and val not in seen:
            seen.add(val)
            result.append({"id": val, "name": val})

    # Also add forms from active page connections if not already in leads
    for fid, fname in form_map.items():
        if fname not in seen:
            seen.add(fname)
            result.append({"id": fid, "name": fname})

    return result

@router.get("", response_model=List[LeadOut])
def list_leads(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    campaign_id: Optional[int] = Query(None),
    form_name: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None, description="ISO date string YYYY-MM-DD"),
    date_to: Optional[str] = Query(None, description="ISO date string YYYY-MM-DD"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    response: Response = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = _base_lead_query(db, current_user)

    if status:
        query = query.filter(Lead.status == status)
    if campaign_id:
        query = query.filter(Lead.campaign_id == campaign_id)
    if form_name:
        query = query.filter(
            (Lead.form_name == form_name) |
            (Lead.form_name.ilike(f"%{form_name}%"))
        )
    if search:
        p = f"%{search}%"
        query = query.filter(
            (Lead.name.ilike(p)) |
            (Lead.email.ilike(p)) |
            (Lead.phone.ilike(p)) |
            (Lead.campaign_name.ilike(p)) |
            (Lead.form_name.ilike(p))
        )
    if date_from:
        try:
            d_from = datetime.strptime(date_from, "%Y-%m-%d")
            query = query.filter(Lead.created_at >= d_from)
        except ValueError:
            pass
    if date_to:
        try:
            d_to = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
            query = query.filter(Lead.created_at <= d_to)
        except ValueError:
            pass

    total = query.count()
    leads = query.order_by(Lead.created_at.desc()).offset(offset).limit(limit).all()

    if response is not None:
        response.headers["X-Total-Count"] = str(total)
        response.headers["Access-Control-Expose-Headers"] = "X-Total-Count"

    return leads


@router.get("/stats", response_model=LeadStats)
def get_lead_stats(
    campaign_id: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    base = _base_lead_query(db, current_user)
    if campaign_id:
        base = base.filter(Lead.campaign_id == campaign_id)

    total = base.count()
    new_count = base.filter(Lead.status == LeadStatus.new).count()
    contacted_count = base.filter(Lead.status == LeadStatus.contacted).count()
    qualified_count = base.filter(Lead.status == LeadStatus.qualified).count()
    converted_count = base.filter(Lead.status == LeadStatus.converted).count()
    lost_count = base.filter(Lead.status == LeadStatus.lost).count()

    conversion_rate = round((converted_count / total * 100), 1) if total > 0 else 0.0

    today = date.today()
    new_today = _base_lead_query(db, current_user).filter(
        func.date(Lead.created_at) == today
    ).count()

    campaign_rows = _base_lead_query(db, current_user).with_entities(
        Lead.campaign_name, func.count(Lead.id)
    ).group_by(Lead.campaign_name).all()
    campaign_counts = {c_name or "Unassigned": count for c_name, count in campaign_rows}

    return LeadStats(
        total_leads=total,
        new_leads=new_count,
        contacted_leads=contacted_count,
        qualified_leads=qualified_count,
        converted_leads=converted_count,
        lost_leads=lost_count,
        conversion_rate=conversion_rate,
        new_today=new_today,
        campaign_counts=campaign_counts,
    )


@router.post("", response_model=LeadOut)
def create_lead(
    payload: LeadCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    fb_id = f"manual_{int(time.time()*1000)}_{random.randint(100, 999)}"
    lead = Lead(
        org_id=current_user.org_id,
        fb_lead_id=fb_id,
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        campaign_name=payload.campaign_name,
        form_name=payload.form_name,
        notes=payload.notes or "",
        status=payload.status or LeadStatus.new,
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


@router.post("/simulate", response_model=LeadOut)
def simulate_meta_lead(
    payload: LeadSimulateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    fb_id = f"meta_sim_{int(time.time()*1000)}_{random.randint(1000, 9999)}"
    mock_raw_data = {
        "created_time": datetime.utcnow().isoformat(),
        "id": fb_id,
        "ad_id": f"ad_{random.randint(10000, 99999)}",
        "form_id": payload.form_name,
        "field_data": [
            {"name": "full_name", "values": [payload.name]},
            {"name": "email", "values": [payload.email]},
            {"name": "phone_number", "values": [payload.phone]}
        ]
    }

    # Try to find matching campaign
    campaign_id = None
    if payload.campaign_name:
        campaign = db.query(Campaign).filter(
            Campaign.org_id == current_user.org_id,
            Campaign.name == payload.campaign_name
        ).first()
        if campaign:
            campaign_id = campaign.id

    lead = Lead(
        org_id=current_user.org_id,
        fb_lead_id=fb_id,
        campaign_id=campaign_id,
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        campaign_name=payload.campaign_name,
        form_name=payload.form_name,
        raw_data=mock_raw_data,
        status=LeadStatus.new,
        notes="Simulated Meta Lead Ad form submission",
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


@router.get("/export/excel")
def export_leads_excel(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    leads = _base_lead_query(db, current_user).order_by(Lead.created_at.desc()).all()
    rows = [{
        "ID": l.id,
        "FB Lead ID": l.fb_lead_id,
        "Name": l.name,
        "Email": l.email,
        "Phone": l.phone,
        "Campaign": l.campaign_name,
        "Status": l.status,
        "Notes": l.notes,
        "Created At": l.created_at,
    } for l in leads]

    df = pd.DataFrame(rows)
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, sheet_name="Leads")
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=leads_export.xlsx"},
    )


@router.get("/{lead_id}", response_model=LeadOut)
def get_lead(
    lead_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    lead = _base_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return lead


@router.patch("/{lead_id}/status", response_model=LeadOut)
def update_lead_status(
    lead_id: int,
    update: LeadStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    lead = _base_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    db.add(LeadStatusHistory(
        lead_id=lead.id,
        old_status=lead.status,
        new_status=update.status,
    ))
    lead.status = update.status
    if update.notes is not None:
        lead.notes = update.notes

    db.commit()
    db.refresh(lead)
    return lead


@router.delete("/{lead_id}")
def delete_lead(
    lead_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    lead = _base_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    db.delete(lead)
    db.commit()
    return {"status": "deleted", "id": lead_id}


@router.get("/{lead_id}/messages", response_model=List[WhatsAppMessageOut])
def get_lead_messages(
    lead_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    lead = _base_lead_query(db, current_user).filter(Lead.id == lead_id).first()
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")
    return db.query(WhatsAppMessage).filter(
        WhatsAppMessage.lead_id == lead_id
    ).order_by(WhatsAppMessage.created_at.asc()).all()


@router.post("/whatsapp/send")
async def whatsapp_send(
    payload: WhatsAppSendRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    lead = _base_lead_query(db, current_user).filter(Lead.id == payload.lead_id).first()
    if not lead or not lead.phone:
        raise HTTPException(status_code=404, detail="Lead or phone number not found")

    try:
        result = await send_whatsapp_message(lead.phone, payload.message)
        wa_msg_id = result.get("messages", [{}])[0].get("id") if isinstance(result, dict) else f"wa_sim_{int(time.time())}"
    except Exception:
        result = {"status": "simulated"}
        wa_msg_id = f"wa_sim_{int(time.time())}"

    msg_record = WhatsAppMessage(
        lead_id=lead.id,
        direction="outbound",
        message_text=payload.message,
        wa_message_id=wa_msg_id,
    )
    db.add(msg_record)

    if lead.status == LeadStatus.new:
        lead.status = LeadStatus.contacted

    db.commit()
    db.refresh(msg_record)

    return {"status": "sent", "message": WhatsAppMessageOut.model_validate(msg_record)}
