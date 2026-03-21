#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional rerank service for two-stage retrieval."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

try:
    import requests
except Exception:  # pragma: no cover - covered by fallback tests via injected module
    requests = None


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


def rerank_documents(
    *,
    query: str,
    documents: List[str],
    metadatas: Optional[List[Dict[str, Any]]] = None,
    top_n: int = 20,
    provider: str = "dashscope",
    api_key: str = "",
    model: str = "qwen3-vl-rerank",
    base_url: str = "https://dashscope.aliyuncs.com",
    timeout_seconds: float = 20.0,
    logger: Any = None,
    requests_module: Any = None,
) -> Dict[str, Any]:
    """Rerank candidate docs with graceful fallback."""
    if not documents:
        return _fallback_result(
            documents=[],
            metadatas=[],
            top_n=0,
            reason="empty_documents",
            provider=provider,
        )

    provider_norm = str(provider or "none").strip().lower()
    if provider_norm in {"none", "off", "disabled"}:
        return _fallback_result(
            documents=documents,
            metadatas=metadatas,
            top_n=top_n,
            reason="provider_disabled",
            provider=provider_norm,
        )

    req = requests_module or requests
    if req is None:
        return _fallback_result(
            documents=documents,
            metadatas=metadatas,
            top_n=top_n,
            reason="requests_unavailable",
            provider=provider_norm,
        )

    if provider_norm != "dashscope":
        return _fallback_result(
            documents=documents,
            metadatas=metadatas,
            top_n=top_n,
            reason="provider_unsupported",
            provider=provider_norm,
        )

    if not api_key:
        return _fallback_result(
            documents=documents,
            metadatas=metadatas,
            top_n=top_n,
            reason="api_key_missing",
            provider=provider_norm,
        )

    docs_to_rerank = list(documents)
    metas_to_rerank = list(metadatas or [])
    endpoint = str(base_url or "https://dashscope.aliyuncs.com").rstrip("/") + "/api/v1/services/rerank/text-rerank/text-rerank"
    payload = {
        "model": model,
        "input": {
            "query": query,
            "documents": docs_to_rerank,
        },
        "parameters": {
            "return_documents": False,
            "top_n": min(max(int(top_n), 1), len(docs_to_rerank)),
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        response = req.post(endpoint, headers=headers, json=payload, timeout=timeout_seconds)
        response.raise_for_status()
        data = response.json() if hasattr(response, "json") else {}
        items = data.get("output", {}).get("results", [])

        ranked_docs: List[str] = []
        ranked_metas: List[Dict[str, Any]] = []
        ranked_scores: List[float] = []
        for item in items:
            idx = int(item.get("index", -1))
            if idx < 0 or idx >= len(docs_to_rerank):
                continue
            ranked_docs.append(docs_to_rerank[idx])
            if idx < len(metas_to_rerank):
                ranked_metas.append(metas_to_rerank[idx])
            ranked_scores.append(float(item.get("relevance_score", 0.0)))

        if not ranked_docs:
            return _fallback_result(
                documents=documents,
                metadatas=metadatas,
                top_n=top_n,
                reason="empty_rerank_result",
                provider=provider_norm,
            )

        return {
            "documents": ranked_docs,
            "metadatas": ranked_metas,
            "rerank_scores": ranked_scores,
            "fallback": False,
            "fallback_reason": "",
            "provider": provider_norm,
        }
    except Exception as exc:
        if logger is not None:
            try:
                logger.warning(f"rerank failed ({provider_norm}), fallback to vector order: {exc}")
            except Exception:
                pass
        return _fallback_result(
            documents=documents,
            metadatas=metadatas,
            top_n=top_n,
            reason="request_failed",
            provider=provider_norm,
        )


__all__ = ["rerank_documents"]
