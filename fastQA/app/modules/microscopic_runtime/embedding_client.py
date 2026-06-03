from __future__ import annotations

import os
from typing import Any

from app.integrations.llm.thinking import auth_headers


def _embedding_auth_mode() -> str:
    return str(os.getenv("EMBEDDING_AUTH_MODE") or os.getenv("QA_EMBEDDING_AUTH_MODE") or "bearer").strip()


def _normalize_embedding_endpoint(api_url: str) -> str:
    value = str(api_url or "").strip().rstrip("/")
    if not value:
        return value
    for suffix in ("/v1/embeddings", "/embeddings"):
        if value.endswith(suffix):
            value = value[: -len(suffix)].rstrip("/")
            break
    if not value.endswith("/v1"):
        value = value.rstrip("/") + "/v1"
    return value.rstrip("/") + "/embeddings"


class RemoteEmbeddingClient:
    def __init__(self, api_url: str, requests_module: Any):
        self.api_url = _normalize_embedding_endpoint(api_url)
        self.requests = requests_module

    def encode(self, texts: list[str]):
        payload: dict[str, Any] = {"input": texts}
        model_name = str(os.getenv("EMBEDDING_API_MODEL", "") or os.getenv("EMBEDDING_MODEL_NAME", "")).strip()
        if model_name:
            payload["model"] = model_name
        api_key = str(os.getenv("EMBEDDING_API_KEY", "") or "").strip()
        headers = auth_headers(api_key, auth_mode=_embedding_auth_mode())

        try:
            timeout_seconds = float(str(os.getenv("EMBEDDING_API_TIMEOUT_SECONDS", "120") or "120").strip())
        except Exception:
            timeout_seconds = 120.0
        response = self.requests.post(self.api_url, json=payload, timeout=timeout_seconds, headers=headers or None)
        response.raise_for_status()
        result = response.json()
        embeddings: list[list[float]]
        if isinstance(result, dict) and isinstance(result.get("data"), list):
            embeddings = [item["embedding"] for item in result["data"] if isinstance(item, dict) and "embedding" in item]
        elif isinstance(result, dict) and isinstance(result.get("embeddings"), list):
            embeddings = result["embeddings"]
        elif isinstance(result, dict) and isinstance(result.get("embedding"), list):
            embeddings = [result["embedding"]]
        else:
            raise ValueError(f"Unsupported embedding response shape from {self.api_url}")

        import numpy as np

        return np.array(embeddings)
