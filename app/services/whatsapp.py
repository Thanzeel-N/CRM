import httpx

from app.config import settings

GRAPH_API_BASE = "https://graph.facebook.com/v20.0"


async def send_whatsapp_message(to_phone: str, message: str) -> dict:
    """Send a plain text WhatsApp message via Meta's WhatsApp Cloud API.
    Note: outside a 24h customer service window, you must use a pre-approved
    template message instead of free text - see Meta's docs on message templates."""
    url = f"{GRAPH_API_BASE}/{settings.whatsapp_phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": message},
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()
