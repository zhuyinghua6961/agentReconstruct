from __future__ import annotations

import os
from typing import Any


class RemoteEmbeddingClient:
    def __init__(self, api_url: str, requests_module: Any):
        self.api_url = api_url
        self.requests = requests_module

    def encode(self, texts: list[str]):
        payload: dict[str, Any] = {"input": texts}
        model_name = str(os.getenv("EMBEDDING_API_MODEL", "") or os.getenv("EMBEDDING_MODEL_NAME", "")).strip()
        if model_name:
            payload["model"] = model_name

        try:
            timeout_seconds = float(str(os.getenv("EMBEDDING_API_TIMEOUT_SECONDS", "120") or "120").strip())
        except Exception:
            timeout_seconds = 120.0
        response = self.requests.post(self.api_url, json=payload, timeout=timeout_seconds)
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
