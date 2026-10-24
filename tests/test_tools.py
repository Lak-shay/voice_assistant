import asyncio
import datetime
import sqlite3
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo
import pytest
from livekit import rtc

from clinic_time import parse_flexible_date, parse_flexible_slot
from session_state import BookingRecord, CallUserdata
from tools.appointment_tools import (
    book_appointment,
    check_availability,
    send_confirmation_sms,
    transfer_to_human,
)
from tools.calendar_service import NOT_A_SLOT_ERROR, CalendarService, CalendarUnavailableError, calendar_service
from tools.phone import get_sip_caller_phone
from tools.sms_service import SMSService
from tools.speech_format import format_date_phonetically, format_slot_phonetically

FUTURE_ISO = (datetime.date.today() + datetime.timedelta(days=14)).isoformat()


@pytest.fixture(autouse=True)
def isolate_test_environment(monkeypatch):
    monkeypatch.setattr("tools.calendar_service.settings.CALENDAR_BACKEND", "mock")
    calendar_service.clear_reservations()


def test_format_slot_phonetically():
    assert "September 10th at 2 30 PM" == format_slot_phonetically("2026-09-10T14:30:00")
    assert "January 1st at 9 AM" == format_slot_phonetically("2026-01-01T09:00:00")
    assert "March 2nd at 11 15 AM" == format_slot_phonetically("2026-03-02T11:15:00")
    assert "October 15th at 10 AM" == format_slot_phonetically("2026-10-15 10:00 AM")
    assert "invalid-date" == format_slot_phonetically("invalid-date")
    assert "September 10th" == format_date_phonetically("2026-09-10")
    assert "October 5th" == format_date_phonetically("October 5th, 2026")


@pytest.mark.asyncio
async def test_calendar_service_all_slots():
    cal = CalendarService()
    slots = await cal.get_all_slots_for_date(FUTURE_ISO)
    assert len(slots) > 0
    assert f"{FUTURE_ISO}T09:00:00" in slots

    assert await cal.get_all_slots_for_date("2020-01-06") == []

    invalid_slots = await cal.get_all_slots_for_date("not-a-date")
    assert invalid_slots == []


def test_google_credentials_fall_back_to_secrets_mount(monkeypatch, tmp_path):
    mounted = tmp_path / "missing-credentials.json"
    mounted.write_text("{}")
    monkeypatch.setattr("tools.calendar_service.settings.CALENDAR_BACKEND", "google")
    monkeypatch.setattr("tools.calendar_service.settings.CALENDAR_API_KEY", "missing-credentials.json")
    monkeypatch.setattr("tools.calendar_service.settings.SECRETS_MOUNT_DIR", str(tmp_path))
    from_file = MagicMock()
    monkeypatch.setattr("tools.calendar_service.service_account.Credentials.from_service_account_file", from_file)
    monkeypatch.setattr("tools.calendar_service.build", MagicMock())

    assert CalendarService().get_google_service() is not None
    assert from_file.call_args.args[0] == str(mounted)


@pytest.mark.asyncio
async def test_calendar_service_booking_and_conflict():
    cal = CalendarService()
    slot = f"{FUTURE_ISO}T10:00:00"

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
    result = await check_availability(mock_run_ctx, preferred_date=FUTURE_ISO)
    assert isinstance(result, str)
    assert "openings" in result.lower() or "slots" in result.lower()
    for forbidden in ("**", "*", "#", "```", "http"):
        assert forbidden not in result


