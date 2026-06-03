from __future__ import annotations

import os
from typing import Any

from server.patent.thinking import auth_headers

try:
    import requests
except Exception:  # pragma: no cover - dependency guard
    requests = None

RERANK_PROVIDER_NAME = "openai_compatible"


def _fallback_result(
    *,
    documents: list[str],
    metadatas: list[dict[str, Any]] | None,
    top_n: int,
    reason: str,
    provider: str,
) -> dict[str, Any]:
    docs = list(documents[: max(0, int(top_n))])
    metas = list((metadatas or [])[: len(docs)])
    return {
        "documents": docs,
        "metadatas": metas,
        "rerank_scores": [1.0 - (index * 0.01) for index in range(len(docs))],
        "fallback": True,
        "fallback_reason": reason,
        "provider": provider,
    }


def _clamp_top_n(top_n: int, document_count: int) -> int:
    return min(max(int(top_n), 1), max(int(document_count), 1))


def _float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(str(os.getenv(name, default)).strip())
    except Exception:
        value = float(default)
    return max(float(minimum), min(float(maximum), value))


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        raw = str(os.getenv(name, "") or "").strip()
        if raw:
            return raw
    return default


def _rerank_auth_mode() -> str:
    return _first_env("RERANK_AUTH_MODE", default="bearer")


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


def rerank_patent_stage2_documents(
    *,
    query: str,
    documents: list[str],
    metadatas: list[dict[str, Any]] | None = None,
    top_n: int = 20,
    provider: str = "dashscope",
    api_key: str = "",
    model: str = "",
    base_url: str = "",
    timeout_seconds: float = 20.0,
    logger: Any = None,
    requests_module: Any = None,
    session: Any = None,
) -> dict[str, Any]:
    del provider
    if not documents:
        return _fallback_result(documents=[], metadatas=[], top_n=0, reason="empty_documents", provider=RERANK_PROVIDER_NAME)

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
    headers = auth_headers(api_key, auth_mode=_rerank_auth_mode()) if api_key else {"Content-Type": "application/json"}

    try:
        response = req.post(endpoint, headers=headers, json=payload, timeout=float(timeout_seconds))
        response.raise_for_status()
        data = response.json() if hasattr(response, "json") else {}
        items = data.get("results", [])
    except Exception as exc:
        if logger is not None:
            try:
                logger.warning("patent stage2 rerank request failed provider=%s error=%s", RERANK_PROVIDER_NAME, exc)
            except Exception:
                pass
        return _fallback_result(
            documents=documents,
            metadatas=metadatas,
            top_n=top_n,
            reason="request_failed",
            provider=RERANK_PROVIDER_NAME,
        )

    ranked_docs: list[str] = []
    ranked_metas: list[dict[str, Any]] = []
    ranked_scores: list[float] = []
    for item in list(items or []):
        try:
            index = int(item.get("index", -1))
        except Exception:
            continue
        if index < 0 or index >= len(docs_to_rerank):
            continue
        ranked_docs.append(docs_to_rerank[index])
        if index < len(metas_to_rerank):
            ranked_metas.append(metas_to_rerank[index])
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


def build_patent_stage2_rerank_fn(*, logger: Any = None, requests_module: Any = None, session: Any = None):
    provider = RERANK_PROVIDER_NAME
    api_key = _first_env("RERANK_API_KEY", "PATENT_STAGE2_RERANK_API_KEY")
    base_url = _first_env("RERANK_BASE_URL", "PATENT_STAGE2_RERANK_BASE_URL")
    model = _first_env("RERANK_MODEL", "PATENT_STAGE2_RERANK_MODEL")
    if not base_url or not model:
        return None
    timeout_env = "RERANK_TIMEOUT_SECONDS" if _first_env("RERANK_TIMEOUT_SECONDS") else "PATENT_STAGE2_RERANK_TIMEOUT_SECONDS"
    timeout_seconds = _float_env(timeout_env, 20.0, minimum=0.5, maximum=300.0)

    def _rerank_fn(**kwargs):
        return rerank_patent_stage2_documents(
            query=str(kwargs.get("query") or ""),
            documents=list(kwargs.get("documents") or []),
            metadatas=[dict(item) for item in list(kwargs.get("metadatas") or []) if isinstance(item, dict)],
            top_n=int(kwargs.get("top_n") or 20),
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            logger=logger,
            requests_module=requests_module,
            session=session,
        )

    return _rerank_fn
