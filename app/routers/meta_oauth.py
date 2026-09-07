from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Request, Body
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
import httpx
import jwt
import dateutil.parser
from datetime import datetime, timedelta

import logging

from app.config import settings
from app.database import get_db
from app.models import User, MetaPageConnection, Lead
from app.services.auth import get_current_user, SECRET_KEY, ALGORITHM
from app.services.meta import parse_field_data

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/facebook", tags=["facebook"])

FB_API_BASE = "https://graph.facebook.com/v20.0"

def create_fb_session_token(user_access_token: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=60)
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
        config_id = getattr(settings, "meta_config_id", "") or "4640757052822358"
        url = (
            f"https://www.facebook.com/v20.0/dialog/oauth?"
            f"client_id={settings.meta_app_id}"
            f"&redirect_uri={redirect_uri}"
            f"&response_type=code"
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
        return f"""
        <html><body>
        <script>
            window.opener.postMessage({{ type: 'FB_OAUTH_ERROR', error: '{error_description}' }}, '*');
            window.close();
        </script>
        </body></html>
        """
    if code:
        return f"""
        <html><body>
        <script>
            window.opener.postMessage({{ type: 'FB_OAUTH_SUCCESS', code: '{code}' }}, '*');
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
        data = resp.json()
        if "error" in data:
            raise HTTPException(status_code=400, detail=data["error"].get("message", "Failed to fetch forms"))
        
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
            required_scopes = ["pages_show_list", "leads_retrieval"]
            missing_scopes = [s for s in required_scopes if s not in granted_scopes]
            
            if missing_scopes:
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
            
            # 3. Historical Lead Sync
            seen_lead_ids = set()
            for form_id in forms:
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
                            
                            new_lead = Lead(
                                org_id=current_user.org_id,
                                fb_lead_id=fb_lead_id,
                                name=fields.get("full_name") or fields.get("name"),
                                email=fields.get("email"),
                                phone=fields.get("phone_number"),
                                campaign_name=l.get("campaign_name"),
                                form_name=l.get("form_id"),
                                raw_data=l,
                                created_at=created_at_val
                            )
                            db.add(new_lead)
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
        conn.connected_forms = forms
        conn.status = "active"
    else:
        conn = MetaPageConnection(
            org_id=current_user.org_id,
            user_id=current_user.id,
            page_id=page_id,
            page_name=page_name,
            access_token=page_access_token,
            connected_forms=forms,
            status="active"
        )
        db.add(conn)
        
    db.commit()
    db.refresh(conn)
    
    return {
        "status": "success", 
        "id": conn.id,
        "sync_results": sync_results
    }

@router.get("/connections")
def list_connections(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    conns = db.query(MetaPageConnection).filter(
        MetaPageConnection.org_id == current_user.org_id
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
            
    conn.status = "disconnected"
    db.commit()
    
    return {"status": "disconnected"}
