from typing import Optional
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v20.0"


async def fetch_lead_details(leadgen_id: str, access_token: Optional[str] = None) -> dict:
    """Fetch full lead field data from Meta's Graph API using the leadgen_id
    that arrives in the webhook notification."""
    url = f"{GRAPH_API_BASE}/{leadgen_id}"
    token = access_token or getattr(settings, "meta_page_access_token", "")
    params = {
        "access_token": token,
        "fields": "field_data,ad_id,ad_name,campaign_id,campaign_name,form_id,created_time",
    }
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except Exception:
            raise RuntimeError("Meta lead fetch failed; check page access and retry") from None




def parse_field_data(field_data: list) -> dict:
    """Meta returns answers as [{'name': 'email', 'values': ['a@b.com']}, ...].
    Flatten that into a normal dict."""
    parsed = {}
    for field in field_data:
        key = field.get("name")
        values = field.get("values", [])
        parsed[key] = values[0] if values else None
    return parsed
