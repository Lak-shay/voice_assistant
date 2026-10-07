from __future__ import annotations

import re
from dataclasses import dataclass

EMERGENCY_PATTERNS = [
    (r"\b(chest\s+pain|heart\s+attack|pain\s+in\s+(my\s+)?chest|pressure\s+in\s+(my\s+)?chest)\b", "chest_pain"),
    (r"\b(can'?t\s+breathe|cannot\s+breathe|trouble\s+breathing|shortness\s+of\s+breath|gasping\s+for\s+air|suffocating)\b", "breathing_difficulty"),
    (r"\b(stroke|face\s+(is\s+)?drooping|slurred\s+speech|speech\s+is\s+slurred|paralyzed|numbness\s+on\s+one\s+side)\b", "stroke"),
    (r"\b(severe\s+bleeding|bleeding\s+heavily|gushing\s+blood|hemorrhage|coughing\s+up\s+blood)\b", "severe_bleeding"),
    (r"\b(unconscious|passed\s+out|lost\s+consciousness|unresponsive|not\s+waking\s+up)\b", "unresponsive"),
    (r"\b(overdose|took\s+too\s+many\s+pills|poisoned|drank\s+bleach|poison\s+control)\b", "poison_overdose"),
    (r"\b(throat\s+(is\s+)?closing(\s+up)?|anaphylaxis|(severe\s+)?allergic\s+reaction)\b", "anaphylaxis"),
    (r"\b(suicid(e|al)|want\s+to\s+end\s+(my\s+)?life|kill\s+myself)\b", "crisis"),
]

COMPILED_PATTERNS = [(re.compile(pattern, re.IGNORECASE), name) for pattern, name in EMERGENCY_PATTERNS]

EMERGENCY_RESPONSE_TEXT = (
    "If you are experiencing a medical emergency, please hang up and call 911 immediately. "
    "Our clinic cannot handle emergency situations over this line."
)

CRISIS_RESPONSE_TEXT = (
    "If you or someone you know is in crisis, please call or text 988 immediately to reach the Suicide and Crisis Lifeline. "
    "Help is free, confidential, and available 24/7."
)


@dataclass(frozen=True)
class EmergencyCheckResult:
    is_emergency: bool
    symptom_category: str | None = None
    response_text: str | None = None


def check_medical_emergency(user_text: str) -> EmergencyCheckResult:
    if not user_text:
        return EmergencyCheckResult(is_emergency=False)

    text = user_text.strip()
    for regex, category in COMPILED_PATTERNS:
        if regex.search(text):
            response = CRISIS_RESPONSE_TEXT if category == "crisis" else EMERGENCY_RESPONSE_TEXT
            return EmergencyCheckResult(
                is_emergency=True,
                symptom_category=category,
                response_text=response,
            )

    return EmergencyCheckResult(is_emergency=False)
