from __future__ import annotations

import datetime
from guardrails.tool_guardrails import (
    validate_booking_date_range,
    validate_patient_name,
    validate_patient_phone,
)


def test_validate_patient_name():
    valid, err = validate_patient_name("Jane Doe")
    assert valid is True
    assert err is None

    invalid_cases = ["", "   ", "a", "12345", "anonymous", "unknown", "caller", "none"]
    for case in invalid_cases:
        valid, err = validate_patient_name(case)
        assert valid is False
        assert err is not None


def test_validate_patient_phone():
    valid, phone, err = validate_patient_phone("+15551234567")
    assert valid is True
    assert phone == "+15551234567"
    assert err is None

    valid, phone, err = validate_patient_phone("(555) 123-4567")
    assert valid is True
    assert phone == "+15551234567"

    valid, phone, err = validate_patient_phone("+10000000000")
    assert valid is False
    assert err is not None

    valid, phone, err = validate_patient_phone("123")
    assert valid is False


def test_validate_booking_date_range():
    ref_date = datetime.date(2026, 10, 1)

    valid, err = validate_booking_date_range("2026-10-15", reference_date=ref_date)
    assert valid is True
    assert err is None

    valid, err = validate_booking_date_range("2026-09-15", reference_date=ref_date)
    assert valid is False
    assert "past dates" in err.lower()

    valid, err = validate_booking_date_range("2027-01-15", reference_date=ref_date)
    assert valid is False
    assert "days in advance" in err.lower()


def test_validate_patient_phone_international():
    valid, phone, _ = validate_patient_phone("+44 20 7946 0958")
    assert valid is True
    assert phone == "+442079460958"

    valid, _, err = validate_patient_phone("+44 0000000000")
    assert valid is False
    assert "invalid" in err.lower()


def test_validate_booking_date_range_ignores_calendar_backend(monkeypatch):
    monkeypatch.setattr("guardrails.tool_guardrails.settings.CALENDAR_BACKEND", "mock")
    valid, err = validate_booking_date_range(datetime.date(2020, 1, 6))
    assert valid is False
    assert "past dates" in err.lower()
