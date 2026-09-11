import io
import time
import logging
import random
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo
from app.services.timezones import day_bounds, local_datetime, DEFAULT_TIMEZONE
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
        q = q.filter((Lead.owner_id == current_user.id) | (
            Lead.owner_id.is_(None) & Lead.campaign_id.in_(assigned_campaign_ids)
        ))

    return q


@router.get("/forms")
async def list_lead_forms(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    campaign_id: Optional[str] = Query(None, description="Restrict to forms belonging to this campaign (id or name)"),
):
    """Return a list of unique form details/names associated with this org's leads and connections."""
    if current_user.role == UserRole.agent:
        rows = _base_lead_query(db, current_user).with_entities(Lead.form_name).distinct().all()
        return [{"id": name, "name": name} for (name,) in rows if name]
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
                    try:
                        data = resp.json()
                    except Exception:
                        data = {}

                    if not resp.is_success or "error" in data:
                        err = data.get("error", {}) if isinstance(data, dict) else {}
                        err_code = err.get("code")
                        err_subcode = err.get("error_subcode")
                        err_type = err.get("type")
                        err_msg = err.get("message") or resp.text[:200]
                        fbtrace_id = err.get("fbtrace_id")

                        logger.error(
                            "Meta leadgen_forms request failed for page_id=%s [HTTP status=%s]: code=%s, error_subcode=%s, error_type=%s, message=%s, fbtrace_id=%s",
                            c.page_id,
                            resp.status_code,
                            err_code,
                            err_subcode,
                            err_type,
                            err_msg,
                            fbtrace_id
                        )
                    elif "data" in data:
                        for f in data["data"]:
                            fid = str(f["id"])
                            fname = f.get("name") or f"Form #{fid}"
                            form_map[fid] = fname
                            if not any(isinstance(p, dict) and p.get("id") == fid for p in page_forms_meta):
                                page_forms_meta.append({"id": fid, "name": fname})
                        c.connected_forms = page_forms_meta
                        db.commit()
                except Exception as ex:
                    logger.warning(f"Error checking leadgen_forms for page {c.page_id}: {ex}")

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
        numeric_keys = [k for k in form_map.keys() if k.isdigit()]
        if numeric_keys:
            leads_to_update = db.query(Lead).filter(
                Lead.org_id == current_user.org_id,
                Lead.form_name.in_(numeric_keys)
            ).all()
            updated_forms = False
            for l in leads_to_update:
                if l.form_name in form_map:
                    l.form_name = form_map[l.form_name]
                    updated_forms = True
            if updated_forms:
                db.commit()

    # 3. Query distinct form names with optional campaign filter
    q_forms = db.query(Lead.form_name).filter(
        Lead.org_id == current_user.org_id,
        Lead.form_name != None
    )
    if campaign_id:
        from app.models import Campaign
        camp = db.query(Campaign).filter(
            Campaign.org_id == current_user.org_id,
            (Campaign.name == campaign_id) | (Campaign.meta_campaign_id == campaign_id)
        ).first()
        if camp:
            q_forms = q_forms.filter((Lead.campaign_id == camp.id) | (Lead.campaign_name == camp.name))
        else:
            q_forms = q_forms.filter(Lead.campaign_name == campaign_id)

    forms_db = q_forms.distinct().all()

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

    # Filter by campaign: show only the forms feeding the selected campaign.
    if campaign_id:
        from app.models import Campaign
        camp = None
        if campaign_id.isdigit():
            camp = db.query(Campaign).filter(
                Campaign.org_id == current_user.org_id,
                Campaign.id == int(campaign_id)
            ).first()
        if camp is None:
            camp = db.query(Campaign).filter(
                Campaign.org_id == current_user.org_id,
                Campaign.name == campaign_id
            ).first()
        if not camp:
            return []
        form_ids = [str(f) for f in (camp.meta_form_ids or [])]
        return [{"id": fid, "name": form_map.get(fid) or fid} for fid in form_ids]

    # Hide forms that belong to deactivated campaigns from the default list.
    from app.models import Campaign
    inactive_ids, active_ids = set(), set()
    for c in db.query(Campaign).filter(Campaign.org_id == current_user.org_id).all():
        for fid in (c.meta_form_ids or []):
            key = str(fid)
            (active_ids if c.is_active else inactive_ids).add(key)
            # Form ids may be stored by name after backfill; track both.
            name_key = form_map.get(key) or key
            (active_ids if c.is_active else inactive_ids).add(name_key)
    if inactive_ids:
        result = [
            f for f in result
            if not (str(f["id"]) in inactive_ids and str(f["id"]) not in active_ids)
        ]

    return result

