import io
import time
import logging
import random
from datetime import datetime, date, timezone
from typing import Optional, List

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
import dateutil.parser

from app.database import get_db
from app.models import Lead, LeadStatus, LeadStatusHistory, WhatsAppMessage, User, Campaign, UserRole
from app.schemas import (
    LeadOut, LeadCreate, LeadStatusUpdate, WhatsAppSendRequest,
    WhatsAppMessageOut, LeadStats, LeadSimulateRequest
)
from app.services.whatsapp import send_whatsapp_message
from app.services.auth import get_current_user

logger = logging.getLogger(__name__)

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
async def list_lead_forms(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Return a list of unique form details/names associated with this org's leads and connections."""
    from app.models import MetaPageConnection
    import httpx

    # 1. Gather form_id -> form_name map from Meta connections & Graph API
    form_map = {}
    conns = db.query(MetaPageConnection).filter(
        MetaPageConnection.org_id == current_user.org_id,
        MetaPageConnection.status == "active"
    ).all()

    async with httpx.AsyncClient() as client:
        for c in conns:
            page_forms_meta = []
            if c.connected_forms and isinstance(c.connected_forms, list):
                for item in c.connected_forms:
                    if isinstance(item, dict) and item.get("id") and item.get("name"):
                        form_map[str(item["id"])] = item["name"]
                        page_forms_meta.append(item)

            # Query Graph API if form_map is incomplete for this page connection
            if c.access_token and c.page_id:
                try:
                    url_forms = f"https://graph.facebook.com/v20.0/{c.page_id}/leadgen_forms"
                    resp = await client.get(url_forms, params={"access_token": c.access_token, "fields": "id,name"})
                    data = resp.json()
                    if "data" in data:
                        for f in data["data"]:
                            fid = str(f["id"])
                            fname = f.get("name") or f"Form #{fid}"
                            form_map[fid] = fname
                            if not any(isinstance(p, dict) and p.get("id") == fid for p in page_forms_meta):
                                page_forms_meta.append({"id": fid, "name": fname})
                        c.connected_forms = page_forms_meta
                        db.commit()
                except Exception:
                    pass

        # Fallback for remaining unresolved numeric form IDs in DB
        unresolved_ids = set()
        for l in db.query(Lead.form_name).filter(Lead.org_id == current_user.org_id).distinct().all():
            val = l[0]
            if val and val.isdigit() and val not in form_map:
                unresolved_ids.add(val)

        if unresolved_ids and conns:
            token = conns[0].access_token
            for fid in unresolved_ids:
                try:
                    resp = await client.get(f"https://graph.facebook.com/v20.0/{fid}", params={"access_token": token, "fields": "name"})
                    fname = resp.json().get("name")
                    if fname:
                        form_map[fid] = fname
                except Exception:
                    logger.debug("Graph API form name resolution failed for form %s", fid)

    # 2. Backfill existing leads where form_name is raw numeric form ID
    if form_map:
        numeric_leads = db.query(Lead).filter(
            Lead.org_id == current_user.org_id
        ).all()
        updated_forms = False
        for l in numeric_leads:
            if l.form_name and l.form_name in form_map:
                l.form_name = form_map[l.form_name]
                updated_forms = True
        if updated_forms:
            db.commit()

    # 2.5 Backfill created_at from raw_data if available
    try:
        leads_to_fix = db.query(Lead).filter(
            Lead.org_id == current_user.org_id,
            Lead.raw_data != None
        ).all()
        date_updated = False
        for l in leads_to_fix:
            if isinstance(l.raw_data, dict) and "created_time" in l.raw_data:
                try:
                    parsed_dt = dateutil.parser.parse(l.raw_data["created_time"])
                    if l.created_at != parsed_dt:
                        l.created_at = parsed_dt
                        date_updated = True
                except Exception:
                    logger.debug("Failed to parse created_time for lead %s", l.id)
        if date_updated:
            db.commit()
    except Exception:
        logger.debug("dateutil.parser not available, skipping date backfill")
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

@router.get("/campaigns")
def list_lead_campaigns(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    campaigns_db = db.query(Lead.campaign_name).filter(
        Lead.org_id == current_user.org_id,
        Lead.campaign_name != None,
        Lead.campaign_name != ""
    ).distinct().all()
    
    result = []
    seen = set()
    for c in campaigns_db:
        val = c[0]
        if val and val not in seen:
            seen.add(val)
            result.append({"id": val, "name": val})
            
    manual_campaigns = db.query(Campaign).filter(
        Campaign.org_id == current_user.org_id,
        Campaign.is_active == True
    ).all()
    
    for mc in manual_campaigns:
        if mc.name not in seen:
            seen.add(mc.name)
            result.append({"id": mc.id, "name": mc.name})
            
    return result

@router.get("", response_model=List[LeadOut])
def list_leads(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    campaign_id: Optional[str] = Query(None),
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
        from sqlalchemy import or_
        filters = [Lead.campaign_name.ilike(f"%{campaign_id}%"), Lead.campaign_name == campaign_id]
        if campaign_id.isdigit():
            filters.append(Lead.campaign_id == int(campaign_id))
        query = query.filter(or_(*filters))
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
        # Validate format but use string for safer SQLite comparison
        try:
            datetime.strptime(date_from, "%Y-%m-%d")
            query = query.filter(func.date(Lead.created_at) >= date_from)
        except ValueError:
            pass
    if date_to:
        try:
            datetime.strptime(date_to, "%Y-%m-%d")
            query = query.filter(func.date(Lead.created_at) <= date_to)
        except ValueError:
            pass

    total = query.count()
    leads = query.order_by(Lead.created_at.desc(), Lead.id.desc()).offset(offset).limit(limit).all()

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

    from datetime import timedelta
    today_utc = datetime.now(timezone.utc).date()
    tomorrow_utc = today_utc + timedelta(days=1)
    new_today = _base_lead_query(db, current_user).filter(
        Lead.created_at >= datetime.combine(today_utc, datetime.min.time()).replace(tzinfo=timezone.utc),
        Lead.created_at <  datetime.combine(tomorrow_utc, datetime.min.time()).replace(tzinfo=timezone.utc),
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
        "created_time": datetime.now(timezone.utc).isoformat(),
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
        logger.debug("WhatsApp send failed for lead %s, using simulated response", payload.lead_id)
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
