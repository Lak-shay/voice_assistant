from unittest.mock import MagicMock, patch
import pytest

from telemetry import TelemetryManager


def test_telemetry_disabled_without_keys():
    tm = TelemetryManager()
    with patch("telemetry.settings.LANGFUSE_PUBLIC_KEY", ""), \
         patch("telemetry.settings.LANGFUSE_SECRET_KEY", ""):
        client = tm.get_client()
        assert client is None
        assert tm.create_session_trace("room_123") is None


def test_telemetry_create_session_trace():
    tm = TelemetryManager()
    mock_client = MagicMock()
    mock_trace = MagicMock()
    mock_client.trace.return_value = mock_trace

    with patch.object(tm, "get_client", return_value=mock_client):
        trace = tm.create_session_trace("room_abc_789", participant_id="caller_456")
        assert trace is mock_trace
        mock_client.trace.assert_called_once_with(
            id="room_abc_789",
            name="inbound_call",
            session_id="room_abc_789",
            user_id="caller_456",
            metadata={
                "environment": tm.get_client().return_value.metadata if False else "dev",
                "room_name": "room_abc_789",
                "clinic_name": "Dr. Smith's Clinic",
            },
        )


@pytest.mark.asyncio
async def test_telemetry_flush_non_blocking():
    tm = TelemetryManager()
    mock_client = MagicMock()
    with patch.object(tm, "get_client", return_value=mock_client):
        await tm.flush("room_abc_789")
        mock_client.flush.assert_called_once()
