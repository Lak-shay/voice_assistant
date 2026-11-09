import datetime

from prompts import build_instructions


def test_name_confirmation_reads_name_before_spelling():
    now = datetime.datetime(2026, 11, 9, 10, 0)
    for phone in ("+15555550100", None):
        text = build_instructions(now, phone)
        assert "repeat it back as a word" in text
        assert "letter by letter only if they say no" in text
