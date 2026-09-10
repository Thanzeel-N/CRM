from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Request, Body
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
import httpx
import jwt
import json
from datetime import datetime, timedelta, timezone
import dateutil.parser

import logging

from app.config import settings
from app.database import get_db
from app.models import User, MetaPageConnection, Lead
from app.services.auth import get_current_user, SECRET_KEY, ALGORITHM
from app.services.meta import parse_field_data, extract_field, PHONE_FIELD_NAMES, EMAIL_FIELD_NAMES, NAME_FIELD_NAMES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/facebook", tags=["facebook"])

FB_API_BASE = "https://graph.facebook.com/v20.0"


async def sync_campaign_statuses(db: Session, org_id: int, client: httpx.AsyncClient = None) -> dict:
    """Refresh/reconcile Campaign rows with Meta's live form & campaign status.

    Page tokens do NOT have ``ads_read`` permission so we cannot enumerate ad
    campaigns directly.  Instead we:
      1. Fetch every ``leadgen_form`` on every connected page (id, name, status).
      2. Read a sample of leads per form to learn which Meta campaign (by name
         & ID) each form belongs to.
      3. Aggregate form statuses per campaign: if ANY form is ACTIVE the
         campaign is ACTIVE; otherwise it mirrors the first form's status.
      4. Create any discovered campaign that isn't in the CRM yet (so running
         campaigns are never missing), and set ``is_active``/``meta_status`` to
         match the live state (so stopped campaigns stop showing as active).
    Campaigns we cannot map (e.g. forms without leads) are checked against the
    ad-campaign endpoint as a best effort when we have their ID.
    """
    from app.models import Campaign
    conns = db.query(MetaPageConnection).filter(
        MetaPageConnection.org_id == org_id,
        MetaPageConnection.status == "active",
    ).all()
    if not conns:
        return {"checked": 0, "updated": 0, "created": 0}

    _owned_client = client or httpx.AsyncClient(timeout=30)
    checked = 0
    updated = 0
    created = 0

    try:
        # campaign name -> {"meta_campaign_id": str, "form_status_by_form": {form_id: status}}
        campaign_info = {}

        for conn in conns:
            token = conn.access_token
            page_id = conn.page_id

            # Step 1: fetch ALL leadgen forms from this page
            url = f"{FB_API_BASE}/{page_id}/leadgen_forms"
            params = {"access_token": token, "fields": "id,name,status", "limit": 100}
            all_forms = []
            while url:
                try:
                    resp = await _owned_client.get(url, params=params)
                    data = resp.json()
                    if "error" in data:
                        logger.warning("leadgen_forms error for page %s: %s",
                                       page_id, data["error"].get("message"))
                        break
                    all_forms.extend(data.get("data", []))
                    url = data.get("paging", {}).get("next")
                    params = None
                except Exception as exc:
                    logger.warning("leadgen_forms fetch failed for page %s: %s", page_id, exc)
                    break

            # Update stored connected_forms with fresh status info
            if all_forms:
                updated_forms = [
                    {"id": str(f["id"]),
                     "name": f.get("name") or f"Form #{f['id']}",
                     "status": f.get("status")}
                    for f in all_forms
                ]
                if conn.connected_forms != updated_forms:
                    conn.connected_forms = updated_forms

            # Step 2: sample leads per form to learn the campaign(s) behind it
            for form in all_forms:
                form_id = str(form.get("id"))
                form_status = (form.get("status") or "").upper()

                lead_url = f"{FB_API_BASE}/{form_id}/leads"
                lead_params = {
                    "access_token": token,
                    "fields": "id,campaign_id,campaign_name",
                    "limit": 50,
                }
                try:
                    resp = await _owned_client.get(lead_url, params=lead_params)
                    lead_data = resp.json()
                    if "error" in lead_data or not lead_data.get("data"):
                        continue
                    for lead in lead_data["data"]:
                        camp_name = (lead.get("campaign_name") or "").strip()
                        if not camp_name:
                            continue
                        info = campaign_info.setdefault(
                            camp_name, {"meta_campaign_id": "", "form_status_by_form": {}}
                        )
                        camp_id = str(lead.get("campaign_id") or "")
                        if camp_id and not info["meta_campaign_id"]:
                            info["meta_campaign_id"] = camp_id
                        info["form_status_by_form"][form_id] = form_status
                except Exception:
                    continue

        existing = {c.name: c for c in db.query(Campaign).filter(Campaign.org_id == org_id).all()}

        # Step 3: create missing campaigns and update existing ones
        for camp_name, info in campaign_info.items():
            statuses = list(info["form_status_by_form"].values())
            has_active = "ACTIVE" in statuses
            overall = "ACTIVE" if has_active else (statuses[0] if statuses else None)
            new_active = (overall == "ACTIVE") if overall else True

            camp = existing.get(camp_name)
            if camp is None:
                camp = Campaign(
                    org_id=org_id,
                    name=camp_name,
                    meta_campaign_id=info["meta_campaign_id"] or None,
                    meta_status=overall,
                    is_active=new_active,
                    description="Auto-created from Meta Lead Ads",
                )
                db.add(camp)
                existing[camp_name] = camp
                created += 1
                continue

            checked += 1
            changed = False
            if info["meta_campaign_id"] and camp.meta_campaign_id != info["meta_campaign_id"]:
                camp.meta_campaign_id = info["meta_campaign_id"]
                changed = True
            if overall and camp.meta_status != overall:
                camp.meta_status = overall
                changed = True
            if camp.is_active != new_active:
                camp.is_active = new_active
                changed = True
            if changed:
                updated += 1

        # Step 4: best effort for known campaigns we could not map via forms
        if existing:
            sample_token = next((c.access_token for c in conns if c.access_token), None)
            for camp in existing.values():
                if camp.name in campaign_info or not camp.meta_campaign_id or not sample_token:
                    continue
                try:
                    resp = await _owned_client.get(
                        f"{FB_API_BASE}/{camp.meta_campaign_id}",
                        params={"access_token": sample_token, "fields": "status,effective_status"},
                    )
                    data = resp.json()
                    effective = (data.get("effective_status") or data.get("status") or "").upper()
                    if "error" in data or not effective:
                        continue
                    checked += 1
                    changed = False
                    if camp.meta_status != effective:
                        camp.meta_status = effective
                        changed = True
                    new_active = (effective == "ACTIVE")
                    if camp.is_active != new_active:
                        camp.is_active = new_active
                        changed = True
                    if changed:
                        updated += 1
                except Exception:
                    continue

    finally:
        if client is None:
            await _owned_client.aclose()

    if updated or created:
        db.commit()
    return {"checked": checked, "updated": updated, "created": created}

