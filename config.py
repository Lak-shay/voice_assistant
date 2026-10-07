import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


def get_active_env_file() -> str:
    app_env = os.getenv("APP_ENV", os.getenv("ENV", "dev")).strip().lower()
    if app_env in ("prod", "production"):
        target_file = ".env.prod"
    else:
        target_file = ".env.dev"

    if not Path(target_file).exists() and Path(".env").exists():
        return ".env"
    return target_file


class Settings(BaseSettings):
    APP_ENV: str = "dev"

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
    CLINIC_FAILOVER_PHONE: str
    SIP_TRUNK_ID: str

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

    # Integrations (mock, google, telnyx)
    CALENDAR_BACKEND: str
    CALENDAR_API_KEY: str
    CALENDAR_ID: str
    SMS_BACKEND: str

    # Timeouts & Concurrency
    STARTUP_CHECK_TIMEOUT: float
    NUM_IDLE_PROCESSES: int = 10

    # Guardrails Configuration
    GUARDRAILS_ENABLED: bool = True
    GUARDRAILS_MAX_BOOKING_DAYS_AHEAD: int = 60
    GUARDRAILS_EMERGENCY_TRIAGE_ENABLED: bool = True
    GUARDRAILS_INJECTION_DEFENSE_ENABLED: bool = True
    GUARDRAILS_TTS_SANITIZER_ENABLED: bool = True

    model_config = SettingsConfigDict(
        env_file=get_active_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
