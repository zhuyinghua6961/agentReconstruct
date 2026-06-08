from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterable, Iterator, Mapping

from app.core.logging import beijing_now_iso
from app.integrations.llm.thinking import (
    LLM_STAGE_STAGE4_FINAL_ANSWER,
    auth_headers,
    resolve_auth_mode,
    resolve_thinking_controls,
)
from app.integrations.llm.upstream_auth_logging import (
    log_upstream_auth_failure,
    log_upstream_auth_success_once,
)

DEFAULT_LLM_COMPATIBLE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_LOGGER = logging.getLogger(__name__)


def _coerce_text_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if isinstance(item, Mapping):
                item_text = item.get("text")
                if isinstance(item_text, str):
                    parts.append(item_text)
                continue
            item_text = getattr(item, "text", None)
            if isinstance(item_text, str):
                parts.append(item_text)
        return "".join(parts)
    return str(content)


def _normalize_message_role(role_value: Any) -> str:
    role = str(role_value or "").strip().lower()
    if role == "human":
        return "user"
    if role == "ai":
        return "assistant"
    if role in {"system", "user", "assistant", "tool"}:
        return role
    return "user"


def normalize_messages(payload: Any) -> list[dict[str, str]]:
    if isinstance(payload, str):
        text = payload.strip()
        return [{"role": "user", "content": text}] if text else []

    normalized: list[dict[str, str]] = []
    if not isinstance(payload, Iterable):
        return normalized
    for item in payload:
        if item is None:
            continue
        if isinstance(item, Mapping):
            role = _normalize_message_role(item.get("role") or item.get("type"))
            content = _coerce_text_content(item.get("content"))
        else:
            role = _normalize_message_role(getattr(item, "role", None) or getattr(item, "type", None))
            content = _coerce_text_content(getattr(item, "content", None))
        content = str(content or "").strip()
        if not content:
            continue
        normalized.append({"role": role, "content": content})
    return normalized


def normalize_openai_compatible_endpoint(base_url: str | None) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        value = DEFAULT_LLM_COMPATIBLE_BASE_URL
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if value.endswith(suffix):
            value = value[: -len(suffix)]
            break
    if not value.endswith("/v1"):
        value = value.rstrip("/") + "/v1"
    return value.rstrip("/") + "/chat/completions"


def extract_openai_compatible_text(payload: Any) -> str:
    if not isinstance(payload, Mapping):
        return ""
    choices = payload.get("choices") or []
    if not choices:
        return ""
    first = choices[0] or {}
    delta = first.get("delta") or {}
    text = _coerce_text_content(delta.get("content"))
    if text:
        return text
    message = first.get("message") or {}
    text = _coerce_text_content(message.get("content"))
    if text:
        return text
    return _coerce_text_content(first.get("text"))


def _httpx_timeout(httpx_module: Any, *, connect: float, read: float, write: float, pool: float) -> Any:
    return httpx_module.Timeout(connect=connect, read=read, write=write, pool=pool)


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _message_chars(messages: Any) -> int:
    if not isinstance(messages, list):
        return 0
    return sum(len(_coerce_text_content((item or {}).get("content") if isinstance(item, Mapping) else "")) for item in messages)

@dataclass(frozen=True)
class _ClientConfig:
    endpoint: str
    api_key: str
    connect_timeout_seconds: float
    read_timeout_seconds: float
    stream_read_timeout_seconds: float
    write_timeout_seconds: float
    pool_timeout_seconds: float
    keepalive_expiry_seconds: float
    max_connections: int
    max_keepalive_connections: int


class _TimingMixin:
    def __init__(self, *, logger: Any | None = None) -> None:
        self._logger = logger

    def _log_timing(self, stage: str, started_at: float, **fields: Any) -> None:
        if self._logger is None:
            return
        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        extras = " ".join(f"{k}={fields[k]}" for k in fields if fields[k] is not None)
        suffix = f" {extras}" if extras else ""
        self._logger.info(
            "[LLM_TRANSPORT] ts=%s stage=%s elapsed_ms=%.2f%s",
            beijing_now_iso(),
            stage,
            elapsed_ms,
            suffix,
        )


