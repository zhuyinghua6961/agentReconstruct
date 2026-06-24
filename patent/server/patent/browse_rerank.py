from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

from server.patent.rerank_service import build_patent_stage2_rerank_fn, rerank_patent_stage2_documents

_LOGGER = logging.getLogger("patent.browse_rerank")


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    return default


def patent_browse_rerank_configured() -> bool:
    base_url = _first_env("RERANK_BASE_URL", "PATENT_STAGE2_RERANK_BASE_URL")
    model = _first_env("RERANK_MODEL", "PATENT_STAGE2_RERANK_MODEL")
    return bool(base_url and model)


def patent_browse_rerank_enabled() -> bool:
    if not patent_browse_rerank_configured():
        return False
    return _env_bool("PATENT_SEARCH_RERANK_ENABLED", True)


def patent_browse_rerank_candidates(*, limit: int) -> int:
    raw = str(os.getenv("PATENT_SEARCH_RERANK_CANDIDATES", "") or "").strip()
    if raw:
        try:
            return max(int(limit), min(int(raw), 80))
        except ValueError:
            pass
    return max(int(limit) * 3, 30)


def _preview_query(query: str, *, max_chars: int = 80) -> str:
    text = re.sub(r"\s+", " ", str(query or "").strip())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _preview_patent_ids(items: list[dict[str, Any]], *, limit: int = 5) -> str:
    ids = [
        str(item.get("canonical_patent_id") or "").strip()
        for item in list(items or [])
        if str(item.get("canonical_patent_id") or "").strip()
    ]
    if not ids:
        return "-"
    head = ",".join(ids[:limit])
    if len(ids) > limit:
        return f"{head},+{len(ids) - limit}"
    return head


def item_rerank_text(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "").strip()
    abstract = str(item.get("abstract") or item.get("snippet") or "").strip()
    patent_id = str(item.get("canonical_patent_id") or "").strip()
    if title and abstract:
        return f"{title}\n{abstract[:600]}"
    if title:
        return title
    if abstract:
        return abstract[:600]
    return patent_id or "unknown"


def apply_patent_browse_rerank(
    *,
    query: str,
    items: list[dict[str, Any]],
    limit: int,
    logger: Any | None = None,
    rerank_fn: Any | None = None,
    context: str = "topic",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    log = logger or _LOGGER
    started_at = time.perf_counter()
    configured = patent_browse_rerank_configured()
    enabled = patent_browse_rerank_enabled()
    metadata: dict[str, Any] = {
        "enabled": enabled,
        "applied": False,
        "fallback": False,
    }
    candidate_ids = _preview_patent_ids(items)

    if not configured:
        log.info(
            "patent_search rerank skipped context=%s reason=not_configured "
            "query=%r candidate_count=%s limit=%s",
            context,
            _preview_query(query),
            len(items),
            limit,
        )
        return items[:limit], metadata

    if not enabled:
        log.info(
            "patent_search rerank skipped context=%s reason=disabled_by_env "
            "env=PATENT_SEARCH_RERANK_ENABLED query=%r candidate_count=%s",
            context,
            _preview_query(query),
            len(items),
        )
        return items[:limit], metadata

    if len(items) <= 1:
        log.info(
            "patent_search rerank skipped context=%s reason=insufficient_candidates "
            "query=%r candidate_count=%s limit=%s",
            context,
            _preview_query(query),
            len(items),
            limit,
        )
        return items[:limit], metadata

    model = _first_env("RERANK_MODEL", "PATENT_STAGE2_RERANK_MODEL")
    base_url = _first_env("RERANK_BASE_URL", "PATENT_STAGE2_RERANK_BASE_URL")
    log.info(
        "patent_search rerank start context=%s model=%s endpoint=%s query=%r "
        "candidate_count=%s top_n=%s candidate_ids=%s",
        context,
        model,
        base_url,
        _preview_query(query),
        len(items),
        limit,
        candidate_ids,
    )

    documents = [item_rerank_text(item) for item in items]
    fn = rerank_fn or build_patent_stage2_rerank_fn(logger=log)
    if fn is None:
        result = rerank_patent_stage2_documents(
            query=query,
            documents=documents,
            metadatas=[dict(item) for item in items],
            top_n=limit,
            api_key=_first_env("RERANK_API_KEY", "PATENT_STAGE2_RERANK_API_KEY"),
            base_url=base_url,
            model=model,
            logger=log,
        )
    else:
        result = fn(
            query=query,
            documents=documents,
            metadatas=[dict(item) for item in items],
            top_n=limit,
        )

    if bool(result.get("fallback")):
        reason = str(result.get("fallback_reason") or "unknown")
        metadata["fallback"] = True
        metadata["fallback_reason"] = reason
        log.warning(
            "patent_search rerank fallback context=%s model=%s reason=%s query=%r "
            "candidate_count=%s elapsed_ms=%.2f candidate_ids=%s",
            context,
            model,
            reason,
            _preview_query(query),
            len(items),
            (time.perf_counter() - started_at) * 1000.0,
            candidate_ids,
        )
        return items[:limit], metadata

    ranked_items: list[dict[str, Any]] = []
    scores = list(result.get("rerank_scores") or [])
    for index, hit in enumerate(list(result.get("metadatas") or [])):
        if not isinstance(hit, dict):
            continue
        ordered = dict(hit)
        if index < len(scores):
            ordered["match_score"] = round(float(scores[index]), 6)
        ordered["match_source"] = "patent_rerank"
        ordered["match_mode"] = "semantic"
        ranked_items.append(ordered)

    if not ranked_items:
        metadata["fallback"] = True
        metadata["fallback_reason"] = "empty_rerank_mapping"
        log.warning(
            "patent_search rerank fallback context=%s model=%s reason=empty_rerank_mapping "
            "query=%r candidate_count=%s elapsed_ms=%.2f",
            context,
            model,
            _preview_query(query),
            len(items),
            (time.perf_counter() - started_at) * 1000.0,
        )
        return items[:limit], metadata

    metadata["applied"] = True
    top_score = ranked_items[0].get("match_score")
    log.info(
        "patent_search rerank success context=%s model=%s selected=%s top_score=%s "
        "query=%r elapsed_ms=%.2f ranked_ids=%s",
        context,
        model,
        len(ranked_items),
        top_score,
        _preview_query(query),
        (time.perf_counter() - started_at) * 1000.0,
        _preview_patent_ids(ranked_items),
    )
    return ranked_items[:limit], metadata