def create_fb_session_token(user_access_token: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=60)
    to_encode = {"exp": expire, "user_access_token": user_access_token}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_fb_session_token(token: str) -> str:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("user_access_token")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired FB session token")

@router.get("/auth-url")
def get_auth_url(request: Request):
    # Dynamically determine callback URL based on the request's origin.
    # This allows it to work seamlessly whether accessed via localhost or a WiFi IP (e.g. 192.168.x.x)
    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/integrations/facebook/callback"
    
    if settings.meta_app_id == "local_dev_meta_app_id":
        # Mock local dev flow - redirect straight to our callback with a dummy code
        url = f"{redirect_uri}?code=mock_oauth_code"
    else:
        config_id = getattr(settings, "meta_config_id", "")

        if not config_id:
            raise HTTPException(
                status_code=500,
                detail="META_CONFIG_ID is not configured"
            )
        scope = "pages_show_list,pages_read_engagement,pages_manage_metadata,leads_retrieval"
        url = (
            f"https://www.facebook.com/v20.0/dialog/oauth?"
            f"client_id={settings.meta_app_id}"
            f"&redirect_uri={redirect_uri}"
            f"&response_type=code"
            f"&scope={scope}"
            f"&config_id={config_id}"
        )
    return {"url": url, "redirect_uri": redirect_uri}

