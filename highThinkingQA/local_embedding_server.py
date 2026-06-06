from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool


DEFAULT_MODEL_NAME = "qwen3-embedding-8b"
DEFAULT_MODEL_PATH = "/home/cqy/qwen3_embedding_8b"
DEFAULT_DIMENSIONS = 4096
DEFAULT_BATCH_SIZE = 4
DEFAULT_MAX_INPUT_TOKENS = 8192


@dataclass(frozen=True)
class EmbeddingServerSettings:
    model_name: str = DEFAULT_MODEL_NAME
    model_path: str = DEFAULT_MODEL_PATH
    dimensions: int = DEFAULT_DIMENSIONS
    allow_dimensions_parameter: bool = False
    batch_size: int = DEFAULT_BATCH_SIZE
    max_input_tokens: int = DEFAULT_MAX_INPUT_TOKENS
    device: str = ""
    api_key: str = ""


EmbedFunc = Callable[[list[str]], list[list[float]]]


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = int(str(os.getenv(name, str(default)) or str(default)).strip())
    except Exception:
        value = int(default)
    return max(minimum, value)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _normalize_api_key(value: str | None) -> str:
    raw = str(value or "").strip()
    scheme, separator, token = raw.partition(" ")
    if separator and scheme.lower() == "bearer":
        return token.strip()
    return raw


def load_settings_from_env() -> EmbeddingServerSettings:
    return EmbeddingServerSettings(
        model_name=str(os.getenv("QWEN3_EMBEDDING_MODEL_NAME", DEFAULT_MODEL_NAME) or DEFAULT_MODEL_NAME).strip(),
        model_path=str(os.getenv("QWEN3_EMBEDDING_MODEL_PATH", DEFAULT_MODEL_PATH) or DEFAULT_MODEL_PATH).strip(),
        dimensions=_env_int("QWEN3_EMBEDDING_DIMENSIONS", DEFAULT_DIMENSIONS),
        allow_dimensions_parameter=_env_bool("QWEN3_EMBEDDING_ALLOW_DIMENSIONS", False),
        batch_size=_env_int("QWEN3_EMBEDDING_BATCH_SIZE", DEFAULT_BATCH_SIZE),
        max_input_tokens=_env_int("QWEN3_EMBEDDING_MAX_INPUT_TOKENS", DEFAULT_MAX_INPUT_TOKENS),
        device=str(os.getenv("QWEN3_EMBEDDING_DEVICE", "") or "").strip(),
        api_key=_normalize_api_key(os.getenv("QWEN3_EMBEDDING_API_KEY", "")),
    )


def _default_embed_func(
    texts: list[str],
    *,
    settings: EmbeddingServerSettings,
    dimensions: int,
) -> list[list[float]]:
    from ingest.local_embedder import embed_texts_local

    return embed_texts_local(
        texts,
        model_path=settings.model_path,
        dimensions=dimensions,
        batch_size=settings.batch_size,
        max_input_tokens=settings.max_input_tokens,
        device=settings.device or None,
    )


def _check_auth(request: Request, settings: EmbeddingServerSettings) -> None:
    expected = _normalize_api_key(settings.api_key)
    if not expected:
        return
    auth = str(request.headers.get("authorization") or "").strip()
    scheme, separator, token = auth.partition(" ")
    provided = token.strip() if separator and scheme.lower() == "bearer" else auth
    provided = provided or str(request.headers.get("x-api-key") or "").strip()
    if provided != expected:
        raise HTTPException(status_code=401, detail="invalid embedding api key")


def _coerce_input(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value]
    raise HTTPException(status_code=400, detail="input must be a string or a list of strings")


def _resolve_dimensions(value: Any, settings: EmbeddingServerSettings) -> int:
    if value is None:
        return int(settings.dimensions)
    if not settings.allow_dimensions_parameter:
        raise HTTPException(status_code=400, detail="dimensions parameter is not supported")
    try:
        dimensions = int(value)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="dimensions must be an integer") from exc
    if dimensions < 1 or dimensions > int(settings.dimensions):
        raise HTTPException(
            status_code=400,
            detail=f"dimensions must be between 1 and {settings.dimensions}",
        )
    return dimensions


def _estimate_prompt_tokens(texts: list[str]) -> int:
    return sum(max(1, len(str(text or "").split())) for text in texts)


def create_app(
    *,
    settings: EmbeddingServerSettings | None = None,
    embed_func: Callable[..., list[list[float]]] | None = None,
) -> FastAPI:
    resolved_settings = settings or load_settings_from_env()
    resolved_embed_func = embed_func or _default_embed_func
    app = FastAPI(title="Qwen3 Local Embedding Server", version="1.0")
    app.state.settings = resolved_settings
    app.state.embed_func = resolved_embed_func

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "model_name": resolved_settings.model_name,
            "model_path": resolved_settings.model_path,
            "model_path_exists": Path(resolved_settings.model_path).is_dir(),
            "dimensions": resolved_settings.dimensions,
            "allow_dimensions_parameter": resolved_settings.allow_dimensions_parameter,
            "device": resolved_settings.device or "auto",
        }

    @app.get("/v1/models")
    def models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [
                {
                    "id": resolved_settings.model_name,
                    "object": "model",
                    "owned_by": "local",
                }
            ],
        }

    @app.post("/v1/embeddings")
    async def embeddings(request: Request) -> dict[str, Any]:
        _check_auth(request, resolved_settings)
        try:
            payload = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid json body") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="json body must be an object")
        model = str(payload.get("model") or resolved_settings.model_name).strip()
        if model != resolved_settings.model_name:
            raise HTTPException(status_code=404, detail=f"model not available: {model}")
        texts = _coerce_input(payload.get("input"))
        dimensions = _resolve_dimensions(payload.get("dimensions"), resolved_settings)
        encoding_format = str(payload.get("encoding_format") or "float").strip().lower()
        if encoding_format != "float":
            raise HTTPException(status_code=400, detail="only encoding_format=float is supported")

        try:
            vectors = await run_in_threadpool(
                resolved_embed_func,
                texts,
                settings=resolved_settings,
                dimensions=dimensions,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"embedding failed: {exc}") from exc

        if len(vectors) != len(texts):
            raise HTTPException(status_code=500, detail="embedding count mismatch")
        data = []
        for index, vector in enumerate(vectors):
            if len(vector) != dimensions:
                raise HTTPException(
                    status_code=500,
                    detail=f"embedding dimension mismatch: expected={dimensions} actual={len(vector)}",
                )
            data.append({"object": "embedding", "embedding": vector, "index": index})

        prompt_tokens = _estimate_prompt_tokens(texts)
        return {
            "object": "list",
            "data": data,
            "model": resolved_settings.model_name,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "total_tokens": prompt_tokens,
            },
        }

    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = str(os.getenv("QWEN3_EMBEDDING_HOST", "0.0.0.0") or "0.0.0.0").strip()
    port = _env_int("QWEN3_EMBEDDING_PORT", 8012)
    uvicorn.run("local_embedding_server:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
