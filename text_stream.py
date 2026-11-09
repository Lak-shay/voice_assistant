from __future__ import annotations

from collections.abc import AsyncIterable, Callable
import re

_LAST_WHITESPACE = re.compile(r"\s(?=\S*\Z)")


async def rewrite_whole_words(
    text: AsyncIterable[str],
    rewrite: Callable[[str], str],
    hold_back: re.Pattern[str] | None = None,
) -> AsyncIterable[str]:
    """Apply rewrite to streamed text one run of whole words at a time.

    The trailing partial word is held until the next whitespace arrives. hold_back, matched
    against the end of the ready text with \\Z, holds back more, for example a clock time
    whose AM or PM may still be in a later chunk.
    """
    pending = ""
    async for chunk in text:
        pending += chunk
        last = _LAST_WHITESPACE.search(pending)
        if not last:
            continue
        end = last.end()
        if hold_back and (held := hold_back.search(pending, 0, end)):
            end = held.start()
        if end:
            yield rewrite(pending[:end])
            pending = pending[end:]
    if pending:
        yield rewrite(pending)
