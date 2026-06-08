from __future__ import annotations

import logging
import math
import os
import time
import json
from types import SimpleNamespace
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from server.patent.answering import PatentAnswerBuilder
from server.patent.archive_loader import PatentArchiveLoader
from server.patent.models import PatentRetrievalClaim, PatentRetrievalPlan
from server.patent.object_reader import ObjectReader
from server.patent.original_minio_loader import PatentOriginalMinioLoader
from server.patent.model_call_logging import (
    auth_mode_label,
    log_model_call_failed,
    log_model_call_start,
    log_model_call_success,
    message_chars,
)
from server.patent.resource_registry import PatentResourceRegistry
from server.patent.retrieval_service import PatentRetrievalService
from server.patent.rerank_service import build_patent_stage2_rerank_fn
from server.patent.stage2_controls import build_stage2_runtime_signature
from server.patent.stages.evidence_loading import run_stage3_load_patent_evidence
from server.patent.stages.planning import DEFAULT_PATENT_STAGE1_PROMPT, run_stage1_pre_answer_and_planning
from server.patent.stages.retrieval import (
    extract_patent_source_ids_from_results,
    run_stage2_targeted_retrieval,
    run_stage25_patent_evidence_expansion,
)
from server.patent.stages.synthesis import run_stage4_synthesis_with_patent_evidence
from server.patent.thinking import (
    LLM_STAGE_CONTROL,
    apply_openai_compatible_thinking,
    auth_headers,
    resolve_auth_mode,
    resolve_thinking_controls,
)
from server.patent.upstream_transport import (
    build_patent_request_timeout,
    describe_patent_transport,
    record_patent_dispatch_error,
    record_patent_dispatch_success,
)
from server.patent.upstream_auth_logging import (
    log_upstream_auth_failure,
    log_upstream_auth_success_once,
)

try:
    import chromadb
except Exception:  # pragma: no cover - dependency guard
    chromadb = None

try:
    from FlagEmbedding import FlagModel
except Exception:  # pragma: no cover - dependency guard
    FlagModel = None


_LOGGER = logging.getLogger("patent.runtime")


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _preview(value: Any, *, limit: int = 220) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(1, limit - 1)].rstrip()}…"


def _vector_diagnostics(vectors: list[list[Any]]) -> dict[str, Any]:
    first = list(vectors[0]) if vectors else []
    numeric: list[float] = []
    has_nan = False
    has_inf = False
    for item in first:
        try:
            value = float(item)
        except (TypeError, ValueError):
            continue
        has_nan = has_nan or math.isnan(value)
        has_inf = has_inf or math.isinf(value)
        if not math.isnan(value) and not math.isinf(value):
            numeric.append(value)
    norm = math.sqrt(sum(value * value for value in numeric)) if numeric else 0.0
    return {
        "count": len(vectors),
        "dim": len(first),
        "norm": norm,
        "has_nan": has_nan,
        "has_inf": has_inf,
        "empty": not bool(first),
    }


def _distance_summary(values: list[Any]) -> dict[str, Any]:
    nums: list[float] = []
    for value in values:
        try:
            nums.append(float(value))
        except (TypeError, ValueError):
            continue
    if not nums:
        return {"count": 0, "min": None, "max": None, "avg": None}
    return {"count": len(nums), "min": min(nums), "max": max(nums), "avg": sum(nums) / len(nums)}


def _first_env(*names: str, default: str = "") -> str:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return str(default or "").strip()


def _positive_int_env(*names: str, default: int) -> int:
    raw = _first_env(*names, default=str(default))
    try:
        value = int(str(raw).strip())
    except Exception:
        return int(default)
    return value if value >= 1 else int(default)


def _resolve_local_embedding_model_path(repo_root: Path) -> str:
    configured = _first_env("EMBEDDING_MODEL_PATH")
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            repo_candidate = repo_root / candidate
            if repo_candidate.exists():
                return str(repo_candidate.resolve())
            return configured
        return str(candidate.resolve())
    bundled = repo_root / "resource" / "fastqa" / "models" / "bge_model"
    if bundled.exists():
        return str(bundled.resolve())
    return "BAAI/bge-large-zh-v1.5"


