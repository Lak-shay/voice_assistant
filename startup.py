import asyncio
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
    """Synchronous Langfuse auth check executed in a background worker thread."""
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

    all_ok = livekit_ok and langfuse_ok

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
    """CLI entrypoint for standalone execution."""
    success = asyncio.run(run_startup_checks())
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
