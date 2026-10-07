from __future__ import annotations

import re
from dataclasses import dataclass

from guardrails.emergency import check_medical_emergency

INJECTION_PATTERNS = [
    r"\b(ignore|disregard|forget)\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts|rules)\b",
    r"\b(system\s+prompt|system\s+instructions|reveal\s+your\s+prompt|show\s+your\s+prompt|what\s+are\s+your\s+instructions)\b",
    r"\b(you\s+are\s+now|act\s+as\s+a(n)?|pretend\s+you\s+are|roleplay\s+as)\b",
    r"\b(jailbreak|dan\s+mode|developer\s+mode|bypass\s+filters?|unrestricted\s+mode)\b",
    r"\b(repeat\s+everything\s+(above|prior)|output\s+initial\s+prompt|system\s+override)\b",
]

MEDICAL_ADVICE_PATTERNS = [
    r"\b(can\s+you\s+prescribe|give\s+me\s+a\s+prescription|prescribe\s+me|what\s+dosage\s+of|how\s+many\s+mg\s+of)\b",
    r"\b(diagnose\s+(me|my)|do\s+I\s+have\s+(cancer|covid|diabetes|an\s+infection)|what\s+disease\s+is\s+this)\b",
    r"\b(interpret\s+(my\s+)?(lab|blood(\s+test)?|x-?ray|mri|biopsy)\s+results?)\b",
]

COMPILED_INJECTIONS = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]
COMPILED_MEDICAL_ADVICE = [re.compile(p, re.IGNORECASE) for p in MEDICAL_ADVICE_PATTERNS]

INJECTION_REFUSAL_TEXT = (
    "I am only able to assist with scheduling appointments for the clinic. "
    "Would you like to book or check an appointment time?"
)

MEDICAL_ADVICE_REFUSAL_TEXT = (
    "I am an appointment assistant and cannot provide medical advice or prescriptions. "
    "Would you like to schedule an appointment with one of our doctors?"
)


@dataclass(frozen=True)
class InputGuardrailResult:
    is_blocked: bool
    reason: str | None = None
    response_text: str | None = None


def check_input_guardrails(
    user_text: str,
    check_emergency: bool = True,
    check_injection: bool = True,
) -> InputGuardrailResult:
    if not user_text:
        return InputGuardrailResult(is_blocked=False)

    text = user_text.strip()

    if check_emergency:
        emergency_res = check_medical_emergency(text)
        if emergency_res.is_emergency:
            return InputGuardrailResult(
                is_blocked=True,
                reason=f"medical_emergency:{emergency_res.symptom_category}",
                response_text=emergency_res.response_text,
            )

    if check_injection:
        for pattern in COMPILED_INJECTIONS:
            if pattern.search(text):
                return InputGuardrailResult(
                    is_blocked=True,
                    reason="prompt_injection",
                    response_text=INJECTION_REFUSAL_TEXT,
                )

    for pattern in COMPILED_MEDICAL_ADVICE:
        if pattern.search(text):
            return InputGuardrailResult(
                is_blocked=True,
                reason="out_of_scope_medical_advice",
                response_text=MEDICAL_ADVICE_REFUSAL_TEXT,
            )

    return InputGuardrailResult(is_blocked=False)