class _OpenAICompatBase(_TimingMixin):
    def __init__(
        self,
        *,
        httpx_module: Any,
        endpoint: str,
        api_key: str,
        logger: Any | None = None,
        connect_timeout_seconds: float = 10.0,
        read_timeout_seconds: float = 65.0,
        stream_read_timeout_seconds: float = 600.0,
        write_timeout_seconds: float = 65.0,
        pool_timeout_seconds: float = 5.0,
        keepalive_expiry_seconds: float = 5.0,
        max_connections: int = 50,
        max_keepalive_connections: int = 20,
        http_client: Any | None = None,
    ) -> None:
        super().__init__(logger=logger)
        self._httpx = httpx_module
        self._cfg = _ClientConfig(
            endpoint=endpoint,
            api_key=api_key,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            stream_read_timeout_seconds=stream_read_timeout_seconds,
            write_timeout_seconds=write_timeout_seconds,
            pool_timeout_seconds=pool_timeout_seconds,
            keepalive_expiry_seconds=keepalive_expiry_seconds,
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        )
        self._owns_client = http_client is None
        self._closed = False
        if http_client is not None:
            self._client = http_client
        else:
            timeout = _httpx_timeout(
                self._httpx,
                connect=self._cfg.connect_timeout_seconds,
                read=self._cfg.read_timeout_seconds,
                write=self._cfg.write_timeout_seconds,
                pool=self._cfg.pool_timeout_seconds,
            )
            limits = self._httpx.Limits(
                max_connections=self._cfg.max_connections,
                max_keepalive_connections=self._cfg.max_keepalive_connections,
                keepalive_expiry=self._cfg.keepalive_expiry_seconds,
            )
            self._client = self._httpx.Client(timeout=timeout, limits=limits, http2=False)
        self._shared_pool = getattr(self._client, "_fastqa_shared_pool", None)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._owns_client:
            return
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def _build_timeout(
        self,
        *,
        timeout: Any | None = None,
        connect_timeout_seconds: float | None = None,
        read_timeout_seconds: float | None = None,
        write_timeout_seconds: float | None = None,
        pool_timeout_seconds: float | None = None,
    ) -> Any | None:
        if timeout is not None:
            return timeout
        if (
            connect_timeout_seconds is None
            and read_timeout_seconds is None
            and write_timeout_seconds is None
            and pool_timeout_seconds is None
        ):
            return None
        return _httpx_timeout(
            self._httpx,
            connect=self._cfg.connect_timeout_seconds if connect_timeout_seconds is None else connect_timeout_seconds,
            read=self._cfg.read_timeout_seconds if read_timeout_seconds is None else read_timeout_seconds,
            write=self._cfg.write_timeout_seconds if write_timeout_seconds is None else write_timeout_seconds,
            pool=self._cfg.pool_timeout_seconds if pool_timeout_seconds is None else pool_timeout_seconds,
        )

    def _headers(self) -> dict[str, str]:
        return auth_headers(self._cfg.api_key, accept="application/json, text/event-stream")

    def _auth_mode(self) -> str:
        return resolve_auth_mode()

    def _stream_read_timeout_seconds(self, explicit: float | None) -> float | None:
        if explicit is not None:
            return explicit
        return self._cfg.stream_read_timeout_seconds

    def _observability_fields(self) -> dict[str, Any]:
        shared_pool = self._shared_pool
        if shared_pool is not None:
            snapshot = dict(getattr(shared_pool, "snapshot", lambda: {})() or {})
            snapshot.setdefault("pool_owner", "app")
            snapshot.setdefault("client_owner", "shared")
            return snapshot
        return {
            "pool_owner": "app",
            "client_owner": "private",
            "shared_client_id": f"{id(self._client):x}",
            "pid": os.getpid(),
            "bootstrap_source": "startup",
            "pool_timeout_count": 0,
            "pool_wait_ms": 0.0,
            "max_connections": self._cfg.max_connections,
            "max_keepalive_connections": self._cfg.max_keepalive_connections,
            "keepalive_expiry_seconds": self._cfg.keepalive_expiry_seconds,
        }

    def _record_pool_wait(self, *, wait_ms: float) -> None:
        record = getattr(self._shared_pool, "record_pool_wait", None)
        if callable(record):
            record(wait_ms=wait_ms)

    def _record_pool_timeout(self, *, wait_ms: float) -> None:
        record = getattr(self._shared_pool, "record_pool_timeout", None)
        if callable(record):
            record(wait_ms=wait_ms)

    def _log_auth_success(self, *, model: str, status_code: Any = None) -> None:
        log_upstream_auth_success_once(
            logger=_LOGGER,
            service="fastQA",
            endpoint="chat",
            model=str(model or ""),
            base_url=self._cfg.endpoint,
            api_key=self._cfg.api_key,
            status_code=status_code,
            auth_mode=self._auth_mode(),
        )

    def _log_auth_failure(self, *, model: str, status_code: Any = None, exc: Exception | None = None) -> None:
        log_upstream_auth_failure(
            logger=_LOGGER,
            service="fastQA",
            endpoint="chat",
            model=str(model or ""),
            base_url=self._cfg.endpoint,
            api_key=self._cfg.api_key,
            status_code=status_code,
            exc=exc,
            auth_mode=self._auth_mode(),
        )

    def _log_model_call_start(
        self,
        *,
        component: str,
        model: str,
        stream: bool,
        message_count: int,
        message_chars: int,
    ) -> float:
        started_at = time.monotonic()
        _LOGGER.info(
            "model_call start service=fastQA component=%s model=%s endpoint=%s auth_mode=%s "
            "stream=%s message_count=%s message_chars=%s key_present=%s",
            component,
            str(model or ""),
            self._cfg.endpoint,
            self._auth_mode(),
            _bool_text(stream),
            int(message_count),
            int(message_chars),
            bool(str(self._cfg.api_key or "").strip()),
        )
        return started_at

    def _log_model_call_success(
        self,
        *,
        component: str,
        model: str,
        started_at: float,
        status_code: Any = None,
        stream: bool,
        answer_chars: int,
        chunk_count: int | None = None,
    ) -> None:
        chunk_field = f" chunk_count={int(chunk_count)}" if chunk_count is not None else ""
        _LOGGER.info(
            "model_call success service=fastQA component=%s model=%s endpoint=%s auth_mode=%s "
            "status_code=%s stream=%s answer_chars=%s%s elapsed_ms=%.2f",
            component,
            str(model or ""),
            self._cfg.endpoint,
            self._auth_mode(),
            status_code,
            _bool_text(stream),
            int(answer_chars),
            chunk_field,
            (time.monotonic() - started_at) * 1000.0,
        )

    def _log_model_call_failed(
        self,
        *,
        component: str,
        model: str,
        started_at: float,
        exc: Exception,
        status_code: Any = None,
        stream: bool,
    ) -> None:
        _LOGGER.warning(
            "model_call failed service=fastQA component=%s model=%s endpoint=%s auth_mode=%s "
            "status_code=%s stream=%s elapsed_ms=%.2f error_type=%s",
            component,
            str(model or ""),
            self._cfg.endpoint,
            self._auth_mode(),
            status_code,
            _bool_text(stream),
            (time.monotonic() - started_at) * 1000.0,
            type(exc).__name__,
        )

    def _is_pool_timeout(self, exc: Exception) -> bool:
        pool_timeout_cls = getattr(self._httpx, "PoolTimeout", None)
        if pool_timeout_cls is not None and isinstance(exc, pool_timeout_cls):
            return True
        try:
            import httpx as real_httpx

            return isinstance(exc, real_httpx.PoolTimeout)
        except Exception:
            return exc.__class__.__name__ == "PoolTimeout"

    def _log_transport_success(self, stage: str, started_at: float, **fields: Any) -> None:
        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        observability = self._observability_fields()
        self._log_timing(
            stage,
            started_at,
            transport_elapsed_ms=f"{elapsed_ms:.2f}",
            pool_wait_ms=observability.get("pool_wait_ms"),
            pool_timeout_count=observability.get("pool_timeout_count"),
            pool_owner=observability.get("pool_owner"),
            client_owner=observability.get("client_owner"),
            shared_client_id=observability.get("shared_client_id"),
            pid=observability.get("pid"),
            bootstrap_source=observability.get("bootstrap_source"),
            max_connections=observability.get("max_connections"),
            max_keepalive_connections=observability.get("max_keepalive_connections"),
            keepalive_expiry_seconds=observability.get("keepalive_expiry_seconds"),
            **fields,
        )

    def _handle_transport_error(self, *, stage: str, started_at: float, exc: Exception) -> None:
        if not self._is_pool_timeout(exc):
            raise exc
        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        self._record_pool_timeout(wait_ms=elapsed_ms)
        observability = self._observability_fields()
        if self._logger is not None:
            self._logger.warning(
                "[LLM_TRANSPORT] ts=%s stage=%s pool_wait_ms=%.2f pool_timeout_count=%s pool_owner=%s client_owner=%s shared_client_id=%s pid=%s bootstrap_source=%s max_connections=%s max_keepalive_connections=%s keepalive_expiry_seconds=%s error=%s",
                beijing_now_iso(),
                stage,
                elapsed_ms,
                observability.get("pool_timeout_count"),
                observability.get("pool_owner"),
                observability.get("client_owner"),
                observability.get("shared_client_id"),
                observability.get("pid"),
                observability.get("bootstrap_source"),
                observability.get("max_connections"),
                observability.get("max_keepalive_connections"),
                observability.get("keepalive_expiry_seconds"),
                exc,
            )
        raise exc

    def _build_payload(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        stream: bool,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        extra_body: Mapping[str, Any] | None = None,
        reasoning_effort: str | None = None,
        omit_sampling_parameters: bool = False,
        response_format: Any | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if temperature is not None and not omit_sampling_parameters:
            payload["temperature"] = temperature
        if top_p is not None and not omit_sampling_parameters:
            payload["top_p"] = top_p
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if reasoning_effort is not None:
            payload["reasoning_effort"] = reasoning_effort
        if response_format is not None:
            payload["response_format"] = response_format
        if isinstance(extra_body, Mapping):
            payload.update(dict(extra_body))
        return payload

    def _iter_sse_json(self, response: Any) -> Iterator[dict[str, Any]]:
        for raw_line in response.iter_lines():
            line = str(raw_line or "").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                if self._logger is not None:
                    self._logger.warning("skip malformed openai-compatible stream frame")
                continue
            if isinstance(payload, Mapping) and isinstance(payload.get("error"), Mapping):
                error_payload = payload.get("error") or {}
                message = str(error_payload.get("message") or error_payload.get("code") or "upstream_stream_error").strip()
                raise RuntimeError(message)
            if isinstance(payload, Mapping):
                yield dict(payload)


class OpenAICompatChatAdapter(_OpenAICompatBase):
    def __init__(
        self,
        *,
        httpx_module: Any,
        endpoint: str,
        api_key: str,
        model: str,
        temperature: float = 0.5,
        top_p: float = 0.95,
        max_tokens: int = 4096,
        logger: Any | None = None,
        connect_timeout_seconds: float = 10.0,
        read_timeout_seconds: float = 65.0,
        stream_read_timeout_seconds: float = 600.0,
        write_timeout_seconds: float = 65.0,
        pool_timeout_seconds: float = 5.0,
        keepalive_expiry_seconds: float = 5.0,
        max_connections: int = 50,
        max_keepalive_connections: int = 20,
        http_client: Any | None = None,
    ) -> None:
        super().__init__(
            httpx_module=httpx_module,
            endpoint=endpoint,
            api_key=api_key,
            logger=logger,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            stream_read_timeout_seconds=stream_read_timeout_seconds,
            write_timeout_seconds=write_timeout_seconds,
            pool_timeout_seconds=pool_timeout_seconds,
            keepalive_expiry_seconds=keepalive_expiry_seconds,
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
            http_client=http_client,
        )
        self._model = model
        self._temperature = temperature
        self._top_p = top_p
        self._max_tokens = max_tokens

    def invoke(
        self,
        payload: Any,
        *,
        timeout: Any | None = None,
        connect_timeout_seconds: float | None = None,
        read_timeout_seconds: float | None = None,
        write_timeout_seconds: float | None = None,
        pool_timeout_seconds: float | None = None,
    ) -> Any:
        messages = normalize_messages(payload)
        if not messages:
            return SimpleNamespace(content="")
        started_at = self._log_model_call_start(
            component="llm",
            model=self._model,
            stream=False,
            message_count=len(messages),
            message_chars=_message_chars(messages),
        )
        thinking_controls = resolve_thinking_controls(
            stage=LLM_STAGE_STAGE4_FINAL_ANSWER,
            max_tokens=self._max_tokens,
            stream=False,
        )
        request_kwargs: dict[str, Any] = {
            "headers": self._headers(),
            "json": self._build_payload(
                model=self._model,
                messages=messages,
                stream=False,
                temperature=self._temperature,
                top_p=self._top_p,
                max_tokens=thinking_controls.max_tokens,
                extra_body=thinking_controls.extra_body,
                reasoning_effort=thinking_controls.reasoning_effort,
                omit_sampling_parameters=thinking_controls.enabled,
            ),
        }
        request_timeout = self._build_timeout(
            timeout=timeout,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            write_timeout_seconds=write_timeout_seconds,
            pool_timeout_seconds=pool_timeout_seconds,
        )
        if request_timeout is not None:
            request_kwargs["timeout"] = request_timeout
        response = None
        try:
            response = self._client.post(
                self._cfg.endpoint,
                **request_kwargs,
            )
        except Exception as exc:
            self._log_model_call_failed(
                component="llm",
                model=self._model,
                started_at=started_at,
                exc=exc,
                status_code=getattr(response, "status_code", None),
                stream=False,
            )
            self._handle_transport_error(stage="openai_compat_invoke_error", started_at=started_at, exc=exc)
        try:
            response.raise_for_status()
        except Exception as exc:
            self._log_auth_failure(model=self._model, status_code=getattr(response, "status_code", None), exc=exc)
            self._log_model_call_failed(
                component="llm",
                model=self._model,
                started_at=started_at,
                exc=exc,
                status_code=getattr(response, "status_code", None),
                stream=False,
            )
            raise
        self._log_auth_success(model=self._model, status_code=getattr(response, "status_code", None))
        body = response.json()
        content = extract_openai_compatible_text(body)
        self._log_model_call_success(
            component="llm",
            model=self._model,
            started_at=started_at,
            status_code=getattr(response, "status_code", None),
            stream=False,
            answer_chars=len(content),
        )
        self._log_transport_success(
            "openai_compat_invoke_done",
            started_at,
            message_count=len(messages),
            answer_chars=len(content),
        )
        return SimpleNamespace(content=content)

    def stream(
        self,
        payload: Any,
        *,
        timeout: Any | None = None,
        connect_timeout_seconds: float | None = None,
        read_timeout_seconds: float | None = None,
        write_timeout_seconds: float | None = None,
        pool_timeout_seconds: float | None = None,
    ) -> Iterator[Any]:
        messages = normalize_messages(payload)
        if not messages:
            return
        request_started_at = self._log_model_call_start(
            component="llm",
            model=self._model,
            stream=True,
            message_count=len(messages),
            message_chars=_message_chars(messages),
        )
        self._log_timing("openai_compat_stream_start", request_started_at, message_count=len(messages))
        first_chunk_logged = False
        thinking_controls = resolve_thinking_controls(
            stage=LLM_STAGE_STAGE4_FINAL_ANSWER,
            max_tokens=self._max_tokens,
            stream=True,
        )
        request_kwargs: dict[str, Any] = {
            "headers": self._headers(),
            "json": self._build_payload(
                model=self._model,
                messages=messages,
                stream=True,
                temperature=self._temperature,
                top_p=self._top_p,
                max_tokens=thinking_controls.max_tokens,
                extra_body=thinking_controls.extra_body,
                reasoning_effort=thinking_controls.reasoning_effort,
                omit_sampling_parameters=thinking_controls.enabled,
            ),
        }
        request_timeout = self._build_timeout(
            timeout=timeout,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=self._stream_read_timeout_seconds(read_timeout_seconds),
            write_timeout_seconds=write_timeout_seconds,
            pool_timeout_seconds=pool_timeout_seconds,
        )
        if request_timeout is not None:
            request_kwargs["timeout"] = request_timeout
        response = None
        try:
            stream_context = self._client.stream(
                "POST",
                self._cfg.endpoint,
                **request_kwargs,
            )
        except Exception as exc:
            self._log_model_call_failed(
                component="llm",
                model=self._model,
                started_at=request_started_at,
                exc=exc,
                status_code=getattr(response, "status_code", None),
                stream=True,
            )
            self._handle_transport_error(stage="openai_compat_stream_error", started_at=request_started_at, exc=exc)
        try:
            with stream_context as response:
                try:
                    response.raise_for_status()
                except Exception as exc:
                    self._log_auth_failure(model=self._model, status_code=getattr(response, "status_code", None), exc=exc)
                    self._log_model_call_failed(
                        component="llm",
                        model=self._model,
                        started_at=request_started_at,
                        exc=exc,
                        status_code=getattr(response, "status_code", None),
                        stream=True,
                    )
                    raise
                self._log_auth_success(model=self._model, status_code=getattr(response, "status_code", None))
                self._log_transport_success(
                    "openai_compat_stream_connected",
                    request_started_at,
                    message_count=len(messages),
                )
                iter_started_at = time.monotonic()
                reasoning_chars = 0
                chunk_count = 0
                answer_chars = 0
                for payload_json in self._iter_sse_json(response):
                    choices = payload_json.get("choices") or []
                    if choices:
                        delta = (choices[0] or {}).get("delta") or {}
                        reasoning = delta.get("reasoning_content")
                        if reasoning:
                            reasoning_chars += len(str(reasoning))
                    content = extract_openai_compatible_text(payload_json)
                    if not content:
                        continue
                    chunk_count += 1
                    answer_chars += len(content)
                    if not first_chunk_logged:
                        self._log_timing("openai_compat_first_chunk", iter_started_at, first_chunk_chars=len(content))
                        first_chunk_logged = True
                    yield SimpleNamespace(content=content)
                if reasoning_chars and self._logger is not None:
                    self._logger.info("openai_compat_stream reasoning_chars=%s thinking_enabled=%s", reasoning_chars, thinking_controls.enabled)
                self._log_model_call_success(
                    component="llm",
                    model=self._model,
                    started_at=request_started_at,
                    status_code=getattr(response, "status_code", None),
                    stream=True,
                    answer_chars=answer_chars,
                    chunk_count=chunk_count,
                )
        except Exception as exc:
            self._log_model_call_failed(
                component="llm",
                model=self._model,
                started_at=request_started_at,
                exc=exc,
                status_code=getattr(response, "status_code", None),
                stream=True,
            )
            self._handle_transport_error(stage="openai_compat_stream_error", started_at=request_started_at, exc=exc)


class _CompatCompletions:
    def __init__(self, parent: "OpenAICompatClient") -> None:
        self._parent = parent

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        stream: bool = False,
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        extra_body: Mapping[str, Any] | None = None,
        reasoning_effort: str | None = None,
        omit_sampling_parameters: bool = False,
        response_format: Any | None = None,
        timeout: Any | None = None,
        connect_timeout_seconds: float | None = None,
        read_timeout_seconds: float | None = None,
        write_timeout_seconds: float | None = None,
        pool_timeout_seconds: float | None = None,
        **_kwargs: Any,
    ) -> Any:
        normalized = normalize_messages(messages)
        if stream:
            return self._parent._stream(
                model=model,
                messages=normalized,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                extra_body=extra_body,
                reasoning_effort=reasoning_effort,
                omit_sampling_parameters=omit_sampling_parameters,
                response_format=response_format,
                timeout=timeout,
                connect_timeout_seconds=connect_timeout_seconds,
                read_timeout_seconds=read_timeout_seconds,
                write_timeout_seconds=write_timeout_seconds,
                pool_timeout_seconds=pool_timeout_seconds,
            )
        return self._parent._invoke(
            model=model,
            messages=normalized,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            extra_body=extra_body,
            reasoning_effort=reasoning_effort,
            omit_sampling_parameters=omit_sampling_parameters,
            response_format=response_format,
            timeout=timeout,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            write_timeout_seconds=write_timeout_seconds,
            pool_timeout_seconds=pool_timeout_seconds,
        )


