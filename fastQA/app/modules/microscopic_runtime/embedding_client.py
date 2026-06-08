from __future__ import annotations

import logging
import os
import time
from typing import Any

from app.integrations.llm.thinking import auth_headers

_LOGGER = logging.getLogger(__name__)


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
        auth_mode = _embedding_auth_mode()
        headers = auth_headers(api_key, auth_mode=auth_mode)

        try:
            timeout_seconds = float(str(os.getenv("EMBEDDING_API_TIMEOUT_SECONDS", "120") or "120").strip())
        except Exception:
            timeout_seconds = 120.0
        started_at = time.perf_counter()
        _LOGGER.info(
            "model_call start service=fastQA component=embedding model=%s endpoint=%s auth_mode=%s "
            "input_count=%s input_chars=%s timeout_seconds=%s key_present=%s",
            model_name or "",
            self.api_url,
            auth_mode,
            len(texts),
            sum(len(str(text or "")) for text in texts),
            timeout_seconds,
            bool(api_key),
        )
        try:
            response = self.requests.post(self.api_url, json=payload, timeout=timeout_seconds, headers=headers or None)
            response.raise_for_status()
            result = response.json()
        except Exception as exc:
            _LOGGER.warning(
                "model_call failed service=fastQA component=embedding model=%s endpoint=%s auth_mode=%s "
                "elapsed_ms=%.2f error_type=%s",
                model_name or "",
                self.api_url,
                auth_mode,
                (time.perf_counter() - started_at) * 1000.0,
                type(exc).__name__,
            )
            raise
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

        output = np.array(embeddings)
        embedding_dim = int(output.shape[1]) if len(output.shape) >= 2 and output.shape[0] else 0
        _LOGGER.info(
            "model_call success service=fastQA component=embedding model=%s endpoint=%s auth_mode=%s "
            "status_code=%s elapsed_ms=%.2f embedding_count=%s embedding_dim=%s",
            model_name or "",
            self.api_url,
            auth_mode,
            getattr(response, "status_code", None),
            (time.perf_counter() - started_at) * 1000.0,
            int(output.shape[0]) if len(output.shape) >= 1 else 0,
            embedding_dim,
        )
        return output
