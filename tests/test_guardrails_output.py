from __future__ import annotations

import pytest

from guardrails.output_guardrails import (
    mask_secrets_transform,
    mask_sensitive_secrets,
    scrub_markdown,
)


def test_scrub_markdown():
    text = "**Welcome** to *our* clinic! Please visit https://example.com/clinic for #appointments."
    cleaned = scrub_markdown(text)
    assert "**" not in cleaned
    assert "*" not in cleaned
    assert "#" not in cleaned
    assert "https://" not in cleaned
    assert "Welcome to our clinic!" in cleaned


def test_mask_sensitive_secrets():
    text = "Connecting with key sk-lf-1234567890abcdef and KEY01A102677AB52B193A8582."
    masked = mask_sensitive_secrets(text)
    assert "sk-lf-" not in masked
    assert "KEY01" not in masked
    assert "[REDACTED]" in masked


@pytest.mark.asyncio
async def test_mask_secrets_transform():
    async def sample_stream():
        yield "Your API key is "
        yield "sk-lf-secret123456789 "
        yield "and keep it safe."

    chunks = [c async for c in mask_secrets_transform(sample_stream())]
    result = "".join(chunks)
    assert "sk-lf-secret" not in result
    assert "[REDACTED]" in result
    assert "and keep it safe." in result

