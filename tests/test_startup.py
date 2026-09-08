from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from config import Settings
from startup import (
    check_langfuse_connection,
    check_livekit_connection,
    run_startup_checks,
)


@pytest.fixture
def mock_settings():
    return Settings(
        APP_ENV="test",
        LIVEKIT_URL="wss://test.livekit.cloud",
        LIVEKIT_API_KEY="test_key",
        LIVEKIT_API_SECRET="test_secret",
        LANGFUSE_PUBLIC_KEY="pk-lf-test",
        LANGFUSE_SECRET_KEY="sk-lf-test",
        LANGFUSE_BASE_URL="https://jp.cloud.langfuse.com",
        STARTUP_CHECK_TIMEOUT=2.0,
    )


@pytest.mark.asyncio
async def test_check_livekit_missing_credentials():
    """LiveKit check should fail immediately if credentials are missing."""
    empty_settings = Settings(
        APP_ENV="test",
        LIVEKIT_URL="",
        LIVEKIT_API_KEY="",
        LIVEKIT_API_SECRET="",
    )
    ok, msg = await check_livekit_connection(empty_settings)
    assert not ok
    assert "Missing" in msg


@pytest.mark.asyncio
async def test_check_livekit_success(mock_settings):
    """LiveKit check succeeds when list_rooms returns rooms."""
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
    """LiveKit check captures exceptions gracefully."""
    with patch("startup.LiveKitAPI", side_effect=RuntimeError("Network unreachable")):
        ok, msg = await check_livekit_connection(mock_settings)
        assert not ok
        assert "Network unreachable" in msg


@pytest.mark.asyncio
async def test_check_langfuse_missing_credentials():
    """Langfuse check should fail immediately if credentials are missing."""
    settings = Settings(LANGFUSE_PUBLIC_KEY="", LANGFUSE_SECRET_KEY="")
    ok, msg = await check_langfuse_connection(settings)
    assert not ok
    assert "Missing" in msg


@pytest.mark.asyncio
async def test_check_langfuse_success(mock_settings):
    """Langfuse check succeeds when auth_check passes."""
    with patch("startup._sync_langfuse_check", return_value=True):
        ok, msg = await check_langfuse_connection(mock_settings)
        assert ok
        assert "Connected and authenticated" in msg
        assert "jp.cloud.langfuse.com" in msg


@pytest.mark.asyncio
async def test_check_langfuse_failure(mock_settings):
    """Langfuse check fails when auth_check fails."""
    with patch("startup._sync_langfuse_check", return_value=False):
        ok, msg = await check_langfuse_connection(mock_settings)
        assert not ok
        assert "authentication failed" in msg


@pytest.mark.asyncio
async def test_run_startup_checks_all_healthy(mock_settings):
    """Full startup checks pipeline passes when all services are healthy."""
    with patch("startup.check_livekit_connection", return_value=(True, "Connected")), \
         patch("startup.check_langfuse_connection", return_value=(True, "Connected")):

        result = await run_startup_checks(mock_settings)
        assert result is True


@pytest.mark.asyncio
async def test_run_startup_checks_livekit_failure(mock_settings):
    """Full startup checks pipeline fails if LiveKit is down."""
    with patch("startup.check_livekit_connection", return_value=(False, "Connection refused")), \
         patch("startup.check_langfuse_connection", return_value=(True, "Connected")):

        result = await run_startup_checks(mock_settings)
        assert result is False


@pytest.mark.asyncio
async def test_run_startup_checks_langfuse_failure(mock_settings):
    """Full startup checks pipeline fails if Langfuse is down."""
    with patch("startup.check_livekit_connection", return_value=(True, "Connected")), \
         patch("startup.check_langfuse_connection", return_value=(False, "Langfuse authentication failed")):

        result = await run_startup_checks(mock_settings)
        assert result is False
