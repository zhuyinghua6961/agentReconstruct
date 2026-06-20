from __future__ import annotations

import os
from typing import Any

import requests

FASTQA_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages:"


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    return default


def _normalize_auth_mode(value: str) -> str:
    return str(value or "bearer").strip().lower()


def _normalize_embedding_endpoint(base_url: str) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        return ""
    if value.endswith("/v1/embeddings"):
        return value
    if value.endswith("/embeddings"):
        return value
    if value.lower().endswith("/v1"):
        return f"{value}/embeddings"
    return f"{value}/v1/embeddings"


def _build_headers(*, api_key: str, auth_mode: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    mode = _normalize_auth_mode(auth_mode)
    if mode == "none":
        return headers
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _extract_embedding_vector(payload: dict[str, Any]) -> list[float]:
    data = payload.get("data")
    if isinstance(data, list) and data:
        first = data[0] if isinstance(data[0], dict) else {}
        embedding = first.get("embedding")
        if isinstance(embedding, list) and embedding:
            return [float(item) for item in embedding]
    embedding = payload.get("embedding")
    if isinstance(embedding, list) and embedding:
        return [float(item) for item in embedding]
    raise RuntimeError("embedding response missing vector")


def _post_embedding(*, endpoint: str, model: str, text: str, api_key: str, auth_mode: str) -> list[float]:
    response = requests.post(
        endpoint,
        headers=_build_headers(api_key=api_key, auth_mode=auth_mode),
        json={"model": model, "input": text},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("embedding response is not a JSON object")
    return _extract_embedding_vector(payload)


def embed_fastqa_query(text: str) -> list[float]:
    base_url = _first_env("QA_EMBEDDING_BASE_URL", "EMBEDDING_API_URL")
    model = _first_env("QA_EMBEDDING_MODEL", "EMBEDDING_API_MODEL", "EMBEDDING_MODEL_NAME")
    api_key = _first_env("QA_EMBEDDING_API_KEY", "EMBEDDING_API_KEY")
    auth_mode = _first_env("QA_EMBEDDING_AUTH_MODE", "EMBEDDING_AUTH_MODE", default="bearer")
    endpoint = _normalize_embedding_endpoint(base_url)
    if not endpoint or not model:
        raise RuntimeError("fastqa embedding endpoint is not configured")
    query_text = f"{FASTQA_QUERY_INSTRUCTION} {text}".strip()
    return _post_embedding(
        endpoint=endpoint,
        model=model,
        text=query_text,
        api_key=api_key,
        auth_mode=auth_mode,
    )


def embed_highthinking_query(text: str) -> list[float]:
    base_url = _first_env("HIGHTHINKINGQA_EMBEDDING_BASE_URL", default="http://127.0.0.1:8014/v1")
    model = _first_env("HIGHTHINKINGQA_EMBEDDING_MODEL", default="qwen3-embedding-8b")
    api_key = _first_env("HIGHTHINKINGQA_EMBEDDING_API_KEY", "EMBEDDING_API_KEY")
    auth_mode = _first_env("HIGHTHINKINGQA_EMBEDDING_AUTH_MODE", "EMBEDDING_AUTH_MODE", default="bearer")
    endpoint = _normalize_embedding_endpoint(base_url)
    if not endpoint or not model:
        raise RuntimeError("highthinking embedding endpoint is not configured")
    return _post_embedding(
        endpoint=endpoint,
        model=model,
        text=text,
        api_key=api_key,
        auth_mode=auth_mode,
    )