def _env_flag(name: str, default: bool = False) -> bool:
    value = str(os.getenv(name) or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


class PatentPlanningClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        http_client: Any | None = None,
    ) -> None:
        self._api_key = str(api_key or "").strip()
        self._base_url = str(base_url or "").strip()
        self._timeout_seconds = float(timeout_seconds)
        self._owns_http_client = http_client is None
        self._http = http_client or httpx.Client(timeout=self._timeout_seconds)
        transport = describe_patent_transport(http_client=self._http, owns_http_client=self._owns_http_client)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))
        _LOGGER.info(
            "Patent planning client initialized base_url=%s timeout_seconds=%s client_owner=%s shared_client_id=%s",
            self._base_url,
            self._timeout_seconds,
            transport.get("client_owner"),
            transport.get("shared_client_id"),
        )

    def close(self) -> None:
        if self._owns_http_client:
            self._http.close()

    def _create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
        stream: bool = False,
        extra_body: dict[str, Any] | None = None,
        reasoning_effort: str | None = None,
        omit_sampling_parameters: bool = False,
        response_format: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
        **_ignored_kwargs: Any,
    ) -> Any:
        del reasoning_effort
        effective_timeout_seconds = self._timeout_seconds if timeout_seconds is None else max(0.001, float(timeout_seconds))
        request_url = f"{self._base_url.rstrip('/')}/chat/completions"
        payload: dict[str, Any] = {
            "model": str(model or "").strip(),
            "messages": list(messages or []),
            "stream": bool(stream),
            "max_tokens": int(max_tokens),
        }
        if not omit_sampling_parameters:
            payload["temperature"] = float(temperature)
        if isinstance(extra_body, dict):
            payload.update(dict(extra_body))
        controls = resolve_thinking_controls(
            stage=LLM_STAGE_CONTROL,
            max_tokens=int(max_tokens),
            stream=False,
            thinking_enabled=False,
        )
        apply_openai_compatible_thinking(payload, controls)
        if response_format is not None:
            payload["response_format"] = dict(response_format)
        payload_chars = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        _LOGGER.info(
            "Patent planning client request payload ready model=%s message_count=%s payload_chars=%s response_format=%s",
            str(model or "").strip(),
            len(payload.get("messages") or []),
            payload_chars,
            bool(response_format),
        )
        _LOGGER.info(
            "Patent planning client request start model=%s base_url=%s timeout_seconds=%s client_owner=%s shared_client_id=%s",
            str(model or "").strip(),
            self._base_url,
            effective_timeout_seconds,
            describe_patent_transport(http_client=self._http, owns_http_client=self._owns_http_client).get("client_owner"),
            describe_patent_transport(http_client=self._http, owns_http_client=self._owns_http_client).get("shared_client_id"),
        )
        model_call_started = log_model_call_start(
            _LOGGER,
            component="llm_planning",
            model=str(model or "").strip(),
            endpoint=request_url,
            auth_mode=auth_mode_label(),
            stream=False,
            message_count=len(list(payload.get("messages") or [])),
            message_chars_value=message_chars(payload.get("messages")),
            timeout_seconds=effective_timeout_seconds,
            key_present=bool(self._api_key),
        )
        request_started = time.perf_counter()
        request_timeout = build_patent_request_timeout(
            http_client=self._http,
            timeout_seconds=effective_timeout_seconds,
            override_client_config=timeout_seconds is not None,
        )
        headers = auth_headers(self._api_key)
        request = None
        response = None
        if hasattr(self._http, "build_request"):
            try:
                try:
                    request = self._http.build_request(
                        "POST",
                        request_url,
                        headers=headers,
                        json=payload,
                        timeout=request_timeout,
                    )
                except TypeError as exc:
                    if "timeout" not in str(exc):
                        raise
                    request = self._http.build_request(
                        "POST",
                        request_url,
                        headers=headers,
                        json=payload,
                    )
                    try:
                        request.extensions["timeout"] = request_timeout.as_dict()
                    except Exception:
                        pass
            except Exception as exc:
                log_model_call_failed(
                    _LOGGER,
                    component="llm_planning",
                    model=str(model or "").strip(),
                    endpoint=request_url,
                    started_at=model_call_started,
                    exc=exc,
                    auth_mode=auth_mode_label(),
                    stream=False,
                    reason="request_build_failed",
                )
                raise
            _LOGGER.info(
                "Patent planning client request object built model=%s method=%s url=%s elapsed_ms=%.3f content_length=%s",
                str(model or "").strip(),
                getattr(request, "method", "POST"),
                str(getattr(request, "url", request_url)),
                (time.perf_counter() - request_started) * 1000,
                str(getattr(request, "headers", {}).get("content-length") or ""),
            )
        _LOGGER.info(
            "Patent planning client request dispatch start model=%s timeout_seconds=%s elapsed_ms=%.3f transport=%s",
            str(model or "").strip(),
            effective_timeout_seconds,
            (time.perf_counter() - request_started) * 1000,
            "send" if request is not None and hasattr(self._http, "send") else "post",
        )
        dispatch_started = time.perf_counter()
        if request is not None and hasattr(self._http, "send"):
            try:
                response = self._http.send(request, stream=False)
            except Exception as exc:
                record_patent_dispatch_error(http_client=self._http, started_at=dispatch_started, exc=exc)
                log_model_call_failed(
                    _LOGGER,
                    component="llm_planning",
                    model=str(model or "").strip(),
                    endpoint=request_url,
                    started_at=model_call_started,
                    exc=exc,
                    auth_mode=auth_mode_label(),
                    status_code=getattr(response, "status_code", None),
                    stream=False,
                    reason="request_failed",
                )
                raise
        else:
            try:
                response = self._http.post(
                    request_url,
                    headers=headers,
                    json=payload,
                    timeout=request_timeout,
                )
            except Exception as exc:
                record_patent_dispatch_error(http_client=self._http, started_at=dispatch_started, exc=exc)
                log_model_call_failed(
                    _LOGGER,
                    component="llm_planning",
                    model=str(model or "").strip(),
                    endpoint=request_url,
                    started_at=model_call_started,
                    exc=exc,
                    auth_mode=auth_mode_label(),
                    status_code=getattr(response, "status_code", None),
                    stream=False,
                    reason="request_failed",
                )
                raise
        record_patent_dispatch_success(http_client=self._http, started_at=dispatch_started)
        _LOGGER.info(
            "Patent planning client request dispatch returned model=%s status_code=%s elapsed_ms=%.3f",
            str(model or "").strip(),
            getattr(response, "status_code", ""),
            (time.perf_counter() - request_started) * 1000,
        )
        _LOGGER.info(
            "Patent planning client response headers received model=%s status_code=%s elapsed_ms=%.3f content_length=%s",
            str(model or "").strip(),
            getattr(response, "status_code", ""),
            (time.perf_counter() - request_started) * 1000,
            str(response.headers.get("content-length") or ""),
        )
        try:
            response.raise_for_status()
        except Exception as exc:
            log_upstream_auth_failure(
                logger=_LOGGER,
                service="patent",
                endpoint="chat",
                model=str(model or "").strip(),
                base_url=self._base_url,
                api_key=self._api_key,
                status_code=getattr(response, "status_code", None),
                exc=exc,
                auth_mode=resolve_auth_mode(),
            )
            log_model_call_failed(
                _LOGGER,
                component="llm_planning",
                model=str(model or "").strip(),
                endpoint=request_url,
                started_at=model_call_started,
                exc=exc,
                auth_mode=auth_mode_label(),
                status_code=getattr(response, "status_code", None),
                stream=False,
                reason="request_failed",
            )
            raise
        log_upstream_auth_success_once(
            logger=_LOGGER,
            service="patent",
            endpoint="chat",
            model=str(model or "").strip(),
            base_url=self._base_url,
            api_key=self._api_key,
            status_code=getattr(response, "status_code", None),
            auth_mode=resolve_auth_mode(),
        )
        try:
            body = response.json()
            choices = list(body.get("choices") or [])
            message = dict((choices[0] or {}).get("message") or {}) if choices else {}
        except Exception as exc:
            log_model_call_failed(
                _LOGGER,
                component="llm_planning",
                model=str(model or "").strip(),
                endpoint=request_url,
                started_at=model_call_started,
                exc=exc,
                auth_mode=auth_mode_label(),
                status_code=getattr(response, "status_code", None),
                stream=False,
                reason="response_parse_failed",
            )
            raise
        _LOGGER.info(
            "Patent planning client response body parsed model=%s status_code=%s elapsed_ms=%.3f response_chars=%s",
            str(model or "").strip(),
            getattr(response, "status_code", ""),
            (time.perf_counter() - request_started) * 1000,
            len(str(message.get("content") or "")),
        )
        _LOGGER.info(
            "Patent planning client response received model=%s status_code=%s elapsed_ms=%.3f response_chars=%s",
            str(model or "").strip(),
            getattr(response, "status_code", ""),
            (time.perf_counter() - request_started) * 1000,
            len(str(message.get("content") or "")),
        )
        log_model_call_success(
            _LOGGER,
            component="llm_planning",
            model=str(model or "").strip(),
            endpoint=request_url,
            started_at=model_call_started,
            auth_mode=auth_mode_label(),
            status_code=getattr(response, "status_code", None),
            stream=False,
            answer_chars=len(str(message.get("content") or "")),
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=str(message.get("content") or "")))]
        )