@router.get("/callback", response_class=HTMLResponse)
def oauth_callback(code: Optional[str] = None, error: Optional[str] = None, error_description: Optional[str] = None):
    """
    This endpoint is hit by Facebook. We render a script that passes the code
    back to our frontend SPA popup opener, then closes the window.
    """
    if error:
        safe_error = json.dumps(error_description or "Unknown error")
        return f"""
        <html><body>
        <script>
            window.opener.postMessage({{ type: 'FB_OAUTH_ERROR', error: {safe_error} }}, '*');
            window.close();
        </script>
        </body></html>
        """
    if code:
        safe_code = json.dumps(code)
        return f"""
        <html><body>
        <script>
            window.opener.postMessage({{ type: 'FB_OAUTH_SUCCESS', code: {safe_code} }}, '*');
            window.close();
        </script>
        </body></html>
        """
    return "Invalid callback"

@router.post("/exchange")
async def exchange_code(
    code: str = Body(...),
    redirect_uri: str = Body(...),
    current_user: User = Depends(get_current_user)
):
    """Exchanges the OAuth code for a user access token and returns a secure session token."""
    url = f"{FB_API_BASE}/oauth/access_token"
    params = {
        "client_id": settings.meta_app_id,
        "client_secret": settings.meta_app_secret,
        "redirect_uri": redirect_uri,
        "code": code
    }
    
    if settings.meta_app_id == "local_dev_meta_app_id":
        return {"fb_session_token": create_fb_session_token("mock_user_token")}
        
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        data = resp.json()
        if "error" in data:
            logger.error(f"OAuth exchange error: {data['error']}")
            raise HTTPException(status_code=400, detail=data["error"].get("message", "OAuth exchange failed"))
        
        user_access_token = data.get("access_token")
        if not user_access_token:
            raise HTTPException(status_code=400, detail="No access token returned")

        # Exchange short-lived token for long-lived user access token (~60 days validity)
        long_lived_url = f"{FB_API_BASE}/oauth/access_token"
        long_lived_params = {
            "grant_type": "fb_exchange_token",
            "client_id": settings.meta_app_id,
            "client_secret": settings.meta_app_secret,
            "fb_exchange_token": user_access_token
        }
        try:
            ll_resp = await client.get(long_lived_url, params=long_lived_params)
            ll_data = ll_resp.json()
            if "access_token" in ll_data:
                user_access_token = ll_data["access_token"]
                logger.info("Successfully exchanged short-lived token for long-lived user access token")
            else:
                logger.warning(f"Could not exchange for long-lived token: {ll_data.get('error', {}).get('message')}")
        except Exception as e:
            logger.warning(f"Failed long-lived token exchange attempt: {e}")
        
        # We encrypt the FB token in a JWT so the frontend can hold it safely for the next steps
        session_token = create_fb_session_token(user_access_token)
        return {"fb_session_token": session_token}

@router.get("/pages")
async def get_pages(
    fb_session_token: str,
    current_user: User = Depends(get_current_user)
):
    user_access_token = decode_fb_session_token(fb_session_token)
    
    if settings.meta_app_id == "local_dev_meta_app_id":
        return {"pages": [{"id": "page_1", "name": "ABC Pharmacy"}, {"id": "page_2", "name": "XYZ Pharmacy"}]}
        
    url = f"{FB_API_BASE}/me/accounts"
    params = {"access_token": user_access_token, "fields": "id,name,access_token,tasks,category"}
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        data = resp.json()
        if "error" in data:
            logger.error(f"Error fetching pages from /me/accounts: {data['error']}")
            raise HTTPException(status_code=400, detail=data["error"].get("message", "Failed to fetch pages"))
        
        pages_raw = data.get("data", [])
        logger.info(f"Fetched {len(pages_raw)} pages for user from /me/accounts")

        if not pages_raw:
            try:
                url_perms = f"{FB_API_BASE}/me/permissions"
                resp_perms = await client.get(url_perms, params={"access_token": user_access_token})
                perms_data = resp_perms.json()
                granted = [p["permission"] for p in perms_data.get("data", []) if p["status"] == "granted"]
                logger.warning(f"0 pages returned from /me/accounts. Granted permissions on token: {granted}")
            except Exception as ex:
                logger.warning(f"Could not inspect permissions: {ex}")

        pages = [{"id": p["id"], "name": p["name"]} for p in pages_raw]
        return {"pages": pages}

