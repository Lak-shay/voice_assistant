from unittest.mock import ANY, MagicMock, patch
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
    mock_client.start_observation.return_value = mock_trace

    with patch.object(tm, "get_client", return_value=mock_client):
        trace = tm.create_session_trace("room_abc_789", participant_id="caller_456")
        assert trace is mock_trace
        mock_client.start_observation.assert_called_once_with(
            name="inbound_call",
            trace_context={"trace_id": ANY},
            metadata={
                "room_name": "room_abc_789",
                "clinic_name": "Dr. Smith's Clinic",
            },
        )
        assert tm._active_traces.get("room_abc_789") is mock_trace


@pytest.mark.asyncio
async def test_telemetry_flush_non_blocking():
    tm = TelemetryManager()
    mock_client = MagicMock()
    mock_trace = MagicMock()
    tm._active_traces["room_abc_789"] = mock_trace

    with patch.object(tm, "get_client", return_value=mock_client):
        await tm.flush("room_abc_789")
        mock_trace.end.assert_called_once()
        mock_client.flush.assert_called_once()
        assert "room_abc_789" not in tm._active_traces
