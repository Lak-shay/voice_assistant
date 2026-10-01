from __future__ import annotations

import re
from typing import Any
import aiohttp
from config import settings
from logger import logger


def sanitize_e164(phone: str, default_country_code: str = "+1") -> str | None:
    cleaned = re.sub(r"[^\d+]", "", phone.strip())
    if cleaned.startswith("+"):
        digits_only = re.sub(r"\D", "", cleaned)
        if 10 <= len(digits_only) <= 15:
            return f"+{digits_only}"
        return None

    digits = re.sub(r"\D", "", cleaned)
    if len(digits) == 10:
        return f"{default_country_code}{digits}"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+{digits}"
    return None


class SMSService:
    TELNYX_API_URL = "https://api.telnyx.com/v2/messages"

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

        payload = {
            "from": sender,
            "to": phone,
            "text": message,
        }
        headers = {
            "Authorization": f"Bearer {settings.TELNYX_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as session:
                async with session.post(self.TELNYX_API_URL, json=payload, headers=headers) as resp:
                    resp_json = {}
                    try:
                        resp_json = await resp.json()
                    except Exception:
                        pass

                    if resp.status in (200, 201, 202):
                        message_id = resp_json.get("data", {}).get("id")
                        return {"success": True, "status": "sent", "message_id": message_id, "to": phone}

                    error_detail = None
                    errors = resp_json.get("errors") if isinstance(resp_json, dict) else None
                    if errors and isinstance(errors, list) and len(errors) > 0:
                        error_detail = errors[0].get("detail") or errors[0].get("title")

                    err_msg = error_detail or f"Telnyx API error HTTP {resp.status}"
                    logger.warning("Telnyx API returned status %s: %s", resp.status, err_msg)
                    return {"success": False, "error": err_msg}
        except Exception as exc:
            logger.warning("Telnyx network dispatch error: %s", exc)
            return {"success": False, "error": str(exc)}


sms_service = SMSService()