@router.get("/campaigns")
def list_lead_campaigns(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role == UserRole.agent:
        rows = _base_lead_query(db, current_user).with_entities(Lead.campaign_name).distinct().all()
        return [{"id": name, "name": name} for (name,) in rows if name]
    campaigns_db = db.query(Lead.campaign_name).filter(
        Lead.org_id == current_user.org_id,
        Lead.campaign_name != None,
        Lead.campaign_name != ""
    ).distinct().all()

    # Only surface active campaigns (or lead names not tied to any archived row).
    org_campaigns = db.query(Campaign).filter(Campaign.org_id == current_user.org_id).all()
    inactive_names = {c.name for c in org_campaigns if not c.is_active}

    result = []
    seen = set()
    for c in campaigns_db:
        val = c[0]
        if val and val not in seen:
            seen.add(val)
            if val in inactive_names:
                continue  # deactivated campaign -> hide from the active list
            result.append({"id": val, "name": val})

    for mc in org_campaigns:
        if mc.is_active and mc.name not in seen:
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
        try:
            start, _ = day_bounds(datetime.strptime(date_from, "%Y-%m-%d").date(), DEFAULT_TIMEZONE)
            query = query.filter(Lead.created_at >= start)
        except ValueError:
            raise HTTPException(400, 'Invalid start date; use YYYY-MM-DD')
    if date_to:
        try:
            _, end = day_bounds(datetime.strptime(date_to, "%Y-%m-%d").date(), DEFAULT_TIMEZONE)
            query = query.filter(Lead.created_at < end)
        except ValueError:
            raise HTTPException(400, 'Invalid end date; use YYYY-MM-DD')

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

    region = DEFAULT_TIMEZONE
    start, end = day_bounds(datetime.now(ZoneInfo(region)).date(), region)
    new_today = base.filter(
        Lead.created_at >= start,
        Lead.created_at < end,
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
        owner_id=current_user.id if current_user.role == UserRole.agent else None,
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        campaign_name=payload.campaign_name,
        form_name=payload.form_name,
        notes=payload.notes or "",
        status=payload.status or LeadStatus.new,
    )
    from app.services.lead_ingestion import insert_lead_once
    from app.routers.webhooks import sync_lead_to_google_sheets
    lead, _ = insert_lead_once(db, lead)
    sync_lead_to_google_sheets(db, lead, commit=False)
    db.commit()
    db.refresh(lead)
    return lead


@router.post("/simulate", response_model=LeadOut)
def simulate_meta_lead(
    payload: LeadSimulateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != UserRole.admin:
        raise HTTPException(403, "Admin access required")
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
        owner_id=current_user.id if current_user.role == UserRole.agent else None,
        name=payload.name,
        email=payload.email,
        phone=payload.phone,
        campaign_name=payload.campaign_name,
        form_name=payload.form_name,
        raw_data=mock_raw_data,
        status=LeadStatus.new,
        notes="Simulated Meta Lead Ad form submission",
    )
    from app.services.lead_ingestion import insert_lead_once
    from app.routers.webhooks import sync_lead_to_google_sheets
    lead, _ = insert_lead_once(db, lead)
    sync_lead_to_google_sheets(db, lead, commit=False)
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
        "Created At": local_datetime(l.created_at, DEFAULT_TIMEZONE).isoformat(sep=' ', timespec='seconds'),
        "Timezone": DEFAULT_TIMEZONE,
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

    from app.routers.workflow import activity
    if lead.status != update.status:
        activity(db, lead, current_user, 'status', f'{lead.status.value} → {update.status.value}')
        if update.status != LeadStatus.new and lead.first_contacted_at is None:
            lead.first_contacted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if update.notes is not None and update.notes != lead.notes:
        activity(db, lead, current_user, 'note', update.notes or 'Notes cleared')
    db.add(LeadStatusHistory(
        lead_id=lead.id,
        old_status=lead.status,
        new_status=update.status,
    ))
    lead.status = update.status
    if update.notes is not None:
        lead.notes = update.notes

    from app.routers.webhooks import sync_lead_to_google_sheets
    sync_lead_to_google_sheets(db, lead, commit=False, refresh=True)
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

    from app.routers.webhooks import sync_lead_to_google_sheets
    sync_lead_to_google_sheets(db, lead, commit=False, refresh=True)
    db.commit()
    db.refresh(msg_record)

    return {"status": "sent", "message": WhatsAppMessageOut.model_validate(msg_record)}
