from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

_LOGGER = logging.getLogger(__name__)
RERANK_PROVIDER_NAME = "openai_compatible"


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    return default


def _rerank_auth_mode() -> str:
    return _first_env("RERANK_AUTH_MODE", default="bearer").lower()


def _normalize_bearer_api_key(api_key: str) -> str:
    value = str(api_key or "").strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return value


def _auth_headers(api_key: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    mode = _rerank_auth_mode()
    key = _normalize_bearer_api_key(api_key)
    if not key:
        return headers
    if mode == "authorization":
        headers["Authorization"] = key
    else:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _normalize_rerank_endpoint(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        return ""
    for suffix in ("/v1/rerank", "/rerank"):
        if value.endswith(suffix):
            value = value[: -len(suffix)].rstrip("/")
            break
    if not value.endswith("/v1"):
        value = value.rstrip("/") + "/v1"
    return value.rstrip("/") + "/rerank"


def rerank_configured() -> bool:
    return bool(_first_env("RERANK_BASE_URL") and _first_env("RERANK_MODEL"))


def rerank_candidate_limit(final_limit: int) -> int:
    final = max(1, int(final_limit or 1))
    if not rerank_configured():
        return final
    return min(50, max(final, final * 3))


def _fallback_result(
    *,
    documents: list[str],
    metadatas: list[dict[str, Any]] | None,
    top_n: int,
    reason: str,
) -> dict[str, Any]:
    docs = list(documents[:top_n])
    metas = list((metadatas or [])[:top_n])
    scores = [1.0 - (idx * 0.01) for idx in range(len(docs))]
    return {
        "documents": docs,
        "metadatas": metas,
        "rerank_scores": scores,
        "fallback": True,
        "fallback_reason": reason,
        "provider": RERANK_PROVIDER_NAME,
    }


def rerank_documents(
    *,
    query: str,
    documents: list[str],
    metadatas: list[dict[str, Any]] | None = None,
    top_n: int = 20,
    timeout_seconds: float = 20.0,
    logger: Any | None = None,
) -> dict[str, Any]:
    if not documents:
        return _fallback_result(documents=[], metadatas=[], top_n=0, reason="empty_documents")

    base_url = _first_env("RERANK_BASE_URL")
    model = _first_env("RERANK_MODEL")
    api_key = _first_env("RERANK_API_KEY")
    endpoint = _normalize_rerank_endpoint(base_url)
    if not endpoint or not model:
        return _fallback_result(
            documents=documents,
            metadatas=metadatas,
            top_n=top_n,
            reason="provider_disabled",
        )

    docs_to_rerank = list(documents)
    metas_to_rerank = list(metadatas or [])
    requested_top_n = min(max(int(top_n), 1), len(docs_to_rerank))
    payload = {
        "model": model,
        "query": query,
        "documents": docs_to_rerank,
        "top_n": requested_top_n,
    }
    headers = _auth_headers(api_key)
    started_at = time.perf_counter()
    try:
        response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout_seconds)
        response.raise_for_status()
        data = response.json() if hasattr(response, "json") else {}
        items = data.get("results", []) if isinstance(data, dict) else []

        ranked_docs: list[str] = []
        ranked_metas: list[dict[str, Any]] = []
        ranked_scores: list[float] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("index", -1))
            except (TypeError, ValueError):
                continue
            if idx < 0 or idx >= len(docs_to_rerank):
                continue
            ranked_docs.append(docs_to_rerank[idx])
            if idx < len(metas_to_rerank):
                ranked_metas.append(metas_to_rerank[idx])
            ranked_scores.append(float(item.get("relevance_score", 0.0)))
            if len(ranked_docs) >= requested_top_n:
                break

        if not ranked_docs:
            return _fallback_result(
                documents=documents,
                metadatas=metadatas,
                top_n=top_n,
                reason="empty_rerank_result",
            )

        _LOGGER.info(
            "literature_search rerank success model=%s selected=%s elapsed_ms=%.2f",
            model,
            len(ranked_docs),
            (time.perf_counter() - started_at) * 1000.0,
        )
        return {
            "documents": ranked_docs,
            "metadatas": ranked_metas,
            "rerank_scores": ranked_scores,
            "fallback": False,
            "fallback_reason": "",
            "provider": RERANK_PROVIDER_NAME,
        }
    except Exception as exc:
        if logger is not None:
            logger.warning("literature_search rerank failed, fallback to original order: %s", exc)
        _LOGGER.warning(
            "literature_search rerank failed model=%s elapsed_ms=%.2f error=%s",
            model,
            (time.perf_counter() - started_at) * 1000.0,
            type(exc).__name__,
        )
        return _fallback_result(
            documents=documents,
            metadatas=metadatas,
            top_n=top_n,
            reason="request_failed",
        )