def _resolve_patent_planning_runtime_config() -> tuple[str, str, str, float]:
    api_key = str(os.getenv("LLM_API_KEY") or "").strip()
    base_url = str(os.getenv("LLM_BASE_URL") or "").strip()
    model = str(os.getenv("LLM_MODEL") or "").strip()
    timeout_seconds = float(str(os.getenv("LLM_READ_TIMEOUT_SECONDS") or "30").strip())
    return api_key, base_url, model, timeout_seconds


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


def resolve_patent_planning_runtime_model() -> str:
    _, _, model, _ = _resolve_patent_planning_runtime_config()
    return str(model or "").strip()


def _build_patent_planning_runtime_inputs(*, http_client: Any | None = None) -> tuple[Any | None, str]:
    api_key, base_url, model, timeout_seconds = _resolve_patent_planning_runtime_config()
    if not base_url or not model:
        _LOGGER.warning(
            "Patent planning client disabled api_key_set=%s base_url_set=%s model=%s",
            bool(api_key),
            bool(base_url),
            model,
        )
        return None, ""
    _LOGGER.info(
        "Patent planning client enabled model=%s base_url=%s timeout_seconds=%s",
        model,
        base_url,
        timeout_seconds,
    )
    return PatentPlanningClient(
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=timeout_seconds,
        http_client=http_client,
    ), model