@router.get("/pages/{page_id}/forms")
async def get_page_forms(
    page_id: str,
    fb_session_token: str,
    current_user: User = Depends(get_current_user)
):
    user_access_token = decode_fb_session_token(fb_session_token)
    
    if settings.meta_app_id == "local_dev_meta_app_id":
        return {"forms": [{"id": "form_1", "name": "Medicine Campaign"}, {"id": "form_2", "name": "Home Delivery Campaign"}]}
    
    # First, get the page access token
    url_accounts = f"{FB_API_BASE}/me/accounts"
    params_acc = {"access_token": user_access_token, "fields": "id,access_token"}
    
    page_access_token = None
    async with httpx.AsyncClient() as client:
        resp = await client.get(url_accounts, params=params_acc)
        data = resp.json()
        for p in data.get("data", []):
            if p["id"] == page_id:
                page_access_token = p["access_token"]
                break
                
    if not page_access_token:
        raise HTTPException(status_code=403, detail="Could not retrieve Page Access Token")
        
    # Now get forms
    url_forms = f"{FB_API_BASE}/{page_id}/leadgen_forms"
    params_forms = {"access_token": page_access_token, "fields": "id,name,status"}
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(url_forms, params=params_forms)
        try:
            data = resp.json()
        except Exception:
            data = {}

        if not resp.is_success or "error" in data:
            err = data.get("error", {}) if isinstance(data, dict) else {}
            err_code = err.get("code")
            err_subcode = err.get("error_subcode")
            err_type = err.get("type")
            err_msg = err.get("message") or (resp.text[:200] if not resp.is_success else "Failed to fetch forms")
            fbtrace_id = err.get("fbtrace_id")

            logger.error(
                "Meta leadgen_forms request failed for page_id=%s [HTTP status=%s]: code=%s, error_subcode=%s, error_type=%s, message=%s, fbtrace_id=%s",
                page_id,
                resp.status_code,
                err_code,
                err_subcode,
                err_type,
                err_msg,
                fbtrace_id
            )
            raise HTTPException(status_code=400, detail=err_msg)

        forms = [{"id": f["id"], "name": f["name"], "status": f.get("status")} for f in data.get("data", [])]
        return {"forms": forms}

