from __future__ import annotations

from typing import Any

import aiohttp

from config import settings
from logger import logger
from tools.phone import sanitize_e164


class SMSService:
    async def send_confirmation(self, to_phone: str, message: str) -> dict[str, Any]:
        phone = sanitize_e164(to_phone)
        if not phone:
            return {"success": False, "error": "Invalid recipient phone number. Must be a valid E.164 phone number."}

        sender = sanitize_e164(settings.TELNYX_PHONE_NUMBER) if settings.TELNYX_PHONE_NUMBER else ""
        if not sender and settings.SMS_BACKEND != "mock":
            return {"success": False, "error": "Configured TELNYX_PHONE_NUMBER is not a valid E.164 phone number."}

        if not settings.TELNYX_API_KEY or settings.SMS_BACKEND == "mock":
            logger.info("Simulated Telnyx SMS to %s: %s", phone, message)
            return {"success": True, "status": "simulated", "to": phone}

        payload = {"from": sender, "to": phone, "text": message}
        headers = {
            "Authorization": f"Bearer {settings.TELNYX_API_KEY}",
            "Content-Type": "application/json",
        }
        url = f"{settings.TELNYX_API_BASE_URL.rstrip('/')}/messages"

        try:
            timeout = aiohttp.ClientTimeout(total=settings.SMS_REQUEST_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload, headers=headers) as resp:
                    try:
                        resp_json = await resp.json()
                    except (aiohttp.ContentTypeError, ValueError):
                        resp_json = {}
                    if not isinstance(resp_json, dict):
                        resp_json = {}

                    if resp.status in (200, 201, 202):
                        message_id = resp_json.get("data", {}).get("id")
                        return {"success": True, "status": "sent", "message_id": message_id, "to": phone}

                    errors = resp_json.get("errors")
                    error_detail = None
                    if isinstance(errors, list) and errors:
                        error_detail = errors[0].get("detail") or errors[0].get("title")
                    err_msg = error_detail or f"Telnyx API error HTTP {resp.status}"
                    logger.warning("Telnyx API returned status %s: %s", resp.status, err_msg)
                    return {"success": False, "error": err_msg}
        except Exception as exc:
            logger.error("Telnyx network dispatch error: %s", exc)
            return {"success": False, "error": str(exc)}


sms_service = SMSService()
