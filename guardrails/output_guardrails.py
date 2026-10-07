from __future__ import annotations

from collections.abc import AsyncIterable
import re

MARKDOWN_PATTERNS = [
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),
    (re.compile(r"\*([^*]+)\*"), r"\1"),
    (re.compile(r"__([^_]+)__"), r"\1"),
    (re.compile(r"_([^_]+)_"), r"\1"),
    (re.compile(r"`([^`]+)`"), r"\1"),
    (re.compile(r"^#+\s*", re.MULTILINE), ""),
    (re.compile(r"^\s*[-*+]\s+", re.MULTILINE), ""),
    (re.compile(r"^\s*\d+\.\s+", re.MULTILINE), ""),
    (re.compile(r"https?://\S+"), ""),
    (re.compile(r"[\[\]<>]"), ""),
]

SECRET_LEAK_PATTERNS = [
    re.compile(r"\b(sk-lf-[a-zA-Z0-9_-]+|KEY01[a-zA-Z0-9_-]+|API[a-zA-Z0-9_-]{10,})\b"),
    re.compile(r"\b(LIVEKIT_API_[A-Z0-9_]+|LANGFUSE_[A-Z0-9_]+|TELNYX_[A-Z0-9_]+)\b"),
]


def scrub_markdown(text: str) -> str:
    cleaned = text
    for pattern, repl in MARKDOWN_PATTERNS:
        cleaned = pattern.sub(repl, cleaned)
    cleaned = cleaned.replace("*", "").replace("#", "").replace("`", "")
    return re.sub(r"\s+", " ", cleaned).strip()


def mask_sensitive_secrets(text: str) -> str:
    cleaned = text
    for pattern in SECRET_LEAK_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned


async def mask_secrets_transform(text: AsyncIterable[str]) -> AsyncIterable[str]:
    """Streaming text transform suitable for LiveKit AgentSession.tts_text_transforms."""
    async for chunk in text:
        yield mask_sensitive_secrets(chunk)

