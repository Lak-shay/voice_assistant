"""Test settings, applied before any project module reads config.

Values are forced (not defaulted) so a developer's shell or .env.dev can never point
the suite at a real calendar, SMS provider, Langfuse project, or the dev bookings DB.
"""

import os
import tempfile
from pathlib import Path

_TEST_ENV = {
    "APP_ENV": "test",
    "LIVEKIT_URL": "wss://test.livekit.cloud",
    "LIVEKIT_API_KEY": "test_key",
    "LIVEKIT_API_SECRET": "test_secret_with_minimum_32_bytes_length!",
    "LANGFUSE_PUBLIC_KEY": "",
    "LANGFUSE_SECRET_KEY": "",
    "LANGFUSE_BASE_URL": "https://cloud.langfuse.com",
    "TELNYX_API_KEY": "",
    "TELNYX_PHONE_NUMBER": "+15550001111",
    "CLINIC_FAILOVER_PHONE": "+15551234567",
    "STT_PROVIDER": "deepgram",
    "STT_MODEL": "nova-3",
    "LLM_PROVIDER": "openai",
    "LLM_MODEL": "gpt-4o-mini",
    "TTS_PROVIDER": "cartesia",
    "TTS_MODEL": "sonic-3",
    "TTS_VOICE_ID": "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc",
    "VAD_MIN_SPEECH_DURATION": "0.05",
    "VAD_MIN_SILENCE_DURATION": "0.40",
    "CLINIC_NAME": "Dr. Smith's Clinic",
    "CLINIC_TIMEZONE": "America/New_York",
    "APPOINTMENT_DEFAULT_DURATION_MINUTES": "30",
    "CALENDAR_BACKEND": "mock",
    "CALENDAR_API_KEY": "",
    "CALENDAR_ID": "",
    "SMS_BACKEND": "mock",
    "STARTUP_CHECK_TIMEOUT": "2.0",
    "BOOKINGS_DB_PATH": str(Path(tempfile.mkdtemp(prefix="va_tests_")) / "bookings.db"),
}

os.environ.update(_TEST_ENV)
