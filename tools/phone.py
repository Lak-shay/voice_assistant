from __future__ import annotations

import re

from livekit import rtc

from config import settings
from logger import logger

# Only '+'-prefixed numbers, as in LiveKit SIP dispatch room names ('call-_+15551234567_abc'), so that
# timestamps or other digit runs in room names are never mistaken for caller ID.
_ROOM_NAME_PHONE = re.compile(r"(\+\d{10,15})")


def sanitize_e164(phone: str, default_country_code: str | None = None) -> str | None:
    """Normalize to E.164. Bare 10-digit numbers get the default country code. Returns None if not a phone number."""
    country_digits = (default_country_code or settings.DEFAULT_COUNTRY_CODE).lstrip("+")
    cleaned = re.sub(r"[^\d+]", "", phone.strip())
    if cleaned.startswith("+"):
        digits = cleaned[1:].replace("+", "")
        return f"+{digits}" if 10 <= len(digits) <= 15 else None

    digits = cleaned.replace("+", "")
    if len(digits) == 10:
        return f"+{country_digits}{digits}"
    if len(digits) == len(country_digits) + 10 and digits.startswith(country_digits):
        return f"+{digits}"
    return None


def _phone_candidates(participant: rtc.Participant) -> list[str]:
    candidates: list[str] = []
    attrs = getattr(participant, "attributes", None) or {}
    if isinstance(attrs, dict) and attrs.get("sip.phoneNumber"):
        candidates.append(attrs["sip.phoneNumber"])
    identity = getattr(participant, "identity", None)
    if isinstance(identity, str):
        if identity.startswith("sip_"):
            candidates.append(identity.removeprefix("sip_"))
        elif getattr(participant, "kind", None) == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            candidates.append(identity)
    return candidates


def get_sip_caller_phone(room: rtc.Room | None = None, participant: rtc.Participant | None = None) -> str | None:
    """Resolve the caller's E.164 number from SIP attributes, participant identity, or the room name, in that order."""
    participants: list[rtc.Participant] = [participant] if participant is not None else []
    remote = getattr(room, "remote_participants", None) if room is not None else None
    if isinstance(remote, dict):
        participants.extend(p for p in remote.values() if p is not participant)

    candidates = [c for p in participants for c in _phone_candidates(p)]
    room_name = getattr(room, "name", None)
    if isinstance(room_name, str):
        match = _ROOM_NAME_PHONE.search(room_name)
        if match:
            candidates.append(match.group(1))

    for cand in candidates:
        sanitized = sanitize_e164(cand)
        if sanitized:
            logger.info("Resolved caller phone number: raw=%s, sanitized=%s", cand, sanitized)
            return sanitized
    return None
