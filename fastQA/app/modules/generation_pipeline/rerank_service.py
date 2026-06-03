#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional rerank service for two-stage retrieval."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from app.integrations.llm.thinking import auth_headers

try:
    import requests
except Exception:  # pragma: no cover - covered by fallback tests via injected module
    requests = None

RERANK_PROVIDER_NAME = "openai_compatible"


def _fallback_result(
    *,
    documents: List[str],
    metadatas: Optional[List[Dict[str, Any]]],
    top_n: int,
    reason: str,
    provider: str,
) -> Dict[str, Any]:
    docs = list(documents[:top_n])
    metas = list((metadatas or [])[:top_n])
    # fallback score follows original order, higher means more relevant
    scores = [1.0 - (idx * 0.01) for idx in range(len(docs))]
    return {
        "documents": docs,
        "metadatas": metas,
        "rerank_scores": scores,
        "fallback": True,
        "fallback_reason": reason,
        "provider": provider,
    }


def _clamp_top_n(top_n: int, document_count: int) -> int:
    return min(max(int(top_n), 1), document_count)


def _rerank_auth_mode() -> str:
    return str(os.getenv("RERANK_AUTH_MODE") or "bearer").strip()


def _build_headers(*, api_key: str, include_auth: bool) -> Dict[str, str]:
    if not include_auth:
        return {"Content-Type": "application/json"}
    return auth_headers(api_key, auth_mode=_rerank_auth_mode())


def _normalize_rerank_endpoint(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        return value
    for suffix in ("/v1/rerank", "/rerank"):
        if value.endswith(suffix):
            value = value[: -len(suffix)].rstrip("/")
            break
    if not value.endswith("/v1"):
        value = value.rstrip("/") + "/v1"
    return value.rstrip("/") + "/rerank"


def rerank_documents(
    *,
    query: str,
    documents: List[str],
    metadatas: Optional[List[Dict[str, Any]]] = None,
    top_n: int = 20,
    provider: str = "dashscope",
    api_key: str = "",
    model: str = "",
    base_url: str = "",
    timeout_seconds: float = 20.0,
    logger: Any = None,
    requests_module: Any = None,
    session: Any = None,
) -> Dict[str, Any]:
    """Rerank candidate docs with graceful fallback."""
    del provider
    if not documents:
        return _fallback_result(
            documents=[],
            metadatas=[],
            top_n=0,
            reason="empty_documents",
            provider=RERANK_PROVIDER_NAME,
        )

    req = session or requests_module or requests
    if req is None:
        return _fallback_result(
            documents=documents,
            metadatas=metadatas,
            top_n=top_n,
            reason="requests_unavailable",
            provider=RERANK_PROVIDER_NAME,
        )

    endpoint = _normalize_rerank_endpoint(base_url)
    if not endpoint or not str(model or "").strip():
        return _fallback_result(
            documents=documents,
            metadatas=metadatas,
            top_n=top_n,
            reason="provider_disabled",
            provider=RERANK_PROVIDER_NAME,
        )

    docs_to_rerank = list(documents)
    metas_to_rerank = list(metadatas or [])
    requested_top_n = _clamp_top_n(top_n, len(docs_to_rerank))
    payload = {
        "model": model,
        "query": query,
        "documents": docs_to_rerank,
        "top_n": requested_top_n,
    }
    headers = _build_headers(api_key=api_key, include_auth=bool(api_key))

    try:
        response = req.post(endpoint, headers=headers, json=payload, timeout=timeout_seconds)
        response.raise_for_status()
        data = response.json() if hasattr(response, "json") else {}
        items = data.get("results", [])

        ranked_docs: List[str] = []
        ranked_metas: List[Dict[str, Any]] = []
        ranked_scores: List[float] = []
        for item in items:
            try:
                idx = int(item.get("index", -1))
            except Exception:
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
                provider=RERANK_PROVIDER_NAME,
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
            try:
                logger.warning(f"rerank failed ({RERANK_PROVIDER_NAME}), fallback to vector order: {exc}")
            except Exception:
                pass
        return _fallback_result(
            documents=documents,
            metadatas=metadatas,
            top_n=top_n,
            reason="request_failed",
            provider=RERANK_PROVIDER_NAME,
        )


__all__ = ["rerank_documents"]
