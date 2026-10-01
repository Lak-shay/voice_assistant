from __future__ import annotations

import asyncio
import datetime
import sys
from typing import Dict, Optional, Tuple
import aiohttp
from livekit.api import LiveKitAPI
from livekit.protocol.room import ListRoomsRequest

from config import Settings, settings as default_settings
from logger import startup_logger as logger


async def check_livekit_connection(
    settings: Settings,
) -> Tuple[bool, str]:
    if not settings.LIVEKIT_URL or not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        return False, "Missing LIVEKIT_URL, LIVEKIT_API_KEY, or LIVEKIT_API_SECRET"

    try:
        timeout = aiohttp.ClientTimeout(total=settings.STARTUP_CHECK_TIMEOUT)
        async with LiveKitAPI(
            url=settings.LIVEKIT_URL,
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
            timeout=timeout,
        ) as api:
            res = await api.room.list_rooms(ListRoomsRequest())
            return True, f"Connected to {settings.LIVEKIT_URL} ({len(res.rooms)} active rooms)"
    except Exception as exc:
        return False, f"LiveKit connection failed: {exc}"


def _sync_langfuse_check(public_key: str, secret_key: str, host: str) -> bool:
    from langfuse import Langfuse
    try:
        client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)
        return bool(client.auth_check())
    except Exception:
        return False


async def check_langfuse_connection(
    settings: Settings,
) -> Tuple[bool, str]:
    if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
        return False, "Missing LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY"

    host = settings.LANGFUSE_BASE_URL
    try:
        authenticated = await asyncio.wait_for(
            asyncio.to_thread(
                _sync_langfuse_check,
                settings.LANGFUSE_PUBLIC_KEY,
                settings.LANGFUSE_SECRET_KEY,
                host,
            ),
            timeout=settings.STARTUP_CHECK_TIMEOUT,
        )
        if authenticated:
            return True, f"Connected and authenticated to {host}"
        return False, f"Langfuse authentication failed for {host}"
    except asyncio.TimeoutError:
        return False, f"Langfuse connection timed out after {settings.STARTUP_CHECK_TIMEOUT}s"
    except Exception as exc:
        return False, f"Langfuse connection failed: {exc}"


async def check_model_configuration(
    settings: Settings,
) -> Tuple[bool, str]:
    required = {
        "STT_PROVIDER": settings.STT_PROVIDER,
        "STT_MODEL": settings.STT_MODEL,
        "LLM_PROVIDER": settings.LLM_PROVIDER,
        "LLM_MODEL": settings.LLM_MODEL,
        "TTS_PROVIDER": settings.TTS_PROVIDER,
        "TTS_MODEL": settings.TTS_MODEL,
        "TTS_VOICE_ID": settings.TTS_VOICE_ID,
    }
    missing = [k for k, v in required.items() if not v or not v.strip()]
    if missing:
        return False, f"Missing required model configurations: {', '.join(missing)}"

    try:
        from livekit.agents import inference
        fallback_secret = "test_secret_with_minimum_32_bytes_for_jwt!"
        inference.STT(
            f"{settings.STT_PROVIDER}/{settings.STT_MODEL}",
            api_key=settings.LIVEKIT_API_KEY or "test_key",
            api_secret=settings.LIVEKIT_API_SECRET or fallback_secret,
        )
        inference.LLM(
            f"{settings.LLM_PROVIDER}/{settings.LLM_MODEL}",
            api_key=settings.LIVEKIT_API_KEY or "test_key",
            api_secret=settings.LIVEKIT_API_SECRET or fallback_secret,
        )
        inference.TTS(
            f"{settings.TTS_PROVIDER}/{settings.TTS_MODEL}",
            voice=settings.TTS_VOICE_ID,
            api_key=settings.LIVEKIT_API_KEY or "test_key",
            api_secret=settings.LIVEKIT_API_SECRET or fallback_secret,
        )
        return True, (
            f"STT: {settings.STT_PROVIDER}/{settings.STT_MODEL}, "
            f"LLM: {settings.LLM_PROVIDER}/{settings.LLM_MODEL}, "
            f"TTS: {settings.TTS_PROVIDER}/{settings.TTS_MODEL} ({settings.TTS_VOICE_ID})"
        )
    except Exception as exc:
        return False, f"Inference model configuration invalid: {exc}"


