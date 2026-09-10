import hashlib
import hmac
import json
import logging

import dateutil.parser

import gspread
from fastapi import APIRouter, Request, Response, Depends, HTTPException, BackgroundTasks
from google.oauth2.service_account import Credentials
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import Lead, WhatsAppMessage, MetaPageConnection, GoogleSheetConnection
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


# Standard column order used for every Google Sheet connection.
# Custom Meta form question names are added after these and before "CRM Marker".
SHEET_STANDARD_HEADERS = ["Date", "Name", "Email", "Phone", "Campaign", "Form"]


def _authorized_client():
    creds_file = settings.google_sheets_credentials_file
    if not creds_file:
        raise RuntimeError("Google Sheets credentials are not configured")
    creds = Credentials.from_service_account_file(
        creds_file,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    client = gspread.authorize(creds)
    client.set_timeout(30)
    return client


def _collect_custom_fields(leads: list) -> list:
    """Return unique Meta form-question/field names found across leads' raw_data."""
    custom = []
    std_lower = {h.lower() for h in SHEET_STANDARD_HEADERS}
    for lead in leads:
        raw = lead.raw_data or {}
        field_data = raw.get("field_data") if isinstance(raw, dict) else None
        if not isinstance(field_data, list):
            continue
        for field in field_data:
            name = (field.get("name") or "").strip()
            if name and name.lower() not in std_lower and name not in custom:
                custom.append(name)
    return custom


def build_sheet_headers(leads: list) -> list:
    """Column headers for a sheet: standard columns + custom form fields + marker."""
    return SHEET_STANDARD_HEADERS + _collect_custom_fields(leads) + ["CRM Marker"]


def _headers_for_lead(lead: Lead) -> list:
    return build_sheet_headers([lead])


def _read_sheet_headers(worksheet) -> list | None:
    row = worksheet.row_values(1)
    if row and "CRM Marker" in row:
        return row
    return None


def _lead_header_value(lead: Lead, header: str) -> str:
    """Map a header name to the lead's value for that column."""
    key = (header or "").strip().lower()
    if key == "date":
        return lead.created_at.strftime("%Y-%m-%d %H:%M:%S") if lead.created_at else ""
    if key == "name":
        return lead.name or ""
    if key == "email":
        return lead.email or ""
    if key == "phone":
        return lead.phone or ""
    if key == "campaign":
        return lead.campaign_name or ""
    if key == "form":
        return lead.form_name or ""
    raw = lead.raw_data or {}
    field_data = raw.get("field_data") if isinstance(raw, dict) else None
    if isinstance(field_data, list):
        for field in field_data:
            if (field.get("name") or "").strip().lower() == key:
                values = field.get("values") or []
                if values:
                    return str(values[0])
    return ""


def populate_google_sheet(sheet_id: str, sheet_name: str, headers: list, leads: list) -> int:
    """Clear the worksheet, write the header row, then write every lead aligned to it."""
    client = _authorized_client()
    worksheet = client.open_by_key(sheet_id).worksheet(sheet_name)
    worksheet.clear()
    rows = [headers] + [[_lead_header_value(lead, h) for h in headers] for lead in leads]
    if rows:
        worksheet.update("A1", rows, value_input_option="USER_ENTERED")
    return max(0, len(rows) - 1)


def _sync_to_google_sheet(sheet_id: str, sheet_name: str, lead: Lead) -> None:
    """Append a new lead row to the connected Google Sheet, aligned to its headers.

    Requires GOOGLE_SHEETS_CREDENTIALS_FILE to point to a valid service-account
    JSON key file downloaded from the Google Cloud Console.
    """
    if not settings.google_sheets_credentials_file:
        logger.info(
            "Google Sheets sync skipped — GOOGLE_SHEETS_CREDENTIALS_FILE not configured. "
            "Lead: %s (sheet_id=%s)", lead.name, sheet_id
        )
        raise RuntimeError("Google Sheets credentials are not configured")

    try:
        client = _authorized_client()
        worksheet = client.open_by_key(sheet_id).worksheet(sheet_name)
        headers = _read_sheet_headers(worksheet)
        if headers is None:
            headers = _headers_for_lead(lead)
            worksheet.clear()
            worksheet.append_row(headers)
        marker_col = headers.index("CRM Marker") + 1
        marker = f'crm:{lead.org_id}:{lead.id}'
        if marker_col and marker in worksheet.col_values(marker_col):
            return
        row = [_lead_header_value(lead, h) for h in headers]
        worksheet.append_row(row)
        logger.info("Synced lead '%s' to Google Sheet %s/%s", lead.name, sheet_id, sheet_name)
    except Exception:
        raise RuntimeError("Google Sheets sync failed; check credentials, sheet sharing and worksheet name") from None


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

    from app.services.integration_queue import enqueue
    for entry in body.get('entry', []):
        page_id = str(entry.get('id', ''))
        connections = db.query(MetaPageConnection).filter(
            MetaPageConnection.page_id == page_id, MetaPageConnection.status == 'active'
        ).all()
        if len({c.org_id for c in connections}) != 1:
            logger.warning('Ignoring unknown or ambiguous Meta page %s', page_id)
            continue
        for change in entry.get('changes', []):
            lead_id = change.get('value', {}).get('leadgen_id')
            if lead_id:
                enqueue(db, connections[0].org_id, 'meta', f'meta:{connections[0].org_id}:{lead_id}', {'entry': [{'id': page_id, 'changes': [change]}]})
    db.commit()
    return {'status': 'queued'}


async def process_meta_body(body, db):
    for entry in body.get("entry", []):
        page_id = str(entry.get("id", ""))

        # Lookup Client Organization matching Facebook Page ID
        conn = db.query(MetaPageConnection).filter(
            MetaPageConnection.page_id == page_id,
            MetaPageConnection.status == "active",
        ).first()

        if not conn:
            raise RuntimeError('Facebook page disconnected; reconnect before retrying')
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
                raise RuntimeError("Meta lead fetch failed; check page connection and retry") from None
            if not details:
                raise RuntimeError("Meta returned empty lead details")

            fields = parse_field_data(details.get("field_data", []))
            
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

            from app.models import Campaign
            campaign = db.query(Campaign).filter(Campaign.org_id == org_id, Campaign.meta_form_id == form_id_str).first()
            if not campaign and details.get('campaign_name'):
                campaign = db.query(Campaign).filter(Campaign.org_id == org_id, Campaign.name == details['campaign_name']).first()
                if not campaign:
                    campaign = Campaign(org_id=org_id, name=details['campaign_name'], meta_form_id=form_id_str)
                    db.add(campaign)
                    db.flush()
            lead = Lead(
                campaign_id=campaign.id if campaign else None,
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
            from app.services.lead_ingestion import insert_lead_once
            lead, inserted = insert_lead_once(db, lead)
            if not inserted:
                continue
            db.refresh(lead)
            logger.info("New lead saved: id=%s name='%s' org_id=%s", lead.id, lead.name, org_id)

            # Trigger Google Sheets sync for all matching campaign connections
            sync_lead_to_google_sheets(db, lead)

    return {"status": "ok"}


def sync_lead_to_google_sheets(db: Session, lead: Lead, background_tasks: BackgroundTasks = None, *, commit=True):
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
            from app.services.integration_queue import enqueue
            enqueue(db, lead.org_id, 'sheets', f'sheets:{gs_conn.id}:{lead.id}', {'connection_id': gs_conn.id, 'lead_id': lead.id})
    if commit:
        db.commit()


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
