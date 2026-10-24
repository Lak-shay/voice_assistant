import datetime

import clinic_time
from config import Settings


def test_clinic_tz_falls_back_to_utc_for_unknown_zone(monkeypatch):
    monkeypatch.setattr(clinic_time.settings, "CLINIC_TIMEZONE", "Not/AZone")
    assert clinic_time.clinic_tz() is datetime.timezone.utc


def test_clinic_tz_resolves_configured_zone():
    assert str(clinic_time.clinic_tz()) == "America/New_York"


def test_operational_settings_have_defaults():
    s = Settings()
    assert (s.CLINIC_OPEN_HOUR, s.CLINIC_CLOSE_HOUR, s.SLOT_INTERVAL_MINUTES) == (9, 18, 30)
    assert (s.AFTERNOON_START_HOUR, s.EVENING_START_HOUR) == (12, 16)
    assert s.DEFAULT_COUNTRY_CODE == "+1"
    assert s.TELNYX_API_BASE_URL == "https://api.telnyx.com/v2"
