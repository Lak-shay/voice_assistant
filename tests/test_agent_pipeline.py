from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from agent import ClinicReceptionistAgent, entrypoint


def test_clinic_agent_instructions_constraints():
    agent = ClinicReceptionistAgent()
    instructions = agent.instructions
    assert "1 to 2 short conversational sentences" in instructions
    assert "Never output markdown characters" in instructions
    assert "phonetically" in instructions
    assert "timezone" in instructions
    assert "Today's date is" in instructions
    assert "When would you like the appointment?" in instructions
    assert "confirm" in instructions.lower()
    assert "book_appointment" in instructions
    assert "No phone number was received from caller ID" in instructions
    assert "confirm the chosen appointment date, time, and phone number" in instructions


def test_clinic_agent_instructions_with_caller_phone():
    agent = ClinicReceptionistAgent(caller_phone="+18453768795")
    instructions = agent.instructions
    assert "+18453768795" in instructions
    assert "confirm the chosen appointment date and time" in instructions



@pytest.mark.asyncio
async def test_clinic_agent_on_enter_greeting():
    agent = ClinicReceptionistAgent()
    mock_session = MagicMock()
    mock_session.generate_reply = AsyncMock()
    agent._activity = MagicMock(session=mock_session)

    await agent.on_enter()
    mock_session.generate_reply.assert_awaited_once()
    call_kwargs = mock_session.generate_reply.call_args.kwargs
    assert "How can I help you?" in call_kwargs["instructions"]


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