class _CompatChat:
    def __init__(self, parent: "OpenAICompatClient") -> None:
        self.completions = _CompatCompletions(parent)


class OpenAICompatClient(_OpenAICompatBase):
    def __init__(
        self,
        *,
        httpx_module: Any,
        endpoint: str,
        api_key: str,
        logger: Any | None = None,
        connect_timeout_seconds: float = 10.0,
        read_timeout_seconds: float = 65.0,
        stream_read_timeout_seconds: float = 600.0,
        write_timeout_seconds: float = 65.0,
        pool_timeout_seconds: float = 5.0,
        keepalive_expiry_seconds: float = 5.0,
        max_connections: int = 50,
        max_keepalive_connections: int = 20,
        http_client: Any | None = None,
    ) -> None:
        super().__init__(
            httpx_module=httpx_module,
            endpoint=endpoint,
            api_key=api_key,
            logger=logger,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            stream_read_timeout_seconds=stream_read_timeout_seconds,
            write_timeout_seconds=write_timeout_seconds,
            pool_timeout_seconds=pool_timeout_seconds,
            keepalive_expiry_seconds=keepalive_expiry_seconds,
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
            http_client=http_client,
        )
        self.chat = _CompatChat(self)

    def _invoke(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float | None,
        top_p: float | None,
        max_tokens: int | None,
        extra_body: Mapping[str, Any] | None,
        reasoning_effort: str | None,
        omit_sampling_parameters: bool,
        response_format: Any | None,
        timeout: Any | None,
        connect_timeout_seconds: float | None,
        read_timeout_seconds: float | None,
        write_timeout_seconds: float | None,
        pool_timeout_seconds: float | None,
    ) -> Any:
        started_at = self._log_model_call_start(
            component="llm",
            model=model,
            stream=False,
            message_count=len(messages),
            message_chars=_message_chars(messages),
        )
        request_kwargs: dict[str, Any] = {
            "headers": self._headers(),
            "json": self._build_payload(
                model=model,
                messages=messages,
                stream=False,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                extra_body=extra_body,
                reasoning_effort=reasoning_effort,
                omit_sampling_parameters=omit_sampling_parameters,
                response_format=response_format,
            ),
        }
        request_timeout = self._build_timeout(
            timeout=timeout,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            write_timeout_seconds=write_timeout_seconds,
            pool_timeout_seconds=pool_timeout_seconds,
        )
        if request_timeout is not None:
            request_kwargs["timeout"] = request_timeout
        response = None
        try:
            response = self._client.post(
                self._cfg.endpoint,
                **request_kwargs,
            )
        except Exception as exc:
            self._log_model_call_failed(
                component="llm",
                model=model,
                started_at=started_at,
                exc=exc,
                status_code=getattr(response, "status_code", None),
                stream=False,
            )
            self._handle_transport_error(stage="openai_compat_client_invoke_error", started_at=started_at, exc=exc)
        try:
            response.raise_for_status()
        except Exception as exc:
            self._log_auth_failure(model=model, status_code=getattr(response, "status_code", None), exc=exc)
            self._log_model_call_failed(
                component="llm",
                model=model,
                started_at=started_at,
                exc=exc,
                status_code=getattr(response, "status_code", None),
                stream=False,
            )
            raise
        self._log_auth_success(model=model, status_code=getattr(response, "status_code", None))
        body = response.json()
        content = extract_openai_compatible_text(body)
        self._log_model_call_success(
            component="llm",
            model=model,
            started_at=started_at,
            status_code=getattr(response, "status_code", None),
            stream=False,
            answer_chars=len(content),
        )
        self._log_transport_success(
            "openai_compat_client_invoke_done",
            started_at,
            message_count=len(messages),
            answer_chars=len(content),
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    def _stream(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float | None,
        top_p: float | None,
        max_tokens: int | None,
        extra_body: Mapping[str, Any] | None,
        reasoning_effort: str | None,
        omit_sampling_parameters: bool,
        response_format: Any | None,
        timeout: Any | None,
        connect_timeout_seconds: float | None,
        read_timeout_seconds: float | None,
        write_timeout_seconds: float | None,
        pool_timeout_seconds: float | None,
    ) -> Iterator[Any]:
        request_started_at = self._log_model_call_start(
            component="llm",
            model=model,
            stream=True,
            message_count=len(messages),
            message_chars=_message_chars(messages),
        )
        self._log_timing("openai_compat_client_stream_start", request_started_at, message_count=len(messages))
        first_chunk_logged = False
        request_kwargs: dict[str, Any] = {
            "headers": self._headers(),
            "json": self._build_payload(
                model=model,
                messages=messages,
                stream=True,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                extra_body=extra_body,
                reasoning_effort=reasoning_effort,
                omit_sampling_parameters=omit_sampling_parameters,
                response_format=response_format,
            ),
        }
        request_timeout = self._build_timeout(
            timeout=timeout,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=self._stream_read_timeout_seconds(read_timeout_seconds),
            write_timeout_seconds=write_timeout_seconds,
            pool_timeout_seconds=pool_timeout_seconds,
        )
        if request_timeout is not None:
            request_kwargs["timeout"] = request_timeout
        response = None
        try:
            stream_context = self._client.stream(
                "POST",
                self._cfg.endpoint,
                **request_kwargs,
            )
        except Exception as exc:
            self._log_model_call_failed(
                component="llm",
                model=model,
                started_at=request_started_at,
                exc=exc,
                status_code=getattr(response, "status_code", None),
                stream=True,
            )
            self._handle_transport_error(stage="openai_compat_client_stream_error", started_at=request_started_at, exc=exc)
        try:
            with stream_context as response:
                try:
                    response.raise_for_status()
                except Exception as exc:
                    self._log_auth_failure(model=model, status_code=getattr(response, "status_code", None), exc=exc)
                    self._log_model_call_failed(
                        component="llm",
                        model=model,
                        started_at=request_started_at,
                        exc=exc,
                        status_code=getattr(response, "status_code", None),
                        stream=True,
                    )
                    raise
                self._log_auth_success(model=model, status_code=getattr(response, "status_code", None))
                self._log_transport_success(
                    "openai_compat_client_stream_connected",
                    request_started_at,
                    message_count=len(messages),
                )
                iter_started_at = time.monotonic()
                chunk_count = 0
                answer_chars = 0
                for payload_json in self._iter_sse_json(response):
                    content = extract_openai_compatible_text(payload_json)
                    if not content:
                        continue
                    chunk_count += 1
                    answer_chars += len(content)
                    if not first_chunk_logged:
                        self._log_timing("openai_compat_client_first_chunk", iter_started_at, first_chunk_chars=len(content))
                        first_chunk_logged = True
                    yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])
                self._log_model_call_success(
                    component="llm",
                    model=model,
                    started_at=request_started_at,
                    status_code=getattr(response, "status_code", None),
                    stream=True,
                    answer_chars=answer_chars,
                    chunk_count=chunk_count,
                )
        except Exception as exc:
            self._log_model_call_failed(
                component="llm",
                model=model,
                started_at=request_started_at,
                exc=exc,
                status_code=getattr(response, "status_code", None),
                stream=True,
            )
            self._handle_transport_error(stage="openai_compat_client_stream_error", started_at=request_started_at, exc=exc)