@pytest.mark.asyncio
async def test_tool_check_availability_partial_slots(monkeypatch):
    mock_run_ctx = MagicMock()

    monkeypatch.setattr(
        calendar_service,
        "get_slots_by_period",
        AsyncMock(return_value={"morning": [f"{FUTURE_ISO}T09:00:00"], "afternoon": [], "evening": []}),
    )
    res_one = await check_availability(mock_run_ctx, preferred_date=FUTURE_ISO)
    assert "an opening on" in res_one

    monkeypatch.setattr(
        calendar_service,
        "get_slots_by_period",
        AsyncMock(return_value={"morning": [f"{FUTURE_ISO}T09:00:00"], "afternoon": [f"{FUTURE_ISO}T14:00:00"], "evening": []}),
    )
    res_two = await check_availability(mock_run_ctx, preferred_date=FUTURE_ISO)
    assert "morning and afternoon" in res_two
    assert "evening" not in res_two

    res_time_unavailable = await check_availability(
        mock_run_ctx,
        preferred_date=FUTURE_ISO,
        preferred_time="5:00 PM",
    )
    assert "unavailable" in res_time_unavailable
    assert "morning and afternoon" in res_time_unavailable
    assert "evening" not in res_time_unavailable

    res_period = await check_availability(
        mock_run_ctx,
        preferred_date=FUTURE_ISO,
        preferred_period="morning",
    )
    assert format_slot_phonetically(f"{FUTURE_ISO}T09:00:00") in res_period


@pytest.mark.asyncio
async def test_tool_book_appointment():
    mock_run_ctx = MagicMock()
    result = await book_appointment(
        mock_run_ctx,
        patient_name="Sarah Connor",
        patient_phone="+15553332222",
        slot_time=f"{FUTURE_ISO}T10:30:00",
    )
    mock_run_ctx.disallow_interruptions.assert_called_once()
    assert "Sarah Connor" in result
    assert "confirmed" in result.lower()
    for forbidden in ("**", "*", "#", "```"):
        assert forbidden not in result


@pytest.mark.asyncio
async def test_tool_send_confirmation_sms(monkeypatch):
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr("tools.appointment_tools.sms_service.send_confirmation", send)
    ctx = MagicMock()
    ctx.userdata = CallUserdata(
        trace_id="t",
        last_booking=BookingRecord(patient_name="Sarah Connor", patient_phone="+15553332222", slot_time=f"{FUTURE_ISO}T10:30:00"),
    )

    result = await send_confirmation_sms(ctx)

    assert "confirmation" in result.lower()
    for forbidden in ("**", "*", "#", "```"):
        assert forbidden not in result
    to_phone, message = send.call_args.args
    assert to_phone == "+15553332222"
    assert format_slot_phonetically(f"{FUTURE_ISO}T10:30:00") in message


@pytest.mark.asyncio
async def test_tool_send_confirmation_sms_requires_booking(monkeypatch):
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr("tools.appointment_tools.sms_service.send_confirmation", send)
    ctx = MagicMock()
    ctx.userdata = CallUserdata(trace_id="t")

    result = await send_confirmation_sms(ctx)

    assert "after an appointment has been booked" in result
    send.assert_not_called()


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

    res = await cal.book_slot(f"{FUTURE_ISO}T10:00:00", "John Doe", "+15554443333")
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
                        "start": f"{FUTURE_ISO}T09:45:00",
                        "end": f"{FUTURE_ISO}T10:15:00",
                    }
                ]
            }
        }
    }
    mock_google.freebusy.return_value.query.return_value = mock_fb
    cal._google_service = mock_google

    res = await cal.book_slot(f"{FUTURE_ISO}T10:00:00", "Jane Doe", "+15556667777")
    assert res["success"] is False
    assert "taken" in res["error"] or "unavailable" in res["error"]
    mock_google.freebusy.return_value.query.assert_called_once()
    mock_google.events.return_value.insert.assert_not_called()


@pytest.mark.asyncio
async def test_calendar_service_thread_concurrency_lock():
    cal = CalendarService()
    slot = f"{FUTURE_ISO}T14:00:00"

    results = await asyncio.gather(
        cal.book_slot(slot, "Patient One", "+15551111111"),
        cal.book_slot(slot, "Patient Two", "+15552222222"),
    )

    successes = [r for r in results if r.get("success") is True]
    failures = [r for r in results if r.get("success") is False]

    assert len(successes) == 1
    assert len(failures) == 1
    assert "taken" in failures[0]["error"]


