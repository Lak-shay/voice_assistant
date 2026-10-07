from guardrails.emergency import check_medical_emergency


def test_emergency_detection_chest_pain():
    result = check_medical_emergency("I am having severe chest pain and pressure.")
    assert result.is_emergency is True
    assert result.symptom_category == "chest_pain"
    assert "911" in result.response_text
    assert "**" not in result.response_text


def test_emergency_detection_breathing():
    result = check_medical_emergency("I can't breathe, please help me!")
    assert result.is_emergency is True
    assert result.symptom_category == "breathing_difficulty"
    assert "911" in result.response_text


def test_emergency_detection_stroke():
    result = check_medical_emergency("My father's face is drooping and his speech is slurred.")
    assert result.is_emergency is True
    assert result.symptom_category == "stroke"
    assert "911" in result.response_text


def test_emergency_detection_bleeding():
    result = check_medical_emergency("I have severe bleeding from a deep wound.")
    assert result.is_emergency is True
    assert result.symptom_category == "severe_bleeding"


def test_emergency_detection_unresponsive():
    result = check_medical_emergency("My sister is unconscious and not waking up.")
    assert result.is_emergency is True
    assert result.symptom_category == "unresponsive"


def test_emergency_detection_poison_overdose():
    result = check_medical_emergency("The child took too many pills by accident.")
    assert result.is_emergency is True
    assert result.symptom_category == "poison_overdose"


def test_emergency_detection_anaphylaxis():
    result = check_medical_emergency("My throat is closing up from an allergic reaction.")
    assert result.is_emergency is True
    assert result.symptom_category == "anaphylaxis"


def test_emergency_detection_crisis():
    result = check_medical_emergency("I am feeling suicidal and need help.")
    assert result.is_emergency is True
    assert result.symptom_category == "crisis"
    assert "988" in result.response_text


def test_routine_appointment_non_emergency():
    non_emergencies = [
        "I'd like to book a routine cleaning for next Tuesday.",
        "Do you have any openings on October 15th at 2 PM?",
        "My tooth feels a little sensitive when I drink cold water.",
        "Can I reschedule my checkup with Dr. Smith?",
    ]
    for text in non_emergencies:
        result = check_medical_emergency(text)
        assert result.is_emergency is False
        assert result.response_text is None
