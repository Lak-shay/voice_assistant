from __future__ import annotations

import pytest

from guardrails.output_guardrails import (
    mask_secrets_transform,
    mask_sensitive_secrets,
)


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


@pytest.mark.asyncio
async def test_mask_secrets_transform_masks_secret_split_across_chunks():
    async def token_stream():
        for token in ["Your key is sk", "-lf-ab", "c123456", "789 and ", "LANGFUSE_SECRET", "_KEY is set."]:
            yield token

    result = "".join([c async for c in mask_secrets_transform(token_stream())])
    assert result == "Your key is [REDACTED] and [REDACTED] is set."