@pytest.mark.asyncio
async def test_contention_same_slot_fallback_booking():
    cal = CalendarService()
    contested_slot = f"{FUTURE_ISO}T10:00:00"
    alternative_slot = f"{FUTURE_ISO}T10:30:00"

    results = await asyncio.gather(
        cal.book_slot(contested_slot, "Alex Taylor", "+15551112222"),
        cal.book_slot(contested_slot, "Jordan Blake", "+15553334444"),
    )

    successes = [r for r in results if r.get("success") is True]
    failures = [r for r in results if r.get("success") is False]

    assert len(successes) == 1
    assert len(failures) == 1
    assert "taken" in failures[0]["error"]
    assert successes[0]["slot_time"] == contested_slot

    failed_caller = "Jordan Blake" if successes[0]["patient_name"] == "Alex Taylor" else "Alex Taylor"
    failed_phone = "+15553334444" if failed_caller == "Jordan Blake" else "+15551112222"

    fallback_res = await cal.book_slot(alternative_slot, failed_caller, failed_phone)
    assert fallback_res["success"] is True
    assert fallback_res["slot_time"] == alternative_slot
    assert fallback_res["patient_name"] == failed_caller


def test_parse_flexible_date():
    tz = ZoneInfo("America/New_York")
    base = datetime.datetime.now(tz).date()

    assert parse_flexible_date("today", tz) == base
    assert parse_flexible_date("tomorrow", tz) == base + datetime.timedelta(days=1)
    assert parse_flexible_date("yesterday", tz) == base - datetime.timedelta(days=1)
    assert parse_flexible_date("2026-10-05", tz) == datetime.date(2026, 10, 5)
    assert parse_flexible_date("October 5th, 2026", tz) == datetime.date(2026, 10, 5)
    assert parse_flexible_date("10/05/2026", tz) == datetime.date(2026, 10, 5)
    assert parse_flexible_date("invalid-nonsense", tz) is None

    # Generic weekday and 'next <weekday>' parsing across all 7 days
    weekdays = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    for day_name, day_num in weekdays.items():
        days_ahead = (day_num - base.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        assert parse_flexible_date(day_name, tz, base_date=base) == base + datetime.timedelta(days=days_ahead)

        next_days = days_ahead + (7 if day_num > base.weekday() else 0)
        assert parse_flexible_date(f"next {day_name}", tz, base_date=base) == base + datetime.timedelta(days=next_days)


def test_parse_flexible_slot():
    tz = ZoneInfo("America/New_York")
    base = datetime.date(2026, 10, 5)

    res1 = parse_flexible_slot("2026-10-05T10:00:00", tz, default_date=base)
    assert res1 == datetime.datetime(2026, 10, 5, 10, 0, tzinfo=tz)

    res2 = parse_flexible_slot("2026-10-05 10:00 AM", tz, default_date=base)
    assert res2 == datetime.datetime(2026, 10, 5, 10, 0, tzinfo=tz)

    res3 = parse_flexible_slot("October 5th at 10:00 AM", tz, default_date=base)
    assert res3 == datetime.datetime(2026, 10, 5, 10, 0, tzinfo=tz)

    res4 = parse_flexible_slot("10:00 AM", tz, default_date=base)
    assert res4 == datetime.datetime(2026, 10, 5, 10, 0, tzinfo=tz)

    assert parse_flexible_slot("not-a-valid-time", tz) is None

    # Generic slot parsing for 'next <weekday>' across all 7 days
    weekdays = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    for day_name, day_num in weekdays.items():
        days_ahead = (day_num - base.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        if day_num > base.weekday():
            days_ahead += 7
        expected_dt = datetime.datetime.combine(base + datetime.timedelta(days=days_ahead), datetime.time(10, 0), tzinfo=tz)
        assert parse_flexible_slot(f"next {day_name} at 10 AM", tz, default_date=base) == expected_dt



@pytest.mark.asyncio
async def test_tool_book_appointment_natural_language():
    mock_run_ctx = MagicMock()
    result = await book_appointment(
        mock_run_ctx,
        patient_name="Alex Mercer",
        patient_phone="(555) 234-5678",
        slot_time=f"{FUTURE_ISO} 10:00 AM",
    )
    assert "Alex Mercer" in result
    assert "confirmed" in result.lower()


@pytest.mark.asyncio
async def test_tool_book_appointment_invalid_slot():
    mock_run_ctx = MagicMock()
    result = await book_appointment(
        mock_run_ctx,
        patient_name="Alex Mercer",
        patient_phone="+15552345678",
        slot_time="invalid time slot",
    )
    assert "could not be understood" in result.lower() or "unavailable" in result.lower()


@pytest.mark.asyncio
async def test_google_calendar_freebusy_error_handling(monkeypatch):
    cal = CalendarService()
    mock_google = MagicMock()
    mock_google.freebusy.return_value.query.side_effect = Exception("API connection dropped")
    cal._google_service = mock_google

    tz = ZoneInfo("America/New_York")
    with pytest.raises(CalendarUnavailableError):
        cal._sync_query_google_busy(
            datetime.datetime(2026, 10, 5, 9, tzinfo=tz),
            datetime.datetime(2026, 10, 5, 18, tzinfo=tz),
        )


@pytest.mark.asyncio
async def test_google_booking_fails_closed_when_freebusy_errors():
    cal = CalendarService()
    mock_google = MagicMock()
    mock_google.freebusy.return_value.query.return_value.execute.side_effect = Exception("503 backendError")
    cal._google_service = mock_google
    slot = f"{FUTURE_ISO}T10:00:00"

    res = await cal.book_slot(slot, "Jane Doe", "+15556667777")

    assert res["success"] is False
    assert "temporary" in res["error"].lower()
    mock_google.events.return_value.insert.assert_not_called()
    assert slot not in cal._get_db_booked_slots(FUTURE_ISO)


def test_freebusy_calendar_errors_raise():
    cal = CalendarService()
    mock_google = MagicMock()
    mock_google.freebusy.return_value.query.return_value.execute.return_value = {
        "calendars": {"primary": {"errors": [{"reason": "notFound"}], "busy": []}}
    }
    cal._google_service = mock_google
    tz = ZoneInfo("America/New_York")
    with pytest.raises(CalendarUnavailableError):
        cal._sync_query_google_busy(
            datetime.datetime(2026, 10, 5, 9, tzinfo=tz),
            datetime.datetime(2026, 10, 5, 18, tzinfo=tz),
        )


@pytest.mark.asyncio
async def test_check_availability_reports_calendar_outage(monkeypatch):
    monkeypatch.setattr("tools.calendar_service.settings.CALENDAR_BACKEND", "google")
    monkeypatch.setattr(calendar_service, "_google_service", None)
    monkeypatch.setattr(calendar_service, "get_google_service", lambda: None)

    res = await check_availability(MagicMock(), preferred_date=FUTURE_ISO)

    assert "trouble checking the schedule" in res


@pytest.mark.asyncio
async def test_google_backend_without_client_does_not_book(monkeypatch):
    monkeypatch.setattr("tools.calendar_service.settings.CALENDAR_BACKEND", "google")
    cal = CalendarService()
    monkeypatch.setattr(cal, "get_google_service", lambda: None)
    slot = f"{FUTURE_ISO}T10:00:00"

    res = await cal.book_slot(slot, "Jane Doe", "+15556667777")

    assert res["success"] is False
    assert slot not in cal._get_db_booked_slots(FUTURE_ISO)


def test_google_client_init_is_retried_after_failure(monkeypatch):
    monkeypatch.setattr("tools.calendar_service.settings.CALENDAR_BACKEND", "google")
    monkeypatch.setattr("tools.calendar_service.settings.CALENDAR_API_KEY", "{not valid json")
    cal = CalendarService()
    assert cal.get_google_service() is None

    monkeypatch.setattr("tools.calendar_service.settings.CALENDAR_API_KEY", '{"type": "service_account"}')
    monkeypatch.setattr("tools.calendar_service.service_account.Credentials.from_service_account_info", MagicMock())
    monkeypatch.setattr("tools.calendar_service.build", MagicMock())
    assert cal.get_google_service() is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "slot_time",
    [
        f"{FUTURE_ISO}T07:00:00",
        f"{FUTURE_ISO}T18:00:00",
        f"{FUTURE_ISO}T19:45:00",
        f"{FUTURE_ISO}T10:17:00",
        (datetime.date.today() - datetime.timedelta(days=1)).isoformat() + "T10:00:00",
    ],
)
async def test_book_slot_rejects_times_outside_the_schedule(slot_time):
    res = await CalendarService().book_slot(slot_time, "Jane Doe", "+15556667777")
    assert res["success"] is False
    assert res["error"] == NOT_A_SLOT_ERROR


def test_get_sip_caller_phone_scenarios():

    mock_participant = MagicMock()
    mock_participant.attributes = {"sip.phoneNumber": "+18455551234"}
    mock_room = MagicMock()
    mock_room.remote_participants = {"p1": mock_participant}
    assert get_sip_caller_phone(mock_room) == "+18455551234"

    mock_participant_raw = MagicMock()
    mock_participant_raw.attributes = {"sip.phoneNumber": "(845) 555-9876"}
    mock_room_raw = MagicMock()
    mock_room_raw.remote_participants = {"p2": mock_participant_raw}
    assert get_sip_caller_phone(mock_room_raw) == "+18455559876"

    # Test SIP participant identity without sip.phoneNumber attribute
    mock_sip_id_p = MagicMock()
    mock_sip_id_p.attributes = {}
    mock_sip_id_p.identity = "sip_+18455554321"
    mock_sip_id_room = MagicMock()
    mock_sip_id_room.remote_participants = {"p3": mock_sip_id_p}
    mock_sip_id_room.name = "regular_room"
    assert get_sip_caller_phone(mock_sip_id_room) == "+18455554321"

    # Test direct participant argument
    assert get_sip_caller_phone(participant=mock_sip_id_p) == "+18455554321"

    # Test room name regex fallback
    mock_room_name_only = MagicMock()
    mock_room_name_only.remote_participants = {}
    mock_room_name_only.name = "call-_+18455554321_Xw6qGY5M6nCd"
    assert get_sip_caller_phone(mock_room_name_only) == "+18455554321"

    mock_web_participant = MagicMock()
    mock_web_participant.attributes = {}
    mock_web_participant.identity = "playground_user_abc"
    mock_web_room = MagicMock()
    mock_web_room.remote_participants = {"p4": mock_web_participant}
    mock_web_room.name = "playground_room"
    assert get_sip_caller_phone(mock_web_room) is None

    assert get_sip_caller_phone(None) is None


@pytest.mark.asyncio
async def test_tool_book_appointment_with_explicit_phone():
    mock_ctx = MagicMock()
    result = await book_appointment(
        mock_ctx,
        patient_name="Alex Mercer",
        patient_phone="+18453334444",
        slot_time=f"{FUTURE_ISO}T11:00:00",
    )
    assert "Alex Mercer" in result
    assert "confirmed" in result.lower()


@pytest.mark.asyncio
async def test_tool_book_appointment_rejects_invalid_phone():
    mock_ctx = MagicMock()
    result = await book_appointment(
        mock_ctx,
        patient_name="Alex Mercer",
        patient_phone="not-a-number",
        slot_time=f"{FUTURE_ISO}T11:00:00",
    )
    assert "valid phone number is required" in result.lower()


@pytest.mark.asyncio
async def test_tool_transfer_to_human_success():
    mock_run_ctx = MagicMock()
    mock_job_ctx = MagicMock()
    mock_job_ctx.transfer_sip_participant = AsyncMock(return_value=None)

    mock_participant = MagicMock()
    mock_participant.identity = "sip_caller_test"
    mock_participant.kind = rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD
    mock_job_ctx.room.remote_participants = {"p1": mock_participant}

    mock_run_ctx.userdata = CallUserdata(trace_id="t", job_ctx=mock_job_ctx)

    res = await transfer_to_human(mock_run_ctx, reason="caller requested real person")
    mock_run_ctx.disallow_interruptions.assert_called_once()
    mock_job_ctx.transfer_sip_participant.assert_called_once_with(
        "sip_caller_test",
        "tel:+15551234567",
        play_dialtone=False,
    )
    assert "transferring you" in res.lower()


@pytest.mark.asyncio
async def test_tool_transfer_to_human_missing_config(monkeypatch):
    monkeypatch.setattr("tools.appointment_tools.settings.CLINIC_FAILOVER_PHONE", "")
    mock_run_ctx = MagicMock()
    res = await transfer_to_human(mock_run_ctx)
    assert "not configured" in res.lower()


@pytest.mark.asyncio
async def test_tool_transfer_to_human_handles_sip_error():
    mock_run_ctx = MagicMock()
    mock_job_ctx = MagicMock()
    mock_job_ctx.transfer_sip_participant = AsyncMock(side_effect=RuntimeError("SIP 486 Busy Here"))

    mock_participant = MagicMock()
    mock_participant.identity = "sip_caller_test"
    mock_job_ctx.room.remote_participants = {"p1": mock_participant}

    mock_run_ctx.userdata = CallUserdata(trace_id="t", job_ctx=mock_job_ctx)

    res = await transfer_to_human(mock_run_ctx)
    assert "unable to transfer" in res.lower()


@pytest.mark.asyncio
async def test_tool_transfer_to_human_standalone_mode():
    mock_run_ctx = MagicMock()
    mock_run_ctx.userdata = CallUserdata(trace_id="t")
    res = await transfer_to_human(mock_run_ctx)
    assert "transferring you" in res.lower()


@pytest.mark.asyncio
async def test_booking_fails_on_database_error(monkeypatch):
    cal = CalendarService()

    def broken_connect():
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(cal, "_connect", broken_connect)
    res = await cal.book_slot(f"{FUTURE_ISO}T10:00:00", "Alice Walker", "+15551112222")
    assert res["success"] is False
    assert "temporary" in res["error"].lower()


@pytest.mark.asyncio
async def test_tool_book_appointment_records_booking():
    ctx = MagicMock()
    ctx.userdata = CallUserdata(trace_id="t")
    await book_appointment(ctx, patient_name=" Sarah Connor ", patient_phone="(555) 333-2222", slot_time=f"{FUTURE_ISO}T10:30:00")
    assert ctx.userdata.last_booking == BookingRecord(
        patient_name="Sarah Connor", patient_phone="+15553332222", slot_time=f"{FUTURE_ISO}T10:30:00"
    )


@pytest.mark.asyncio
async def test_tool_check_availability_rejects_past_date():
    res = await check_availability(MagicMock(), preferred_date="2020-01-06")
    assert "past dates" in res.lower()


@pytest.mark.asyncio
async def test_tool_transfer_records_outcome():
    job_ctx = MagicMock()
    caller = MagicMock(identity="sip_caller", kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP)
    job_ctx.room.remote_participants = {"p1": caller}

    job_ctx.transfer_sip_participant = AsyncMock(return_value=None)
    ctx = MagicMock()
    ctx.userdata = CallUserdata(trace_id="t", job_ctx=job_ctx)
    await transfer_to_human(ctx, reason="billing question")
    assert ctx.userdata.last_transfer.transferred is True
    assert ctx.userdata.last_transfer.reason == "billing question"

    job_ctx.transfer_sip_participant = AsyncMock(side_effect=RuntimeError("SIP 486 Busy Here"))
    await transfer_to_human(ctx)
    assert ctx.userdata.last_transfer.transferred is False

    job_ctx.room.remote_participants = {}
    res = await transfer_to_human(ctx)
    assert "unable to transfer" in res.lower()
    assert ctx.userdata.last_transfer.transferred is False
