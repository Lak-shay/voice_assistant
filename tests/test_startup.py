from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from config import Settings
from startup import (
    _sync_langfuse_check,
    check_calendar_connection,
    check_langfuse_connection,
    check_livekit_connection,
    check_model_configuration,
    check_telnyx_connection,
    run_startup_checks,
)


@pytest.fixture
def mock_settings():
    return Settings(
        APP_ENV="test",
        LIVEKIT_URL="wss://test.livekit.cloud",
        LIVEKIT_API_KEY="test_key",
        LIVEKIT_API_SECRET="test_secret_with_minimum_32_bytes_length!",
        LANGFUSE_PUBLIC_KEY="pk-lf-test",
        LANGFUSE_SECRET_KEY="sk-lf-test",
        LANGFUSE_BASE_URL="https://jp.cloud.langfuse.com",
        TELNYX_API_KEY="test_telnyx_key",
        TELNYX_PHONE_NUMBER="+15550001111",
        CLINIC_FAILOVER_PHONE="+15551234567",
        SIP_TRUNK_ID="trunk_123",
        STT_PROVIDER="deepgram",
        STT_MODEL="nova-3",
        LLM_PROVIDER="openai",
        LLM_MODEL="gpt-4o-mini",
        TTS_PROVIDER="cartesia",
        TTS_MODEL="sonic-3",
        TTS_VOICE_ID="9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
        VAD_MIN_SPEECH_DURATION=0.05,
        VAD_MIN_SILENCE_DURATION=0.40,
        CLINIC_NAME="Dr. Smith's Clinic",
        CLINIC_TIMEZONE="America/New_York",
        APPOINTMENT_DEFAULT_DURATION_MINUTES=30,
        CALENDAR_BACKEND="mock",
        CALENDAR_API_KEY="",
        CALENDAR_ID="",
        SMS_BACKEND="mock",
        STARTUP_CHECK_TIMEOUT=2.0,
    )


@pytest.mark.asyncio
async def test_check_livekit_missing_credentials(mock_settings):
    mock_settings.LIVEKIT_URL = ""
    ok, msg = await check_livekit_connection(mock_settings)
    assert not ok
    assert "Missing" in msg


@pytest.mark.asyncio
async def test_check_livekit_success(mock_settings):
    mock_api = MagicMock()
    mock_api.__aenter__ = AsyncMock(return_value=mock_api)
    mock_api.__aexit__ = AsyncMock(return_value=None)
    mock_room_res = MagicMock()
    mock_room_res.rooms = ["room1", "room2"]
    mock_api.room.list_rooms = AsyncMock(return_value=mock_room_res)

    with patch("startup.LiveKitAPI", return_value=mock_api):
        ok, msg = await check_livekit_connection(mock_settings)
        assert ok
        assert "Connected to" in msg
        assert "2 active rooms" in msg


@pytest.mark.asyncio
async def test_check_livekit_exception(mock_settings):
    with patch("startup.LiveKitAPI", side_effect=RuntimeError("Network unreachable")):
        ok, msg = await check_livekit_connection(mock_settings)
        assert not ok
        assert "Network unreachable" in msg


@pytest.mark.asyncio
async def test_check_langfuse_missing_credentials(mock_settings):
    mock_settings.LANGFUSE_PUBLIC_KEY = ""
    ok, msg = await check_langfuse_connection(mock_settings)
    assert not ok
    assert "Missing" in msg


@pytest.mark.asyncio
async def test_check_langfuse_success(mock_settings):
    with patch("startup._sync_langfuse_check", return_value=True):
        ok, msg = await check_langfuse_connection(mock_settings)
        assert ok
        assert "Connected and authenticated" in msg
        assert "jp.cloud.langfuse.com" in msg


@pytest.mark.asyncio
async def test_check_langfuse_failure(mock_settings):
    with patch("startup._sync_langfuse_check", return_value=False):
        ok, msg = await check_langfuse_connection(mock_settings)
        assert not ok
        assert "authentication failed" in msg


def test_sync_langfuse_check_auth_check():
    with patch("langfuse.Langfuse.auth_check", return_value=True):
        res = _sync_langfuse_check("pk-test", "sk-test", "https://cloud.langfuse.com")
        assert res is True


@pytest.mark.asyncio
async def test_check_model_configuration_missing(mock_settings):
    mock_settings.STT_MODEL = ""
    mock_settings.LLM_PROVIDER = ""
    ok, msg = await check_model_configuration(mock_settings)
    assert not ok
    assert "Missing required model configurations" in msg


@pytest.mark.asyncio
async def test_check_model_configuration_success(mock_settings):
    ok, msg = await check_model_configuration(mock_settings)
    assert ok
    assert "STT: deepgram/nova-3" in msg
    assert "LLM: openai/gpt-4o-mini" in msg
    assert "TTS: cartesia/sonic-3" in msg


@pytest.mark.asyncio
async def test_run_startup_checks_all_healthy(mock_settings):
    with patch("startup.check_livekit_connection", return_value=(True, "Connected")), \
         patch("startup.check_langfuse_connection", return_value=(True, "Connected")), \
         patch("startup.check_model_configuration", return_value=(True, "Models valid")):

        result = await run_startup_checks(mock_settings)
        assert result is True


