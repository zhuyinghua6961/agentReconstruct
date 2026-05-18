from __future__ import annotations

import os
from typing import Any

try:
    import requests
except Exception:  # pragma: no cover - dependency guard
    requests = None


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


def rerank_patent_stage2_documents(
    *,
    query: str,
    documents: list[str],
    metadatas: list[dict[str, Any]] | None = None,
    top_n: int = 20,
    provider: str = "dashscope",
    api_key: str = "",
    model: str = "qwen3-vl-rerank",
    base_url: str = "",
    timeout_seconds: float = 20.0,
    logger: Any = None,
    requests_module: Any = None,
    session: Any = None,
) -> dict[str, Any]:
    if not documents:
        return _fallback_result(documents=[], metadatas=[], top_n=0, reason="empty_documents", provider=provider)

    provider_norm = str(provider or "none").strip().lower()
    if provider_norm in {"none", "off", "disabled"}:
        return _fallback_result(
            documents=documents,
            metadatas=metadatas,
            top_n=top_n,
            reason="provider_disabled",
            provider=provider_norm,
        )

    req = session or requests_module or requests
    if req is None:
        return _fallback_result(
            documents=documents,
            metadatas=metadatas,
            top_n=top_n,
            reason="requests_unavailable",
            provider=provider_norm,
        )
    if provider_norm not in {"dashscope", "local"}:
        return _fallback_result(
            documents=documents,
            metadatas=metadatas,
            top_n=top_n,
            reason="provider_unsupported",
            provider=provider_norm,
        )
    if provider_norm == "dashscope" and not str(api_key or "").strip():
        return _fallback_result(
            documents=documents,
            metadatas=metadatas,
            top_n=top_n,
            reason="api_key_missing",
            provider=provider_norm,
        )

    docs_to_rerank = list(documents)
    metas_to_rerank = list(metadatas or [])
    requested_top_n = _clamp_top_n(top_n, len(docs_to_rerank))
    if provider_norm == "local":
        endpoint = str(base_url or "http://localhost:8084").rstrip("/") + "/v1/rerank"
        payload = {
            "model": model,
            "query": query,
            "documents": docs_to_rerank,
            "top_n": requested_top_n,
            "return_documents": True,
        }
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    else:
        endpoint = (
            str(base_url or "https://dashscope.aliyuncs.com").rstrip("/")
            + "/api/v1/services/rerank/text-rerank/text-rerank"
        )
        payload = {
            "model": model,
            "input": {
                "query": query,
                "documents": docs_to_rerank,
            },
            "parameters": {
                "return_documents": False,
                "top_n": requested_top_n,
            },
        }
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

    try:
        response = req.post(endpoint, headers=headers, json=payload, timeout=float(timeout_seconds))
        response.raise_for_status()
        data = response.json() if hasattr(response, "json") else {}
        items = data.get("results", []) if provider_norm == "local" else data.get("output", {}).get("results", [])
    except Exception as exc:
        if logger is not None:
            try:
                logger.warning("patent stage2 rerank request failed provider=%s error=%s", provider_norm, exc)
            except Exception:
                pass
        return _fallback_result(
            documents=documents,
            metadatas=metadatas,
            top_n=top_n,
            reason="request_failed",
            provider=provider_norm,
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


def build_patent_stage2_rerank_fn(*, logger: Any = None, requests_module: Any = None, session: Any = None):
    provider = _first_env("RERANK_PROVIDER", default="none").lower()
    if provider in {"", "none", "off", "disabled"}:
        return None
    raw_api_key = _first_env("RERANK_API_KEY")
    api_key = raw_api_key
    default_base_url = "http://localhost:8084" if provider == "local" else "https://dashscope.aliyuncs.com"
    base_url = _first_env("RERANK_BASE_URL", default=default_base_url)
    model = _first_env("RERANK_MODEL", default="qwen3-vl-rerank")
    timeout_seconds = _float_env("RERANK_TIMEOUT_SECONDS", 20.0, minimum=0.5, maximum=300.0)

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
