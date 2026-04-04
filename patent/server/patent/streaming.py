from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any, Callable

_DEFAULT_CHUNK_SIZE = 24


def iter_text_output(output: Any) -> Iterator[str]:
    if output is None:
        return
    if isinstance(output, bytes):
        text = output.decode("utf-8", errors="ignore")
        if text:
            yield text
        return
    if isinstance(output, str):
        if output:
            yield output
        return
    if isinstance(output, Iterable):
        for item in output:
            text = str(item or "")
            if text:
                yield text
        return
    text = str(output or "")
    if text:
        yield text


def iter_text_chunks(text: str, *, chunk_size: int = _DEFAULT_CHUNK_SIZE) -> Iterator[str]:
    normalized = str(text or "")
    size = max(1, int(chunk_size))
    for index in range(0, len(normalized), size):
        piece = normalized[index : index + size]
        if piece:
            yield piece


def emit_text_chunks(
    text: str,
    *,
    content_callback: Callable[[str], None] | None,
    chunk_size: int = _DEFAULT_CHUNK_SIZE,
) -> int:
    if not callable(content_callback):
        return 0
    emitted = 0
    for piece in iter_text_chunks(text, chunk_size=chunk_size):
        content_callback(piece)
        emitted += 1
    return emitted
