from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from agent import ClinicReceptionistAgent, entrypoint


def test_clinic_agent_instructions_constraints():
    agent = ClinicReceptionistAgent()
    instructions = agent.instructions

    # Must mandate short conversational turns
    assert "1 to 2 short conversational sentences" in instructions
    # Must explicitly prohibit markdown characters
    assert "Never output markdown characters" in instructions
    # Must mandate phonetic representation
    assert "phonetically" in instructions


@pytest.mark.asyncio
async def test_entrypoint_orchestration():
    mock_ctx = MagicMock()
    mock_ctx.room.name = "room_test_12345"
    mock_ctx.connect = AsyncMock()
    mock_ctx.add_shutdown_callback = MagicMock()

    mock_session = MagicMock()
    mock_session.start = AsyncMock()

    with patch("agent.telemetry.create_session_trace") as mock_trace, \
         patch("agent.AgentSession", return_value=mock_session), \
         patch("agent.silero.VAD.load", return_value=MagicMock()), \
         patch("agent.inference.STT", return_value=MagicMock()), \
         patch("agent.inference.TTS", return_value=MagicMock()), \
         patch("agent.inference.LLM", return_value=MagicMock()):

        await entrypoint(mock_ctx)

        mock_ctx.connect.assert_awaited_once()
        mock_trace.assert_called_once_with("room_test_12345")
        mock_ctx.add_shutdown_callback.assert_called_once()
        mock_session.start.assert_awaited_once()
