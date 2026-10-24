import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_active_env_file() -> str:
    app_env = os.getenv("APP_ENV", os.getenv("ENV", "dev")).strip().lower()
    target_file = ".env.prod" if app_env in ("prod", "production") else ".env.dev"
    if not Path(target_file).exists() and Path(".env").exists():
        return ".env"
    return target_file


ACTIVE_ENV_FILE = get_active_env_file()

# Exported to os.environ as well so SDKs that read their own env vars (LiveKit CLI) see the same values.
if Path(ACTIVE_ENV_FILE).exists():
    load_dotenv(ACTIVE_ENV_FILE, override=False)


class Settings(BaseSettings):
    APP_ENV: str = "dev"
    LOG_LEVEL: str = "INFO"

    # LiveKit Cloud
    LIVEKIT_URL: str
    LIVEKIT_API_KEY: str
    LIVEKIT_API_SECRET: str

    # Observability (Langfuse)
    LANGFUSE_PUBLIC_KEY: str
    LANGFUSE_SECRET_KEY: str
    LANGFUSE_BASE_URL: str

    # Telephony (Telnyx SIP & Carrier Failover)
    TELNYX_API_KEY: str
    TELNYX_PHONE_NUMBER: str
    TELNYX_API_BASE_URL: str = "https://api.telnyx.com/v2"
    CLINIC_FAILOVER_PHONE: str
    SIP_TRUNK_ID: str = ""
    DEFAULT_COUNTRY_CODE: str = "+1"

    # LiveKit Inference Model Selection
    STT_PROVIDER: str
    STT_MODEL: str
    LLM_PROVIDER: str
    LLM_MODEL: str
    TTS_PROVIDER: str
    TTS_MODEL: str
    TTS_VOICE_ID: str

    # Silero VAD & Turn Tuning
    VAD_MIN_SPEECH_DURATION: float
    VAD_MIN_SILENCE_DURATION: float

    # Clinic Operations & Booking
    CLINIC_NAME: str
    CLINIC_TIMEZONE: str
    APPOINTMENT_DEFAULT_DURATION_MINUTES: int
    CLINIC_OPEN_HOUR: int = 9
    CLINIC_CLOSE_HOUR: int = 18
    SLOT_INTERVAL_MINUTES: int = 30
    AFTERNOON_START_HOUR: int = 12
    EVENING_START_HOUR: int = 16
    NEARBY_SEARCH_DAYS: int = 3

    # Integrations (mock, google, telnyx)
    CALENDAR_BACKEND: str
    CALENDAR_API_KEY: str
    CALENDAR_ID: str
    SECRETS_MOUNT_DIR: str = "/etc/secrets"
    SMS_BACKEND: str
    BOOKINGS_DB_PATH: str = str(Path(tempfile.gettempdir()) / "voice_assistant_bookings.db")

    # Timeouts & Concurrency (seconds)
    STARTUP_CHECK_TIMEOUT: float
    NUM_IDLE_PROCESSES: int = 10
    PROCESS_INIT_TIMEOUT: float = 30.0
    PARTICIPANT_WAIT_TIMEOUT: float = 5.0
    ROOM_SID_TIMEOUT: float = 2.0
    SMS_REQUEST_TIMEOUT: float = 5.0
    BOOKINGS_DB_TIMEOUT: float = 10.0

    # Guardrails Configuration
    GUARDRAILS_ENABLED: bool = True
    GUARDRAILS_MAX_BOOKING_DAYS_AHEAD: int = 60
    GUARDRAILS_EMERGENCY_TRIAGE_ENABLED: bool = True
    GUARDRAILS_INJECTION_DEFENSE_ENABLED: bool = True
    GUARDRAILS_TTS_SANITIZER_ENABLED: bool = True

    model_config = SettingsConfigDict(
        env_file=ACTIVE_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
