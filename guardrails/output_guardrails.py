from __future__ import annotations

from collections.abc import AsyncIterable
import re

SECRET_LEAK_PATTERNS = [
    re.compile(r"\b(sk-lf-[a-zA-Z0-9_-]+|KEY01[a-zA-Z0-9_-]+|API[a-zA-Z0-9_-]{10,})\b"),
    re.compile(r"\b(LIVEKIT_API_[A-Z0-9_]+|LANGFUSE_[A-Z0-9_]+|TELNYX_[A-Z0-9_]+)\b"),
]


_LAST_WHITESPACE = re.compile(r"\s(?=\S*\Z)")


def mask_sensitive_secrets(text: str) -> str:
    cleaned = text
    for pattern in SECRET_LEAK_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned


async def mask_secrets_transform(text: AsyncIterable[str]) -> AsyncIterable[str]:
    """Streaming text transform suitable for LiveKit AgentSession.tts_text_transforms.

    LLM tokens can split a secret across chunks, so the trailing partial word is held
    back until the next whitespace arrives and only whole words are masked and emitted.
    """
    pending = ""
    async for chunk in text:
        pending += chunk
        match = _LAST_WHITESPACE.search(pending)
        if match:
            yield mask_sensitive_secrets(pending[: match.end()])
            pending = pending[match.end():]
    if pending:
        yield mask_sensitive_secrets(pending)

