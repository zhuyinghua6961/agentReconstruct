"""Shared SSE frame buffering and JSON payload parsing helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class SSEFrameBuffer:
    _buffer: str = field(default="", init=False)

    def feed(self, chunk: bytes) -> list[str]:
        if not chunk:
            return []
        try:
            text = chunk.decode("utf-8")
        except UnicodeDecodeError:
            text = chunk.decode("utf-8", errors="ignore")
        self._buffer += text.replace("\r\n", "\n")
        frames: list[str] = []
        while "\n\n" in self._buffer:
            frame, self._buffer = self._buffer.split("\n\n", 1)
            frames.append(frame)
        return frames

    def flush(self) -> str | None:
        if not self._buffer.strip():
            self._buffer = ""
            return None
        frame = self._buffer
        self._buffer = ""
        return frame


def parse_sse_json_frame(frame: str) -> tuple[dict | None, list[str]]:
    raw_lines = [line for line in str(frame or "").splitlines() if str(line).strip()]
    lines = [line.strip() for line in raw_lines]
    data_lines = [line[5:].strip() for line in lines if line.startswith("data:")]
    prefix_lines = [line for line in raw_lines if not str(line).strip().startswith("data:")]
    if not data_lines:
        return None, prefix_lines
    try:
        payload = json.loads("\n".join(data_lines))
    except Exception:
        return None, prefix_lines
    return (payload if isinstance(payload, dict) else None), prefix_lines