@pytest.mark.asyncio
async def test_run_startup_checks_livekit_failure(mock_settings):
    with patch("startup.check_livekit_connection", return_value=(False, "Connection refused")), \
         patch("startup.check_langfuse_connection", return_value=(True, "Connected")), \
         patch("startup.check_model_configuration", return_value=(True, "Models valid")):

        result = await run_startup_checks(mock_settings)
        assert result is False


@pytest.mark.asyncio
async def test_run_startup_checks_langfuse_failure(mock_settings):
    with patch("startup.check_livekit_connection", return_value=(True, "Connected")), \
         patch("startup.check_langfuse_connection", return_value=(False, "Langfuse authentication failed")), \
         patch("startup.check_model_configuration", return_value=(True, "Models valid")):

        result = await run_startup_checks(mock_settings)
        assert result is False


@pytest.mark.asyncio
async def test_run_startup_checks_model_failure(mock_settings):
    with patch("startup.check_livekit_connection", return_value=(True, "Connected")), \
         patch("startup.check_langfuse_connection", return_value=(True, "Connected")), \
         patch("startup.check_model_configuration", return_value=(False, "Invalid model configuration")):

        result = await run_startup_checks(mock_settings)
        assert result is False


@pytest.mark.asyncio
async def test_check_telnyx_skipped_in_mock_mode(mock_settings):
    mock_settings.SMS_BACKEND = "mock"
    ok, msg = await check_telnyx_connection(mock_settings)
    assert ok is True
    assert "skipped" in msg.lower()


@pytest.mark.asyncio
async def test_check_telnyx_missing_api_key(mock_settings):
    mock_settings.SMS_BACKEND = "telnyx"
    mock_settings.TELNYX_API_KEY = ""
    ok, msg = await check_telnyx_connection(mock_settings)
    assert ok is False
    assert "Missing TELNYX_API_KEY" in msg


@pytest.mark.asyncio
async def test_run_startup_checks_telnyx_failure(mock_settings):
    with patch("startup.check_livekit_connection", return_value=(True, "Connected")), \
         patch("startup.check_langfuse_connection", return_value=(True, "Connected")), \
         patch("startup.check_model_configuration", return_value=(True, "Models valid")), \
         patch("startup.check_telnyx_connection", return_value=(False, "Telnyx authentication failed")), \
         patch("startup.check_calendar_connection", return_value=(True, "Calendar mock")):

        result = await run_startup_checks(mock_settings)
        assert result is False


@pytest.mark.asyncio
async def test_check_calendar_skipped_in_mock_mode(mock_settings):
    mock_settings.CALENDAR_BACKEND = "mock"
    ok, msg = await check_calendar_connection(mock_settings)
    assert ok is True
    assert "skipped" in msg.lower()


@pytest.mark.asyncio
async def test_check_calendar_missing_api_key_when_google(mock_settings):
    mock_settings.CALENDAR_BACKEND = "google"
    mock_settings.CALENDAR_API_KEY = ""
    ok, msg = await check_calendar_connection(mock_settings)
    assert ok is False
    assert "Missing CALENDAR_API_KEY" in msg


@pytest.mark.asyncio
async def test_check_calendar_google_success(mock_settings):
    mock_settings.CALENDAR_BACKEND = "google"
    mock_settings.CALENDAR_API_KEY = "test_key"
    mock_settings.CALENDAR_ID = "primary"

    mock_google = MagicMock()
    mock_fb = MagicMock()
    mock_fb.execute.return_value = {"calendars": {"primary": {"busy": []}}}
    mock_google.freebusy.return_value.query.return_value = mock_fb

    with patch("tools.calendar_service.CalendarService._get_google_service", return_value=mock_google):
        ok, msg = await check_calendar_connection(mock_settings)
        assert ok is True
        assert "Connected to Google Calendar API" in msg


@pytest.mark.asyncio
async def test_check_calendar_google_failure(mock_settings):
    mock_settings.CALENDAR_BACKEND = "google"
    mock_settings.CALENDAR_API_KEY = "test_key"
    mock_settings.CALENDAR_ID = "primary"

    mock_google = MagicMock()
    mock_google.freebusy.return_value.query.return_value.execute.side_effect = RuntimeError("Google API network timeout")

    with patch("tools.calendar_service.CalendarService._get_google_service", return_value=mock_google):
        ok, msg = await check_calendar_connection(mock_settings)
        assert ok is False
        assert "failed" in msg.lower()


@pytest.mark.asyncio
async def test_run_startup_checks_calendar_failure(mock_settings):
    with patch("startup.check_livekit_connection", return_value=(True, "Connected")), \
         patch("startup.check_langfuse_connection", return_value=(True, "Connected")), \
         patch("startup.check_model_configuration", return_value=(True, "Models valid")), \
         patch("startup.check_telnyx_connection", return_value=(True, "Telnyx valid")), \
         patch("startup.check_calendar_connection", return_value=(False, "Google Calendar credentials invalid")):

        result = await run_startup_checks(mock_settings)
        assert result is False