def build_chat_adapter(
    *,
    api_key: str,
    base_url: str,
    model: str,
    temperature: float = 0.5,
    top_p: float = 0.95,
    max_tokens: int = 4096,
    logger: Any | None = None,
    connect_timeout_seconds: float = 10.0,
    read_timeout_seconds: float = 65.0,
    stream_read_timeout_seconds: float = 600.0,
    write_timeout_seconds: float = 65.0,
    pool_timeout_seconds: float = 5.0,
    keepalive_expiry_seconds: float = 5.0,
    max_connections: int = 50,
    max_keepalive_connections: int = 20,
    http_client: Any | None = None,
) -> OpenAICompatChatAdapter:
    import httpx

    return OpenAICompatChatAdapter(
        httpx_module=httpx,
        endpoint=normalize_openai_compatible_endpoint(base_url),
        api_key=api_key,
        model=model,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        logger=logger,
        connect_timeout_seconds=connect_timeout_seconds,
        read_timeout_seconds=read_timeout_seconds,
        stream_read_timeout_seconds=stream_read_timeout_seconds,
        write_timeout_seconds=write_timeout_seconds,
        pool_timeout_seconds=pool_timeout_seconds,
        keepalive_expiry_seconds=keepalive_expiry_seconds,
        max_connections=max_connections,
        max_keepalive_connections=max_keepalive_connections,
        http_client=http_client,
    )


def build_chat_completions_client(
    *,
    api_key: str,
    base_url: str,
    logger: Any | None = None,
    connect_timeout_seconds: float = 10.0,
    read_timeout_seconds: float = 65.0,
    stream_read_timeout_seconds: float = 600.0,
    write_timeout_seconds: float = 65.0,
    pool_timeout_seconds: float = 5.0,
    keepalive_expiry_seconds: float = 5.0,
    max_connections: int = 50,
    max_keepalive_connections: int = 20,
    http_client: Any | None = None,
) -> OpenAICompatClient:
    import httpx

    return OpenAICompatClient(
        httpx_module=httpx,
        endpoint=normalize_openai_compatible_endpoint(base_url),
        api_key=api_key,
        logger=logger,
        connect_timeout_seconds=connect_timeout_seconds,
        read_timeout_seconds=read_timeout_seconds,
        stream_read_timeout_seconds=stream_read_timeout_seconds,
        write_timeout_seconds=write_timeout_seconds,
        pool_timeout_seconds=pool_timeout_seconds,
        keepalive_expiry_seconds=keepalive_expiry_seconds,
        max_connections=max_connections,
        max_keepalive_connections=max_keepalive_connections,
        http_client=http_client,
    )
