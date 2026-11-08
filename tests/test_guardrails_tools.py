from __future__ import annotations

import datetime
from guardrails.tool_guardrails import (
    validate_booking_date_range,
    validate_patient_name,
    validate_patient_phone,
)


def test_validate_patient_name():
    assert validate_patient_name("Jane Doe") is None

    invalid_cases = ["", "   ", "a", "12345", "anonymous", "unknown", "caller", "none"]
    for case in invalid_cases:
        assert validate_patient_name(case) is not None


def test_validate_patient_phone():
    assert validate_patient_phone("+15551234567") == ("+15551234567", None)
    assert validate_patient_phone("(555) 123-4567") == ("+15551234567", None)

    phone, err = validate_patient_phone("+10000000000")
    assert phone is None
    assert err is not None

    phone, err = validate_patient_phone("123")
    assert phone is None
    assert err is not None


def test_validate_booking_date_range():
    ref_date = datetime.date(2026, 10, 1)

    assert validate_booking_date_range("2026-10-15", reference_date=ref_date) is None
    assert "past dates" in validate_booking_date_range("2026-09-15", reference_date=ref_date).lower()
    assert "days in advance" in validate_booking_date_range("2027-01-15", reference_date=ref_date).lower()


def test_validate_patient_phone_international():
    assert validate_patient_phone("+44 20 7946 0958") == ("+442079460958", None)

    phone, err = validate_patient_phone("+44 0000000000")
    assert phone is None
    assert "invalid" in err.lower()


def test_validate_booking_date_range_ignores_calendar_backend(monkeypatch):
    monkeypatch.setattr("guardrails.tool_guardrails.settings.CALENDAR_BACKEND", "mock")
    err = validate_booking_date_range(datetime.date(2020, 1, 6))
    assert "past dates" in err.lower()