@router.post("/connect")
async def connect_page(
    fb_session_token: str = Body(...),
    page_id: str = Body(...),
    page_name: str = Body(...),
    forms: List[str] = Body(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    user_access_token = decode_fb_session_token(fb_session_token)
    
    sync_results = {
        "forms_synced": 0,
        "leads_imported": 0,
        "duplicates_skipped": 0,
        "failed_forms": 0
    }
    
    if settings.meta_app_id == "local_dev_meta_app_id":
        page_access_token = "mock_page_token"
    else:
        async with httpx.AsyncClient() as client:
            # 0. Verify permissions
            url_perms = f"{FB_API_BASE}/me/permissions"
            params_perms = {"access_token": user_access_token}
            resp_perms = await client.get(url_perms, params=params_perms)
            perms_data = resp_perms.json()
            
            if "error" in perms_data:
                raise HTTPException(status_code=400, detail="Failed to verify permissions")
                
            granted_scopes = [p["permission"] for p in perms_data.get("data", []) if p["status"] == "granted"]
            required_scopes = ["pages_show_list", "pages_read_engagement", "pages_manage_metadata", "leads_retrieval"]
            missing_scopes = [s for s in required_scopes if s not in granted_scopes]
            
            if missing_scopes:
                logger.error(f"Connect page verification failed. Missing required Facebook permissions: {missing_scopes}")
                raise HTTPException(
                    status_code=403, 
                    detail=f"Missing required Facebook permissions: {', '.join(missing_scopes)}"
                )

            # 1. Get Page Access Token
            url_accounts = f"{FB_API_BASE}/me/accounts"
            params_acc = {"access_token": user_access_token, "fields": "id,access_token"}
            
            page_access_token = None
            resp = await client.get(url_accounts, params=params_acc)
            data = resp.json()
            for p in data.get("data", []):
                if p["id"] == page_id:
                    page_access_token = p["access_token"]
                    break
                
            if not page_access_token:
                raise HTTPException(status_code=403, detail="Could not retrieve Page Access Token")
                    
            # 2. Subscribe webhook
            url_sub = f"{FB_API_BASE}/{page_id}/subscribed_apps"
            params_sub = {
                "access_token": page_access_token,
                "subscribed_fields": "leadgen"
            }
            resp_sub = await client.post(url_sub, params=params_sub)
            sub_data = resp_sub.json()
            
            if "error" in sub_data:
                err_msg = sub_data["error"].get("message", "Unknown error subscribing webhook")
                logger.error(f"Webhook subscription failed for page {page_id}: {err_msg}")
                raise HTTPException(status_code=400, detail=f"Webhook subscription failed: {err_msg}")
            
            forms_meta = []
            clean_form_ids = []
            form_map = {}
            for item in forms:
                if isinstance(item, dict):
                    fid = str(item.get("id"))
                    fname = item.get("name") or f"Form #{fid}"
                    fstatus = item.get("status")
                else:
                    fid = str(item)
                    fname = f"Form #{fid}"
                    fstatus = None
                clean_form_ids.append(fid)
                forms_meta.append({"id": fid, "name": fname, "status": fstatus})
                form_map[fid] = fname

            # 3. Historical Lead Sync
            seen_lead_ids = set()
            for form_id in clean_form_ids:
                url_leads = f"{FB_API_BASE}/{form_id}/leads"
                params_leads = {
                    "access_token": page_access_token,
                    "fields": "id,created_time,field_data,campaign_name,form_id"
                }
                
                try:
                    form_success = True
                    while url_leads:
                        resp_leads = await client.get(url_leads, params=params_leads)
                        leads_data = resp_leads.json()
                        
                        if "error" in leads_data:
                            logger.error(f"Error syncing leads for form {form_id}: {leads_data['error']}")
                            form_success = False
                            break
                            
                        # Process leads
                        for l in leads_data.get("data", []):
                            fb_lead_id = l.get("id")
                            if not fb_lead_id or fb_lead_id in seen_lead_ids:
                                sync_results["duplicates_skipped"] += 1
                                continue
                                
                            existing = db.query(Lead).filter(
                                Lead.fb_lead_id == fb_lead_id,
                                Lead.org_id == current_user.org_id
                            ).first()
                            
                            if existing:
                                seen_lead_ids.add(fb_lead_id)
                                sync_results["duplicates_skipped"] += 1
                                continue
                                
                            fields = parse_field_data(l.get("field_data", []))
                            
                            created_at_val = None
                            if "created_time" in l:
                                try:
                                    created_at_val = dateutil.parser.parse(l["created_time"])
                                except Exception:
                                    pass
                            
                            target_form_id = str(l.get("form_id") or form_id)
                            resolved_form_name = form_map.get(target_form_id, f"Form #{target_form_id}")

                            new_lead = Lead(
                                org_id=current_user.org_id,
                                fb_lead_id=fb_lead_id,
                                name=extract_field(fields, *NAME_FIELD_NAMES),
                                email=extract_field(fields, *EMAIL_FIELD_NAMES),
                                phone=extract_field(fields, *PHONE_FIELD_NAMES),
                                campaign_name=l.get("campaign_name"),
                                form_name=resolved_form_name,
                                raw_data=l,
                                created_at=created_at_val
                            )
                            from app.services.lead_ingestion import insert_lead_once
                            new_lead, inserted = insert_lead_once(db, new_lead)
                            if not inserted:
                                seen_lead_ids.add(fb_lead_id)
                                sync_results["duplicates_skipped"] += 1
                                continue
                            from app.routers.webhooks import sync_lead_to_google_sheets
                            sync_lead_to_google_sheets(db, new_lead, commit=False)
                            seen_lead_ids.add(fb_lead_id)
                            sync_results["leads_imported"] += 1
                            
                        # Pagination
                        paging = leads_data.get("paging", {})
                        url_leads = paging.get("next")
                        params_leads = None
                        
                    if form_success:
                        sync_results["forms_synced"] += 1
                    else:
                        sync_results["failed_forms"] += 1
                        
                except Exception as e:
                    logger.exception(f"Exception syncing form {form_id}: {e}")
                    sync_results["failed_forms"] += 1
            
    # 4. Save to DB
    conn = db.query(MetaPageConnection).filter(
        MetaPageConnection.org_id == current_user.org_id,
        MetaPageConnection.page_id == page_id
    ).first()
    
    if conn:
        conn.page_name = page_name
        conn.access_token = page_access_token
        conn.connected_forms = forms_meta if 'forms_meta' in locals() else forms
        conn.status = "active"
    else:
        conn = MetaPageConnection(
            org_id=current_user.org_id,
            user_id=current_user.id,
            page_id=page_id,
            page_name=page_name,
            access_token=page_access_token,
            connected_forms=forms_meta if 'forms_meta' in locals() else forms,
            status="active"
        )
        db.add(conn)
        
    db.commit()
    db.refresh(conn)

    # Reflect Meta's live campaign state (stopped -> archived, active -> active).
    status_results = {"checked": 0, "updated": 0}
    try:
        status_results = await sync_campaign_statuses(db, current_user.org_id)
    except Exception as exc:
        logger.warning("Campaign status refresh failed during page connect: %s", exc)

    return {
        "status": "success", 
        "id": conn.id,
        "sync_results": sync_results,
        "campaign_statuses": status_results
    }

@router.get("/connections")
def list_connections(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conns = db.query(MetaPageConnection).filter(
        MetaPageConnection.org_id == current_user.org_id,
        MetaPageConnection.status == "active"
    ).order_by(MetaPageConnection.created_at.desc()).all()
    
    unique_conns = {}
    for c in conns:
        if c.page_id not in unique_conns:
            unique_conns[c.page_id] = c
            
    return [
        {
            "id": c.id,
            "page_id": c.page_id,
            "page_name": c.page_name,
            "connected_forms": c.connected_forms,
            "status": c.status,
            "created_at": c.created_at
        } for c in unique_conns.values()
    ]

@router.post("/connections/{conn_id}/disconnect")
async def disconnect_page(
    conn_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    conn = db.query(MetaPageConnection).filter(
        MetaPageConnection.id == conn_id,
        MetaPageConnection.org_id == current_user.org_id
    ).first()
    
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
        
    if settings.meta_app_id != "local_dev_meta_app_id" and conn.access_token:
        url_sub = f"{FB_API_BASE}/{conn.page_id}/subscribed_apps"
        params = {"access_token": conn.access_token}
        async with httpx.AsyncClient() as client:
            await client.delete(url_sub, params=params)

    db.delete(conn)
    db.commit()
    
    return {"status": "disconnected"}


@router.post("/sync")
async def sync_facebook_leads(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Manually fetch and sync all historical leads from Meta Graph API for active page connections."""
    conns = db.query(MetaPageConnection).filter(
        MetaPageConnection.org_id == current_user.org_id,
        MetaPageConnection.status == "active"
    ).all()
    
    if not conns:
        return {"status": "success", "message": "No active Meta page connections found", "sync_results": {"leads_imported": 0, "duplicates_skipped": 0, "forms_synced": 0}}
        
    sync_results = {
        "forms_synced": 0,
        "leads_imported": 0,
        "duplicates_skipped": 0,
        "failed_forms": 0
    }
    
    async with httpx.AsyncClient() as client:
        for conn in conns:
            page_id = conn.page_id
            page_access_token = conn.access_token
            
            # Map form IDs to form names
            form_map = {}
            forms_meta = conn.connected_forms or []
            clean_form_ids = []
            
            if isinstance(forms_meta, list):
                for item in forms_meta:
                    if isinstance(item, dict):
                        fid = str(item.get("id"))
                        fname = item.get("name") or f"Form #{fid}"
                    else:
                        fid = str(item)
                        fname = f"Form #{fid}"
                    clean_form_ids.append(fid)
                    form_map[fid] = fname

            # If no connected forms stored, fetch all leadgen_forms for this page
            if not clean_form_ids and page_access_token:
                try:
                    url_forms = f"{FB_API_BASE}/{page_id}/leadgen_forms"
                    resp = await client.get(url_forms, params={"access_token": page_access_token, "fields": "id,name,status"})
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
                            page_id,
                            resp.status_code,
                            err_code,
                            err_subcode,
                            err_type,
                            err_msg,
                            fbtrace_id
                        )
                    elif "data" in data:
                        fc = [{"id": str(f["id"]), "name": f.get("name") or f"Form #{f['id']}", "status": f.get("status")} for f in data["data"]]
                        if conn.connected_forms != fc:
                            conn.connected_forms = fc
                            db.commit()
                        for f in fc:
                            clean_form_ids.append(f["id"])
                            form_map[f["id"]] = f["name"]
                except Exception as ex:
                    logger.warning(f"Could not fetch forms for page {page_id}: {ex}")

            seen_lead_ids = set()
            for form_id in clean_form_ids:
                url_leads = f"{FB_API_BASE}/{form_id}/leads"
                params_leads = {
                    "access_token": page_access_token,
                    "fields": "id,created_time,field_data,campaign_name,form_id",
                    "limit": 100
                }
                
                try:
                    form_success = True
                    while url_leads:
                        resp_leads = await client.get(url_leads, params=params_leads)
                        leads_data = resp_leads.json()
                        
                        if "error" in leads_data:
                            logger.error(f"Error syncing leads for form {form_id}: {leads_data['error']}")
                            form_success = False
                            break
                            
                        for l in leads_data.get("data", []):
                            fb_lead_id = l.get("id")
                            if not fb_lead_id or fb_lead_id in seen_lead_ids:
                                sync_results["duplicates_skipped"] += 1
                                continue
                                
                            existing = db.query(Lead).filter(
                                Lead.fb_lead_id == fb_lead_id,
                                Lead.org_id == current_user.org_id
                            ).first()
                            
                            if existing:
                                seen_lead_ids.add(fb_lead_id)
                                sync_results["duplicates_skipped"] += 1
                                continue
                                
                            fields = parse_field_data(l.get("field_data", []))
                            
                            created_at_val = None
                            if "created_time" in l:
                                try:
                                    created_at_val = dateutil.parser.parse(l["created_time"])
                                except Exception:
                                    logger.debug("Failed to parse created_time for lead %s", fb_lead_id)
                            
                            target_form_id = str(l.get("form_id") or form_id)
                            resolved_form_name = form_map.get(target_form_id, f"Form #{target_form_id}")

                            new_lead = Lead(
                                org_id=current_user.org_id,
                                fb_lead_id=fb_lead_id,
                                name=extract_field(fields, *NAME_FIELD_NAMES) or "Meta Lead",
                                email=extract_field(fields, *EMAIL_FIELD_NAMES),
                                phone=extract_field(fields, *PHONE_FIELD_NAMES),
                                campaign_name=l.get("campaign_name"),
                                form_name=resolved_form_name,
                                raw_data=l,
                                created_at=created_at_val
                            )
                            from app.services.lead_ingestion import insert_lead_once
                            new_lead, inserted = insert_lead_once(db, new_lead)
                            if not inserted:
                                seen_lead_ids.add(fb_lead_id)
                                sync_results["duplicates_skipped"] += 1
                                continue
                            from app.routers.webhooks import sync_lead_to_google_sheets
                            sync_lead_to_google_sheets(db, new_lead, commit=False)
                            seen_lead_ids.add(fb_lead_id)
                            sync_results["leads_imported"] += 1
                            
                        paging = leads_data.get("paging", {})
                        url_leads = paging.get("next")
                        params_leads = None
                        
                    if form_success:
                        sync_results["forms_synced"] += 1
                    else:
                        sync_results["failed_forms"] += 1
                        
                except Exception as e:
                    logger.exception(f"Exception syncing form {form_id}: {e}")
                    sync_results["failed_forms"] += 1

    db.commit()

    # Reflect Meta's live campaign state (stopped -> archived, active -> active).
    status_results = {"checked": 0, "updated": 0}
    if conns:
        try:
            status_results = await sync_campaign_statuses(db, current_user.org_id)
        except Exception as exc:
            logger.warning("Campaign status refresh failed during Meta sync: %s", exc)

    return {
        "status": "success",
        "sync_results": sync_results,
        "campaign_statuses": status_results
    }

