"""Gate QA generation pipeline Redis cache (Stage1–3 + singleflight locks).

Avoid importing ``generation_pipeline`` here to prevent import cycles via
``qa_kb`` ⇄ ``orchestration`` bootstrap paths.
"""

from __future__ import annotations

import os
from typing import Any

TRUE_VALUES = {"1", "true", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "no", "n", "off"}


def _env_bool_pipeline_cache(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = str(raw).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return default


def resolve_qa_pipeline_cache_redis(redis_service: Any | None) -> Any | None:
    """Strip Redis wrapper when ``QA_PIPELINE_CACHE_ENABLED`` is false.

    Returning ``None`` skips cache reads/writes and ``run_singleflight`` Redis locks
    inside ``GenerationPipelineOrchestrator`` stage runners; Redis remains available
    for other callers.
    """
    if not _env_bool_pipeline_cache("QA_PIPELINE_CACHE_ENABLED", default=True):
        return None
    return redis_service


__all__ = ["resolve_qa_pipeline_cache_redis"]
