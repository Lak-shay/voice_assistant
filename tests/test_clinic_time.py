import datetime

import clinic_time
from config import Settings


def test_clinic_tz_falls_back_to_utc_for_unknown_zone(monkeypatch):
    monkeypatch.setattr(clinic_time.settings, "CLINIC_TIMEZONE", "Not/AZone")
    assert clinic_time.clinic_tz() is datetime.timezone.utc


def test_clinic_tz_resolves_configured_zone():
    assert str(clinic_time.clinic_tz()) == "America/New_York"


def test_explicit_date_wins_over_weekday_name():
    saturday = datetime.date(2026, 10, 24)
    assert clinic_time.parse_flexible_date("Friday, November 6th", base_date=saturday) == datetime.date(2026, 11, 6)
    assert clinic_time.parse_flexible_date("Friday", base_date=saturday) == datetime.date(2026, 10, 30)


def test_explicit_date_without_year_rolls_to_next_year():
    base = datetime.date(2026, 10, 24)
    assert clinic_time.parse_flexible_date("January 5th", base_date=base) == datetime.date(2027, 1, 5)
    assert clinic_time.parse_flexible_date("January 5th, 2026", base_date=base) == datetime.date(2026, 1, 5)


def test_slot_with_explicit_date_and_weekday():
    tz = clinic_time.clinic_tz()
    slot = clinic_time.parse_flexible_slot(
        "Thursday November 12th at 10 AM", tz, default_date=datetime.date(2026, 10, 24)
    )
    assert slot == datetime.datetime(2026, 11, 12, 10, 0, tzinfo=tz)


def test_operational_settings_have_defaults():
    s = Settings()
    assert (s.CLINIC_OPEN_HOUR, s.CLINIC_CLOSE_HOUR, s.SLOT_INTERVAL_MINUTES) == (9, 18, 30)
    assert (s.AFTERNOON_START_HOUR, s.EVENING_START_HOUR) == (12, 16)
    assert s.DEFAULT_COUNTRY_CODE == "+1"
    assert s.TELNYX_API_BASE_URL == "https://api.telnyx.com/v2"
