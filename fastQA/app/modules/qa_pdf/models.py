from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class PdfQaRequest:
    question: str
    pdf_content: str
    kb_verification: dict[str, Any] | None = None
    stream: bool = False
    first_token_timeout_sec: float | None = None
    is_cancelled: Callable[[], bool] | None = None


@dataclass
class UploadedPdfQaRequest:
    question: str
    pdf_path: str
    pdf_content: str
    performance_mode: str = "balanced"
    allow_kb_verification: bool = False
    turn_mode: str = "file_only"
    cache_key_mode: str = ""
    cache_key_question: str = ""
    trace_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
