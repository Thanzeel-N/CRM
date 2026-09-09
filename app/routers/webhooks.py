import hashlib
import hmac
import json
import logging

import dateutil.parser
from sqlalchemy import or_, and_

import gspread
from fastapi import APIRouter, Request, Response, Depends, HTTPException, BackgroundTasks
from google.oauth2.service_account import Credentials
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Lead, WhatsAppMessage, Organization, MetaPageConnection, GoogleSheetConnection
from app.services.meta import fetch_lead_details, parse_field_data

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ---------- HELPERS ----------

def _verify_meta_signature(body: bytes, signature_header: str | None) -> bool:
    """Validate X-Hub-Signature-256 HMAC sent by Meta on every POST."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        settings.meta_app_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def _sync_to_google_sheet(sheet_id: str, sheet_name: str, lead: Lead) -> None:
    """Append a new lead row to the connected Google Sheet.

    Requires GOOGLE_SHEETS_CREDENTIALS_FILE to point to a valid service-account
    JSON key file downloaded from the Google Cloud Console.
    """
    creds_file = settings.google_sheets_credentials_file
    if not creds_file:
        logger.info(
            "Google Sheets sync skipped — GOOGLE_SHEETS_CREDENTIALS_FILE not configured. "
            "Lead: %s (sheet_id=%s)", lead.name, sheet_id
        )
        return

    try:
        creds = Credentials.from_service_account_file(
            creds_file,
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        client = gspread.authorize(creds)
        sheet = client.open_by_key(sheet_id).worksheet(sheet_name)
        row = [
            lead.created_at.strftime("%Y-%m-%d %H:%M:%S") if lead.created_at else "",
            lead.name or "",
            lead.email or "",
            lead.phone or "",
            lead.campaign_name or "",
            lead.form_name or "",
        ]
        sheet.append_row(row)
        logger.info("Synced lead '%s' to Google Sheet %s/%s", lead.name, sheet_id, sheet_name)
    except Exception:
        logger.exception("Google Sheets sync failed for lead '%s' to Google Sheet %s", lead.name, sheet_id)


# ---------- META LEAD ADS ----------

@router.get("/meta")
async def verify_meta_webhook(request: Request):
    """Meta calls this once when you register the webhook URL to confirm ownership.
    Echoes back the challenge only if the verify token matches exactly.
    """
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == settings.meta_webhook_verify_token:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/meta")
async def receive_meta_lead(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Meta sends a lightweight notification here containing only the leadgen_id.
    We validate the HMAC signature, then fetch full lead details via the Graph API
    and route to the matching client organization by Page ID.
    """
    raw_body = await request.body()

    # Validate HMAC signature in production
    if settings.app_env == "production":
        sig = request.headers.get("X-Hub-Signature-256")
        if not _verify_meta_signature(raw_body, sig):
            logger.warning("Meta webhook: invalid signature — rejecting request")
            raise HTTPException(status_code=403, detail="Invalid webhook signature")

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    for entry in body.get("entry", []):
        page_id = str(entry.get("id", ""))

        # Lookup Client Organization matching Facebook Page ID
        conn = db.query(MetaPageConnection).filter(
            MetaPageConnection.page_id == page_id,
            MetaPageConnection.status == "active",
        ).first()

        if not conn:
            # Fallback to first organization (useful for local testing / single-tenant)
            org = db.query(Organization).first()
            if not org:
                logger.warning("Meta webhook: no matching page connection and no organization found for page_id=%s", page_id)
                continue
            org_id = org.id
            access_token = settings.meta_app_secret  # fallback — replace with real token in prod
            connected_forms: list = []
        else:
            org_id = conn.org_id
            access_token = conn.access_token
            connected_forms = conn.connected_forms or []

        for change in entry.get("changes", []):
            value = change.get("value", {})
            leadgen_id = value.get("leadgen_id")
            if not leadgen_id:
                continue

            # Skip if already stored (idempotency guard)
            existing = db.query(Lead).filter(
                Lead.fb_lead_id == leadgen_id,
                Lead.org_id == org_id,
            ).first()
            if existing:
                logger.debug("Duplicate lead skipped: fb_lead_id=%s", leadgen_id)
                continue
                
            try:
                details = await fetch_lead_details(leadgen_id, access_token=access_token)
            except Exception:
                logger.exception("Meta webhook: failed to fetch lead details for leadgen_id=%s", leadgen_id)
                continue

            if not details:
                logger.warning("Meta webhook: empty details for leadgen_id=%s", leadgen_id)
                continue

            fields = parse_field_data(details.get("field_data", []))
            
            # Check for duplicates by phone/email and campaign
            phone = fields.get("phone_number")
            email = fields.get("email")
            campaign_name = details.get("campaign_name")
            
            duplicate_conds = []
            if phone:
                duplicate_conds.append(and_(Lead.phone != None, Lead.phone != "", Lead.phone == phone))
            if email:
                duplicate_conds.append(and_(Lead.email != None, Lead.email != "", Lead.email == email))
                
            if duplicate_conds:
                existing_duplicate = db.query(Lead).filter(
                    Lead.org_id == org_id,
                    Lead.campaign_name == campaign_name,
                    or_(*duplicate_conds)
                ).first()
                if existing_duplicate:
                    logger.debug("Duplicate lead by phone/email skipped for fb_lead_id=%s", leadgen_id)
                    continue

            form_id = details.get("form_id")

            # Check if form is in connected forms (matching by string or dict id)
            form_id_str = str(form_id) if form_id else ""
            if conn and connected_forms:
                connected_ids = [str(f.get("id") if isinstance(f, dict) else f) for f in connected_forms]
                if connected_ids and form_id_str not in connected_ids:
                    logger.debug("Lead form %s not in connected forms for org %s — skipping", form_id, org_id)
                    continue

            # Resolve human-readable form name
            resolved_form_name = form_id_str
            if conn and connected_forms:
                for f in connected_forms:
                    if isinstance(f, dict) and str(f.get("id")) == form_id_str:
                        resolved_form_name = f.get("name") or form_id_str
                        break

            # Parse created_at from raw details
            created_at_val = None
            if "created_time" in details:
                try:
                    created_at_val = dateutil.parser.parse(details["created_time"])
                except Exception:
                    logger.debug("Failed to parse created_time for leadgen_id=%s", leadgen_id)

            lead = Lead(
                org_id=org_id,
                fb_lead_id=leadgen_id,
                name=fields.get("full_name") or fields.get("name"),
                email=fields.get("email"),
                phone=fields.get("phone_number"),
                campaign_name=details.get("campaign_name"),
                form_name=resolved_form_name,
                raw_data=details,
                created_at=created_at_val
            )
            db.add(lead)
            db.commit()
            db.refresh(lead)
            logger.info("New lead saved: id=%s name='%s' org_id=%s", lead.id, lead.name, org_id)

            # Trigger Google Sheets sync for all matching campaign connections
            sync_lead_to_google_sheets(db, lead, background_tasks)

    return {"status": "ok"}