async def check_telnyx_connection(
    settings: Settings,
) -> Tuple[bool, str]:
    if settings.SMS_BACKEND != "telnyx":
        return True, f"Telnyx verification skipped (SMS_BACKEND={settings.SMS_BACKEND})"

    if not settings.TELNYX_API_KEY:
        return False, "Missing TELNYX_API_KEY while SMS_BACKEND is 'telnyx'"

    url = "https://api.telnyx.com/v2/balance"
    headers = {
        "Authorization": f"Bearer {settings.TELNYX_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        timeout = aiohttp.ClientTimeout(total=settings.STARTUP_CHECK_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    currency = data.get("data", {}).get("currency", "USD")
                    balance = data.get("data", {}).get("balance", "0.00")
                    return True, f"Connected to Telnyx API (balance: {balance} {currency})"
                body = await resp.text()
                return False, f"Telnyx authentication failed (HTTP {resp.status}): {body}"
    except Exception as exc:
        return False, f"Telnyx connection failed: {exc}"


async def check_calendar_connection(
    settings: Settings,
) -> Tuple[bool, str]:
    if settings.CALENDAR_BACKEND != "google":
        return True, f"Calendar verification skipped (CALENDAR_BACKEND={settings.CALENDAR_BACKEND})"

    if not settings.CALENDAR_API_KEY:
        return False, "Missing CALENDAR_API_KEY while CALENDAR_BACKEND is 'google'"

    try:
        from tools.calendar_service import calendar_service
        service = calendar_service._get_google_service()
        if not service:
            return False, "Failed to initialize Google Calendar client from configured credentials"
        cal_id = settings.CALENDAR_ID or "primary"
        now = datetime.datetime.now(datetime.timezone.utc)
        body = {
            "timeMin": now.isoformat(),
            "timeMax": (now + datetime.timedelta(hours=1)).isoformat(),
            "items": [{"id": cal_id}],
        }
        res = await asyncio.to_thread(service.freebusy().query(body=body).execute)
        if "calendars" in res:
            return True, f"Connected to Google Calendar API (Calendar ID: {cal_id})"
        return False, "Google Calendar freebusy query did not return expected calendar data"
    except Exception as exc:
        return False, f"Google Calendar connection failed: {exc}"


async def run_startup_checks(
    custom_settings: Optional[Settings] = None,
) -> bool:
    app_settings = custom_settings or default_settings
    logger.info(
        "Initiating startup API connection checks (environment: %s)",
        app_settings.APP_ENV,
    )

    results: Dict[str, Tuple[bool, str]] = {}

    livekit_ok, livekit_msg = await check_livekit_connection(app_settings)
    results["LiveKit Cloud"] = (livekit_ok, livekit_msg)
    if livekit_ok:
        logger.info("[PASS] [LiveKit Cloud]: %s", livekit_msg)
    else:
        logger.error("[FAIL] [LiveKit Cloud]: %s", livekit_msg)

    langfuse_ok, langfuse_msg = await check_langfuse_connection(app_settings)
    results["Langfuse Telemetry"] = (langfuse_ok, langfuse_msg)
    if langfuse_ok:
        logger.info("[PASS] [Langfuse Telemetry]: %s", langfuse_msg)
    else:
        logger.error("[FAIL] [Langfuse Telemetry]: %s", langfuse_msg)

    model_ok, model_msg = await check_model_configuration(app_settings)
    results["LiveKit Inference Models"] = (model_ok, model_msg)
    if model_ok:
        logger.info("[PASS] [LiveKit Inference Models]: %s", model_msg)
    else:
        logger.error("[FAIL] [LiveKit Inference Models]: %s", model_msg)

    telnyx_ok, telnyx_msg = await check_telnyx_connection(app_settings)
    results["Telnyx Telephony"] = (telnyx_ok, telnyx_msg)
    if telnyx_ok:
        logger.info("[PASS] [Telnyx Telephony]: %s", telnyx_msg)
    else:
        logger.error("[FAIL] [Telnyx Telephony]: %s", telnyx_msg)

    cal_ok, cal_msg = await check_calendar_connection(app_settings)
    results["Google Calendar"] = (cal_ok, cal_msg)
    if cal_ok:
        logger.info("[PASS] [Google Calendar]: %s", cal_msg)
    else:
        logger.error("[FAIL] [Google Calendar]: %s", cal_msg)

    all_ok = livekit_ok and langfuse_ok and model_ok and telnyx_ok and cal_ok

    if all_ok:
        logger.info(
            "!!!!!!!!!!! Application startup readiness check passed (environment: %s) !!!!!!!!!!!!!!",
            app_settings.APP_ENV,
        )
        return True
    else:
        logger.error("Application startup readiness check failed.")
        return False


def main() -> None:
    success = asyncio.run(run_startup_checks())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
