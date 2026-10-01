from unittest.mock import AsyncMock, MagicMock
import pytest

from tools.appointment_tools import (
    book_appointment,
    check_availability,
    format_slot_phonetically,
    send_confirmation_sms,
)
from tools.calendar_service import CalendarService
from tools.sms_service import SMSService


def test_format_slot_phonetically():
    assert "September 10th at 2 30 PM" == format_slot_phonetically("2026-09-10T14:30:00")
    assert "January 1st at 9 AM" == format_slot_phonetically("2026-01-01T09:00:00")
    assert "March 2nd at 11 15 AM" == format_slot_phonetically("2026-03-02T11:15:00")
    assert "invalid-date" == format_slot_phonetically("invalid-date")


@pytest.mark.asyncio
async def test_calendar_service_available_slots():
    cal = CalendarService()
    slots = await cal.get_available_slots("2026-09-15")
    assert len(slots) > 0
    assert "2026-09-15T09:00:00" in slots

    invalid_slots = await cal.get_available_slots("not-a-date")
    assert invalid_slots == []


@pytest.mark.asyncio
async def test_calendar_service_booking_and_conflict():
    cal = CalendarService()
    slot = "2026-09-15T10:00:00"

    res = await cal.book_slot(slot, "Alice Walker", "+15551112222")
    assert res["success"] is True
    assert res["patient_name"] == "Alice Walker"

    # Conflicting booking on the same slot must fail
    conflict = await cal.book_slot(slot, "Bob Jones", "+15553334444")
    assert conflict["success"] is False
    assert "unavailable" in conflict["error"] or "taken" in conflict["error"]


@pytest.mark.asyncio
async def test_sms_service_mock_mode():
    sms = SMSService()
    res = await sms.send_confirmation("+15559998888", "Your appointment is confirmed.")
    assert res["success"] is True
    assert res["status"] == "simulated"
    assert res["to"] == "+15559998888"

    # Test auto-formatting of standard 10-digit US numbers
    res_formatted = await sms.send_confirmation("(555) 999-8888", "Your appointment is confirmed.")
    assert res_formatted["success"] is True
    assert res_formatted["to"] == "+15559998888"

    # Test rejection of invalid phone numbers
    res_invalid = await sms.send_confirmation("123", "Your appointment is confirmed.")
    assert res_invalid["success"] is False
    assert "E.164" in res_invalid["error"]


@pytest.mark.asyncio
async def test_tool_check_availability():
    mock_run_ctx = MagicMock()
    result = await check_availability(mock_run_ctx, preferred_date="2026-09-12")
    assert isinstance(result, str)
    assert "openings" in result.lower() or "slots" in result.lower()
    # Enforce voice UX rule: never emit markdown formatting syntax
    for forbidden in ("**", "*", "#", "```", "http"):
        assert forbidden not in result


@pytest.mark.asyncio
async def test_tool_book_appointment():
    mock_run_ctx = MagicMock()
    result = await book_appointment(
        mock_run_ctx,
        patient_name="Sarah Connor",
        patient_phone="+15553332222",
        slot_time="2026-09-12T10:30:00",
    )
    # Must disallow interruptions during mutating booking action
    mock_run_ctx.disallow_interruptions.assert_called_once()
    assert "Sarah Connor" in result
    assert "confirmed" in result.lower()
    # Enforce zero markdown syntax
    for forbidden in ("**", "*", "#", "```"):
        assert forbidden not in result


@pytest.mark.asyncio
async def test_tool_send_confirmation_sms():
    mock_run_ctx = MagicMock()
    result = await send_confirmation_sms(
        mock_run_ctx,
        patient_phone="+15553332222",
        appointment_summary="September 12th at 10 30 AM",
    )
    assert "confirmation text" in result.lower() or "text message" in result.lower()
    for forbidden in ("**", "*", "#", "```"):
        assert forbidden not in result