def sync_lead_to_google_sheets(db: Session, lead: Lead, background_tasks: BackgroundTasks = None):
    """Find all matching Google Sheets for this lead's campaign / org and append rows."""
    conns = db.query(GoogleSheetConnection).filter(
        GoogleSheetConnection.org_id == lead.org_id,
        GoogleSheetConnection.status == "active",
    ).all()

    for gs_conn in conns:
        matches = False
        if gs_conn.campaign_id is None:
            matches = True
        elif lead.campaign_id and gs_conn.campaign_id == lead.campaign_id:
            matches = True
        elif lead.campaign_name and gs_conn.campaign and gs_conn.campaign.name.strip().lower() == lead.campaign_name.strip().lower():
            matches = True

        if matches:
            if background_tasks:
                background_tasks.add_task(
                    _sync_to_google_sheet,
                    gs_conn.spreadsheet_id,
                    gs_conn.sheet_name or "Sheet1",
                    lead,
                )
            else:
                try:
                    _sync_to_google_sheet(gs_conn.spreadsheet_id, gs_conn.sheet_name or "Sheet1", lead)
                except Exception as e:
                    logger.warning("Google Sheet sync failed for lead %s: %s", lead.id, e)


# ---------- WHATSAPP ----------

@router.get("/whatsapp")
async def verify_whatsapp_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == settings.whatsapp_webhook_verify_token:
        return Response(content=challenge, media_type="text/plain")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/whatsapp")
async def receive_whatsapp_message(request: Request, db: Session = Depends(get_db)):
    """Logs inbound WhatsApp replies against the matching lead, matched by phone number."""
    body = await request.json()

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            messages = change.get("value", {}).get("messages", [])
            for msg in messages:
                from_phone = msg.get("from", "")
                text = msg.get("text", {}).get("body", "")

                if not from_phone:
                    continue

                # Match on last 9 digits to handle country-code variations
                lead = db.query(Lead).filter(
                    Lead.phone.contains(from_phone[-9:])
                ).first()
                if lead:
                    db.add(WhatsAppMessage(
                        lead_id=lead.id,
                        direction="inbound",
                        message_text=text,
                        wa_message_id=msg.get("id"),
                    ))
                    db.commit()
                    logger.info("Inbound WhatsApp logged for lead id=%s", lead.id)

    return {"status": "ok"}
