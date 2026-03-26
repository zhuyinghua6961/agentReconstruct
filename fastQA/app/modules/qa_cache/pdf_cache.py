from __future__ import annotations

import hashlib
import os
from pathlib import Path

from app.integrations.redis import RedisService
from app.modules.qa_cache.metrics import increment_cache_metric


def _pdf_cache_epoch() -> str:
    return str(os.getenv("QA_CACHE_EPOCH", "0") or "0").strip() or "0"


def _pdf_text_cache_version() -> str:
    return str(os.getenv("PDF_TEXT_CACHE_VERSION", "1") or "1").strip() or "1"


def _pdf_text_cache_ttl_seconds() -> int:
    raw = str(os.getenv("PDF_TEXT_CACHE_TTL_SECONDS", "86400") or "86400").strip()
    try:
        return max(60, int(raw))
    except Exception:
        return 86400


def _file_signature(pdf_path: str) -> str | None:
    path = Path(str(pdf_path or "").strip())
    if not path.exists() or not path.is_file():
        return None
    stat = path.stat()
    source = f"{path.resolve()}|{stat.st_mtime_ns}|{stat.st_size}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def build_pdf_text_cache_key(
    *,
    redis_service: RedisService,
    pdf_path: str,
    max_pages: int,
    exclude_references: bool,
) -> str | None:
    signature = _file_signature(pdf_path)
    if not signature:
        return None
    return redis_service.key_factory.cache(
        "pdftext",
        _pdf_cache_epoch(),
        _pdf_text_cache_version(),
        signature,
        int(max_pages),
        int(bool(exclude_references)),
    )


def build_pdf_text_lock_key(
    *,
    redis_service: RedisService,
    pdf_path: str,
    max_pages: int,
    exclude_references: bool,
) -> str | None:
    signature = _file_signature(pdf_path)
    if not signature:
        return None
    return redis_service.key_factory.lock(
        "pdftext",
        _pdf_cache_epoch(),
        _pdf_text_cache_version(),
        signature,
        int(max_pages),
        int(bool(exclude_references)),
    )


def get_cached_pdf_text(
    *,
    redis_service: RedisService | None,
    pdf_path: str,
    max_pages: int,
    exclude_references: bool,
) -> str | None:
    if redis_service is None or not redis_service.available:
        return None
    key = build_pdf_text_cache_key(
        redis_service=redis_service,
        pdf_path=pdf_path,
        max_pages=max_pages,
        exclude_references=exclude_references,
    )
    if not key:
        return None
    payload = redis_service.get_json(key, default=None)
    if not isinstance(payload, dict):
        return None
    text = payload.get("content")
    return str(text) if isinstance(text, str) and text else None


def cache_pdf_text(
    *,
    redis_service: RedisService | None,
    pdf_path: str,
    max_pages: int,
    exclude_references: bool,
    content: str,
) -> bool:
    if redis_service is None or not redis_service.available:
        return False
    text = str(content or "").strip()
    if not text:
        return False
    key = build_pdf_text_cache_key(
        redis_service=redis_service,
        pdf_path=pdf_path,
        max_pages=max_pages,
        exclude_references=exclude_references,
    )
    if not key:
        return False
    ok = redis_service.set_json(
        key,
        {
            "content": text,
            "max_pages": int(max_pages),
            "exclude_references": bool(exclude_references),
            "cache_epoch": _pdf_cache_epoch(),
            "version": _pdf_text_cache_version(),
        },
        ttl_seconds=_pdf_text_cache_ttl_seconds(),
    )
    if ok:
        increment_cache_metric("pdftext", "cache_write")
    return ok


__all__ = [
    "build_pdf_text_cache_key",
    "build_pdf_text_lock_key",
    "cache_pdf_text",
    "get_cached_pdf_text",
]