def build_patent_planning_runtime_inputs(*, http_client: Any | None = None) -> tuple[Any | None, str]:
    return _build_patent_planning_runtime_inputs(http_client=http_client)


class PatentEmbeddingClient:
    def __init__(self) -> None:
        self._repo_root = Path(__file__).resolve().parents[3]
        self._mode = _first_env("EMBEDDING_MODEL_TYPE", default="remote").lower()
        self._http = httpx.Client(
            timeout=float(str(os.getenv("EMBEDDING_API_TIMEOUT_SECONDS") or "120").strip())
        )
        self._api_url = _first_env(
            "EMBEDDING_API_URL",
            default="http://127.0.0.1:8001/v1",
        )
        self._api_url = _normalize_embedding_endpoint(self._api_url)
        self._api_model = _first_env("EMBEDDING_API_MODEL", default="bge-local")
        self._api_key = _first_env("EMBEDDING_API_KEY")
        self._local_model_path = _resolve_local_embedding_model_path(self._repo_root)
        self._local_model = None

    def close(self) -> None:
        self._http.close()

    def encode(self, texts: list[str]) -> list[list[float]]:
        started_at = time.perf_counter()
        input_texts = [str(item or "") for item in list(texts or [])]
        input_chars = sum(len(item) for item in input_texts)
        input_bytes = sum(len(item.encode("utf-8", errors="replace")) for item in input_texts)
        if self._mode == "local":
            self._prime_local_model()
            if self._local_model is not None:
                embeddings = self._local_model.encode(texts).tolist()
                diag = _vector_diagnostics([list(item or []) for item in list(embeddings or [])])
                _LOGGER.info(
                    "patent embedding diagnostic mode=local model=%s endpoint=local input_count=%s input_chars=%s "
                    "input_utf8_bytes=%s embedding_count=%s embedding_dim=%s embedding_norm=%.6f has_nan=%s "
                    "has_inf=%s empty_embedding=%s elapsed_ms=%.2f input_preview=%s",
                    self._local_model_path,
                    len(input_texts),
                    input_chars,
                    input_bytes,
                    diag["count"],
                    diag["dim"],
                    float(diag["norm"]),
                    _bool_text(bool(diag["has_nan"])),
                    _bool_text(bool(diag["has_inf"])),
                    _bool_text(bool(diag["empty"])),
                    (time.perf_counter() - started_at) * 1000.0,
                    _preview(" | ".join(input_texts[:3])),
                )
                return embeddings
        auth_mode = _first_env("EMBEDDING_AUTH_MODE", "QA_EMBEDDING_AUTH_MODE", default="bearer")
        _LOGGER.info(
            "model_call start service=patent component=embedding model=%s endpoint=%s auth_mode=%s "
            "input_count=%s input_chars=%s key_present=%s",
            self._api_model,
            self._api_url,
            auth_mode,
            len(input_texts),
            input_chars,
            bool(self._api_key),
        )
        response = None
        try:
            response = self._http.post(
                self._api_url,
                json={"input": texts, "model": self._api_model},
                headers=self._embedding_headers(),
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            _LOGGER.warning(
                "model_call failed service=patent component=embedding model=%s endpoint=%s auth_mode=%s "
                "status_code=%s elapsed_ms=%.2f error_type=%s",
                self._api_model,
                self._api_url,
                auth_mode,
                getattr(response, "status_code", None),
                (time.perf_counter() - started_at) * 1000.0,
                type(exc).__name__,
            )
            raise
        data = list(payload.get("data") or [])
        embeddings = [list(item.get("embedding") or []) for item in data if isinstance(item, dict)]
        if embeddings:
            diag = _vector_diagnostics(embeddings)
            _LOGGER.info(
                "model_call success service=patent component=embedding model=%s endpoint=%s auth_mode=%s "
                "status_code=%s elapsed_ms=%.2f embedding_count=%s embedding_dim=%s",
                self._api_model,
                self._api_url,
                auth_mode,
                getattr(response, "status_code", None),
                (time.perf_counter() - started_at) * 1000.0,
                diag["count"],
                diag["dim"],
            )
            _LOGGER.info(
                "patent embedding diagnostic mode=%s model=%s endpoint=%s input_count=%s input_chars=%s "
                "input_utf8_bytes=%s embedding_count=%s embedding_dim=%s embedding_norm=%.6f has_nan=%s "
                "has_inf=%s empty_embedding=%s elapsed_ms=%.2f input_preview=%s",
                self._mode,
                self._api_model,
                self._api_url,
                len(input_texts),
                input_chars,
                input_bytes,
                diag["count"],
                diag["dim"],
                float(diag["norm"]),
                _bool_text(bool(diag["has_nan"])),
                _bool_text(bool(diag["has_inf"])),
                _bool_text(bool(diag["empty"])),
                (time.perf_counter() - started_at) * 1000.0,
                _preview(" | ".join(input_texts[:3])),
            )
            return embeddings
        single = list(payload.get("embedding") or [])
        out = [single] if single else []
        diag = _vector_diagnostics(out)
        _LOGGER.info(
            "model_call success service=patent component=embedding model=%s endpoint=%s auth_mode=%s "
            "status_code=%s elapsed_ms=%.2f embedding_count=%s embedding_dim=%s",
            self._api_model,
            self._api_url,
            auth_mode,
            getattr(response, "status_code", None),
            (time.perf_counter() - started_at) * 1000.0,
            diag["count"],
            diag["dim"],
        )
        _LOGGER.info(
            "patent embedding diagnostic mode=%s model=%s endpoint=%s input_count=%s input_chars=%s "
            "input_utf8_bytes=%s embedding_count=%s embedding_dim=%s embedding_norm=%.6f has_nan=%s "
            "has_inf=%s empty_embedding=%s elapsed_ms=%.2f input_preview=%s",
            self._mode,
            self._api_model,
            self._api_url,
            len(input_texts),
            input_chars,
            input_bytes,
            diag["count"],
            diag["dim"],
            float(diag["norm"]),
            _bool_text(bool(diag["has_nan"])),
            _bool_text(bool(diag["has_inf"])),
            _bool_text(bool(diag["empty"])),
            (time.perf_counter() - started_at) * 1000.0,
            _preview(" | ".join(input_texts[:3])),
        )
        return out

    def _embedding_headers(self) -> dict[str, str]:
        auth_mode = _first_env("EMBEDDING_AUTH_MODE", "QA_EMBEDDING_AUTH_MODE", default="bearer")
        return auth_headers(self._api_key, auth_mode=auth_mode)

    def _prime_local_model(self) -> None:
        if self._local_model is not None:
            return
        if FlagModel is None:
            if self._api_url:
                self._mode = "remote"
                _LOGGER.warning("FlagEmbedding unavailable; falling back to remote patent embedding endpoint")
                return
            raise RuntimeError("FlagEmbedding is unavailable and no remote embedding endpoint is configured")
        try:
            self._local_model = FlagModel(
                self._local_model_path,
                query_instruction_for_retrieval="Represent this sentence for searching relevant passages:",
                use_fp16=False,
                trust_remote_code=True,
            )
        except Exception:
            if self._api_url:
                self._mode = "remote"
                _LOGGER.warning(
                    "Patent local embedding model failed to load from %s; falling back to remote endpoint",
                    self._local_model_path,
                    exc_info=True,
                )
                return
            raise


class ChromaPatentSearch:
    def __init__(self, *, db_path: str, collection_name: str, embedding_client: PatentEmbeddingClient) -> None:
        if chromadb is None:
            raise RuntimeError("chromadb is unavailable")
        self._client = chromadb.PersistentClient(path=db_path)
        self._collection = self._client.get_collection(collection_name)
        self._embedding_client = embedding_client

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def search(self, *, question: str, top_k: int, patent_ids: list[str] | None = None) -> list[dict[str, Any]]:
        started_at = time.perf_counter()
        embeddings = self._embedding_client.encode([question])
        if not embeddings:
            _LOGGER.info(
                "patent chroma search diagnostic status=empty_embedding collection=%s top_k=%s question_chars=%s patent_filter_count=%s",
                str(getattr(self._collection, "name", "")),
                int(top_k),
                len(str(question or "")),
                len(list(patent_ids or [])),
            )
            return []
        where: dict[str, Any] | None = None
        normalized_patent_ids = [str(item).strip().upper() for item in list(patent_ids or []) if str(item).strip()]
        if len(normalized_patent_ids) == 1:
            where = {"patent_id": normalized_patent_ids[0]}
        elif len(normalized_patent_ids) > 1:
            where = {"patent_id": {"$in": normalized_patent_ids}}
        results = self._collection.query(
            query_embeddings=embeddings,
            n_results=max(int(top_k), 1),
            include=["documents", "metadatas", "distances"],
            where=where,
        )
        documents = list((results.get("documents") or [[]])[0])
        metadatas = list((results.get("metadatas") or [[]])[0])
        distances = list((results.get("distances") or [[]])[0])
        ids = list((results.get("ids") or [[]])[0])
        stats = _distance_summary(distances)
        _LOGGER.info(
            "patent chroma search diagnostic collection=%s top_k=%s where=%s question_chars=%s "
            "question_utf8_bytes=%s hits=%s distance_count=%s distance_min=%s distance_max=%s distance_avg=%s "
            "elapsed_ms=%.2f id_sample=%s question_preview=%s",
            str(getattr(self._collection, "name", "")),
            max(int(top_k), 1),
            where or {},
            len(str(question or "")),
            len(str(question or "").encode("utf-8", errors="replace")),
            len(documents),
            stats["count"],
            stats["min"],
            stats["max"],
            stats["avg"],
            (time.perf_counter() - started_at) * 1000.0,
            ids[:5],
            _preview(question),
        )
        for rank, doc in enumerate(documents[:5], start=1):
            meta = metadatas[rank - 1] if rank - 1 < len(metadatas) and isinstance(metadatas[rank - 1], dict) else {}
            distance = distances[rank - 1] if rank - 1 < len(distances) else None
            _LOGGER.info(
                "patent chroma hit detail rank=%s patent_id=%s distance=%s id=%s metadata_keys=%s doc_preview=%s",
                rank,
                str(meta.get("patent_id") or meta.get("canonical_patent_id") or meta.get("json_stem") or ""),
                distance,
                ids[rank - 1] if rank - 1 < len(ids) else "",
                sorted(meta.keys())[:16],
                _preview(doc, limit=360),
            )
        hits: list[dict[str, Any]] = []
        for index, metadata in enumerate(metadatas):
            item = dict(metadata or {})
            item["document"] = documents[index] if index < len(documents) else ""
            item["distance"] = distances[index] if index < len(distances) else None
            item["id"] = ids[index] if index < len(ids) else None
            hits.append(item)
        return hits


@dataclass
class PatentRuntime:
    retrieval_service: PatentRetrievalService
    resources: list[Any]
    archive_loader: PatentArchiveLoader | None = None
    answer_builder: Any | None = None
    planning_client: Any | None = None
    planning_hot_pool: Any | None = None
    planning_upstream_gate: Any | None = None
    planning_model: str = ""
    stage1_prompt: str = DEFAULT_PATENT_STAGE1_PROMPT
    stage25_is_noop: bool = True
    stage25_skip_reason: str = "patent_mode_no_md_expansion"
    stage3_force_pdf: bool = False
    stage2_parallel_workers: int = 4
    stage3_parallel_workers: int = 4
    stage2_rerank_fn: Any | None = None
    table_loader: Any | None = None
    pdf_loader: Any | None = None

    def stage2_runtime_signature(self) -> dict[str, Any]:
        return build_stage2_runtime_signature(
            base_signature={
                "runtime_type": type(self).__name__,
                "retrieval_version": getattr(self.retrieval_service, "retrieval_version", ""),
                "catalog_index_version": getattr(self.retrieval_service, "catalog_index_version", ""),
                "stage2_query_model": self.planning_model,
            }
        )

    def stage1_pre_answer_and_planning(
        self,
        user_question: str,
        conversation_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        planning_client = self.planning_client
        if self.planning_hot_pool is not None:
            proxy_client = getattr(self.planning_hot_pool, "proxy_client", None)
            if callable(proxy_client):
                planning_client = proxy_client(fallback_client=self.planning_client)
        if self.planning_upstream_gate is not None:
            gate_proxy_client = getattr(self.planning_upstream_gate, "proxy_client", None)
            if callable(gate_proxy_client):
                planning_client = gate_proxy_client(
                    base_client=planning_client,
                    trace_label="stage1_planning",
                )
        return run_stage1_pre_answer_and_planning(
            user_question=user_question,
            client=planning_client,
            model=self.planning_model,
            logger=_LOGGER,
            conversation_context=conversation_context,
            stage1_prompt=self.stage1_prompt,
        )

    def stage2_targeted_retrieval(
        self,
        retrieval_claims: Any,
        *,
        user_question: str,
        should_cancel: Any | None = None,
        active_stream_count: int | None = None,
        conversation_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query_client = self.planning_client
        if self.planning_hot_pool is not None:
            proxy_client = getattr(self.planning_hot_pool, "proxy_client", None)
            if callable(proxy_client):
                query_client = proxy_client(fallback_client=self.planning_client)
        if self.planning_upstream_gate is not None:
            gate_proxy_client = getattr(self.planning_upstream_gate, "proxy_client", None)
            if callable(gate_proxy_client):
                query_client = gate_proxy_client(
                    base_client=query_client,
                    trace_label="stage2_query_generation",
                    should_cancel=should_cancel,
                )
        if isinstance(retrieval_claims, PatentRetrievalPlan):
            queries = list(retrieval_claims.evidence_localization_queries or retrieval_claims.candidate_recall_queries or [])
            retrieval_claims = [
                PatentRetrievalClaim(
                    claim=query,
                    keywords=[],
                    preferred_sections=list(retrieval_claims.preferred_sections or []),
                    filters=dict(retrieval_claims.filters or {}),
                )
                for query in queries
            ] or [
                PatentRetrievalClaim(
                    claim=" ".join(str(item).strip() for item in retrieval_claims.explicit_patent_ids if str(item).strip()),
                    keywords=[],
                    preferred_sections=list(retrieval_claims.preferred_sections or []),
                    filters=dict(retrieval_claims.filters or {}),
                )
            ]
        return run_stage2_targeted_retrieval(
            retrieval_service=self.retrieval_service,
            retrieval_claims=list(retrieval_claims or []),
            user_question=user_question,
            query_client=query_client,
            query_model=self.planning_model,
            logger=_LOGGER,
            should_cancel=should_cancel,
            active_stream_count=active_stream_count,
            parallel_workers=self.stage2_parallel_workers,
            context=conversation_context,
            rerank_fn=self.stage2_rerank_fn,
        )

    def _extract_patent_ids_from_results(self, retrieval_results: dict[str, Any]) -> list[str]:
        return extract_patent_source_ids_from_results(
            retrieval_service=self.retrieval_service,
            retrieval_results=retrieval_results,
        )

    def stage25_patent_evidence_expansion(
        self,
        *,
        retrieval_results: dict[str, Any],
        user_question: str,
        source_ids: list[str],
    ) -> dict[str, Any]:
        return run_stage25_patent_evidence_expansion(
            retrieval_results=retrieval_results,
            skipped=self.stage25_is_noop,
            skip_reason=self.stage25_skip_reason,
        )

    def stage3_load_patent_evidence(
        self,
        *,
        retrieval_results: dict[str, Any],
        source_ids: list[str],
        should_cancel: Any | None = None,
    ) -> dict[str, Any]:
        strict_original_minio_only = _env_flag("PATENT_ORIGINAL_MINIO_ONLY", default=True)
        table_loader = self.table_loader if callable(self.table_loader) else None
        if table_loader is None and not strict_original_minio_only and self.archive_loader is not None:
            table_loader = self.archive_loader.load_tables
        pdf_loader = self.pdf_loader if callable(self.pdf_loader) else None
        if pdf_loader is None and not strict_original_minio_only and self.archive_loader is not None:
            pdf_loader = self.archive_loader.load_pdf_document
        return run_stage3_load_patent_evidence(
            retrieval_results=retrieval_results,
            source_ids=source_ids,
            catalog_loader=self.archive_loader.load_catalog_record if self.archive_loader is not None else None,
            table_loader=table_loader,
            pdf_loader=pdf_loader,
            force_pdf=self.stage3_force_pdf,
            parallel_workers=self.stage3_parallel_workers,
            should_cancel=should_cancel,
        )

    def stage4_synthesis_with_patent_evidence(
        self,
        *,
        user_question: str,
        deep_answer: str,
        patent_evidence_bundle: dict[str, Any],
        retrieval_results: dict[str, Any] | None = None,
        should_cancel: Any | None = None,
        content_callback: Any | None = None,
        conversation_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return run_stage4_synthesis_with_patent_evidence(
            user_question=user_question,
            deep_answer=deep_answer,
            patent_evidence_bundle=patent_evidence_bundle,
            retrieval_results=retrieval_results,
            answer_builder=self.answer_builder,
            content_callback=content_callback,
            conversation_context=conversation_context,
            should_cancel=should_cancel,
        )

    def close(self) -> None:
        for resource in self.resources:
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    continue


def build_default_patent_runtime(
    *,
    execution_cache: Any | None = None,
    http_client: Any | None = None,
    planning_hot_pool: Any | None = None,
    planning_upstream_gate: Any | None = None,
) -> PatentRuntime | None:
    registry = PatentResourceRegistry.discover()
    if not registry.archive_available():
        _LOGGER.warning("Patent runtime bootstrap skipped because archive root is unavailable")
        return None

    archive_loader = PatentArchiveLoader(registry.archive_root)
    answer_builder = PatentAnswerBuilder.from_env(http_client=http_client) if http_client is not None else PatentAnswerBuilder.from_env()
    resources: list[Any] = [answer_builder]
    planning_client, planning_model = (
        _build_patent_planning_runtime_inputs(http_client=http_client)
        if http_client is not None
        else _build_patent_planning_runtime_inputs()
    )
    if planning_client is not None:
        resources.append(planning_client)
    abstract_search = None
    chunk_search = None

    if registry.vector_resources_available() and chromadb is not None:
        vector_resources: list[Any] = []
        try:
            embedding_client = PatentEmbeddingClient()
            vector_resources.append(embedding_client)
            abstract_runtime = ChromaPatentSearch(
                db_path=str(registry.abstract_db_path),
                collection_name=registry.abstract_collection,
                embedding_client=embedding_client,
            )
            vector_resources.append(abstract_runtime)
            chunk_runtime = ChromaPatentSearch(
                db_path=str(registry.chunk_db_path),
                collection_name=registry.chunk_collection,
                embedding_client=embedding_client,
            )
            vector_resources.append(chunk_runtime)
        except Exception:
            _LOGGER.warning("Patent vector runtime bootstrap failed; degrading to no-vector retrieval", exc_info=True)
            for resource in reversed(vector_resources):
                close = getattr(resource, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        continue
        else:
            resources.extend(vector_resources)
            abstract_search = lambda question, top_k: abstract_runtime.search(question=question, top_k=top_k)
            chunk_search = lambda question, candidate_patent_ids, top_k: chunk_runtime.search(
                question=question,
                top_k=top_k,
                patent_ids=candidate_patent_ids,
            )
    strict_original_minio_only = _env_flag("PATENT_ORIGINAL_MINIO_ONLY", default=True)
    original_minio_loader = None
    table_loader = getattr(archive_loader, "load_tables", None)
    pdf_loader = getattr(archive_loader, "load_pdf_document", None)
    if strict_original_minio_only:
        original_minio_loader = PatentOriginalMinioLoader(
            reader=ObjectReader(),
            bucket=_first_env("MINIO_BUCKET", default="agentcode") or "agentcode",
            archive_root=registry.archive_root,
        )
        table_loader = original_minio_loader.load_tables
        pdf_loader = original_minio_loader.load_pdf_document
        resources.append(original_minio_loader)
    try:
        retrieval_service = PatentRetrievalService(
            execution_cache=execution_cache,
            identity_registry=archive_loader.build_identity_registry(),
            catalog_records=archive_loader.build_catalog_records(),
            retrieval_version="retrieval-v2",
            catalog_index_version="catalog-v2",
            abstract_vector_search=abstract_search,
            chunk_vector_search=chunk_search,
            table_loader=table_loader,
            answer_builder=answer_builder,
            archive_loader=None if strict_original_minio_only else archive_loader,
        )
    except Exception:
        for resource in reversed(resources):
            close = getattr(resource, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    continue
        raise
    _LOGGER.info(
        "Patent runtime bootstrap complete archive_root=%s abstract_db=%s chunk_db=%s planner_ready=%s planning_model=%s answer_builder_ready=%s answer_model=%s vector_enabled=%s",
        registry.archive_root,
        registry.abstract_db_path,
        registry.chunk_db_path,
        planning_client is not None,
        planning_model,
        bool(getattr(answer_builder, "api_key", "")),
        getattr(answer_builder, "model", ""),
        bool(abstract_search and chunk_search),
    )
    return PatentRuntime(
        retrieval_service=retrieval_service,
        resources=resources,
        archive_loader=archive_loader,
        answer_builder=answer_builder,
        planning_client=planning_client,
        planning_hot_pool=planning_hot_pool,
        planning_upstream_gate=planning_upstream_gate,
        planning_model=planning_model,
        stage2_rerank_fn=build_patent_stage2_rerank_fn(logger=_LOGGER),
        table_loader=table_loader,
        pdf_loader=pdf_loader,
        stage3_force_pdf=_first_env("PATENT_STAGE3_FORCE_PDF", default="false").lower() in {"1", "true", "yes", "on"},
        stage2_parallel_workers=_positive_int_env("PATENT_STAGE2_PARALLEL_WORKERS", default=4),
        stage3_parallel_workers=_positive_int_env("PATENT_STAGE3_PARALLEL_WORKERS", default=4),
    )
