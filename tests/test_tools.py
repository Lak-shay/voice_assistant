from unittest.mock import AsyncMock, MagicMock
import pytest

from tools.appointment_tools import (
    book_appointment,
    check_availability,
    format_slot_phonetically,
    send_confirmation_sms,
)
from tools.calendar_service import CalendarService, calendar_service
from tools.sms_service import SMSService


@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch):
    monkeypatch.setattr("tools.calendar_service.settings.CALENDAR_BACKEND", "mock")
    monkeypatch.setattr("tools.appointment_tools.settings.CALENDAR_BACKEND", "mock")
    calendar_service._in_memory_bookings.clear()


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

    res_formatted = await sms.send_confirmation("(555) 999-8888", "Your appointment is confirmed.")
    assert res_formatted["success"] is True
    assert res_formatted["to"] == "+15559998888"

    res_invalid = await sms.send_confirmation("123", "Your appointment is confirmed.")
    assert res_invalid["success"] is False
    assert "E.164" in res_invalid["error"]


@pytest.mark.asyncio
async def test_tool_check_availability():
    mock_run_ctx = MagicMock()
    result = await check_availability(mock_run_ctx, preferred_date="2026-09-12")
    assert isinstance(result, str)
    assert "openings" in result.lower() or "slots" in result.lower()
    for forbidden in ("**", "*", "#", "```", "http"):
        assert forbidden not in result


@pytest.mark.asyncio
async def test_tool_check_availability_partial_slots(monkeypatch):
    from tools.calendar_service import calendar_service

    mock_run_ctx = MagicMock()

    # Single opening in morning only
    monkeypatch.setattr(
        calendar_service,
        "get_slots_by_period",
        AsyncMock(return_value={"morning": ["2026-09-12T09:00:00"], "afternoon": [], "evening": []}),
    )
    res_one = await check_availability(mock_run_ctx, preferred_date="2026-09-12")
    assert "an opening on" in res_one

    # Morning and afternoon available, but evening unavailable
    monkeypatch.setattr(
        calendar_service,
        "get_slots_by_period",
        AsyncMock(return_value={"morning": ["2026-09-12T09:00:00"], "afternoon": ["2026-09-12T14:00:00"], "evening": []}),
    )
    res_two = await check_availability(mock_run_ctx, preferred_date="2026-09-12")
    assert "morning and afternoon" in res_two
    assert "evening" not in res_two

    # Specific time requested that is unavailable -> only offer morning and afternoon
    res_time_unavailable = await check_availability(
        mock_run_ctx,
        preferred_date="2026-09-12",
        preferred_time="5:00 PM",
    )
    assert "unavailable" in res_time_unavailable
    assert "morning and afternoon" in res_time_unavailable
    assert "evening" not in res_time_unavailable

    # Customer specifies preferred period
    res_period = await check_availability(
        mock_run_ctx,
        preferred_date="2026-09-12",
        preferred_period="morning",
    )
    assert "September 12th at 9 AM" in res_period


@pytest.mark.asyncio
async def test_tool_book_appointment():
    mock_run_ctx = MagicMock()
    result = await book_appointment(
        mock_run_ctx,
        patient_name="Sarah Connor",
        patient_phone="+15553332222",
        slot_time="2026-09-12T10:30:00",
    )
    mock_run_ctx.disallow_interruptions.assert_called_once()
    assert "Sarah Connor" in result
    assert "confirmed" in result.lower()
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


@pytest.mark.asyncio
async def test_google_calendar_check_and_book_success():
    cal = CalendarService()
    mock_google = MagicMock()
    mock_fb = MagicMock()
    mock_fb.execute.return_value = {"calendars": {"primary": {"busy": []}}}
    mock_google.freebusy.return_value.query.return_value = mock_fb

    mock_ins = MagicMock()
    mock_ins.execute.return_value = {"id": "event_gcal_12345"}
    mock_google.events.return_value.insert.return_value = mock_ins
    cal._google_service = mock_google

    res = await cal.book_slot("2026-10-15T10:00:00", "John Doe", "+15554443333")
    assert res["success"] is True
    assert res["event_id"] == "event_gcal_12345"
    mock_google.freebusy.return_value.query.assert_called_once()
    mock_google.events.return_value.insert.assert_called_once()


@pytest.mark.asyncio
async def test_google_calendar_slot_busy_rejection():
    cal = CalendarService()
    mock_google = MagicMock()
    mock_fb = MagicMock()
    mock_fb.execute.return_value = {
        "calendars": {
            "primary": {
                "busy": [
                    {
                        "start": "2026-10-15T09:45:00-04:00",
                        "end": "2026-10-15T10:15:00-04:00",
                    }
                ]
            }
        }
    }
    mock_google.freebusy.return_value.query.return_value = mock_fb
    cal._google_service = mock_google

    res = await cal.book_slot("2026-10-15T10:00:00", "Jane Doe", "+15556667777")
    assert res["success"] is False
    assert "taken" in res["error"] or "unavailable" in res["error"]
    mock_google.freebusy.return_value.query.assert_called_once()
    mock_google.events.return_value.insert.assert_not_called()


@pytest.mark.asyncio
async def test_calendar_service_thread_concurrency_lock():
    import asyncio
    cal = CalendarService()
    slot = "2026-11-01T14:00:00"

    results = await asyncio.gather(
        cal.book_slot(slot, "Patient One", "+15551111111"),
        cal.book_slot(slot, "Patient Two", "+15552222222"),
    )

    successes = [r for r in results if r.get("success") is True]
    failures = [r for r in results if r.get("success") is False]

    assert len(successes) == 1
    assert len(failures) == 1
    assert "taken" in failures[0]["error"]
