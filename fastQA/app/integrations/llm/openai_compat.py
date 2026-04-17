from __future__ import annotations

import json
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterable, Iterator, Mapping

from app.core.logging import beijing_now_iso


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
        value = "https://api.openai.com/v1"
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


@dataclass(frozen=True)
class _ClientConfig:
    endpoint: str
    api_key: str
    connect_timeout_seconds: float
    read_timeout_seconds: float
    write_timeout_seconds: float
    pool_timeout_seconds: float
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
        write_timeout_seconds: float = 65.0,
        pool_timeout_seconds: float = 5.0,
        max_connections: int = 50,
        max_keepalive_connections: int = 20,
    ) -> None:
        super().__init__(logger=logger)
        self._httpx = httpx_module
        self._cfg = _ClientConfig(
            endpoint=endpoint,
            api_key=api_key,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            write_timeout_seconds=write_timeout_seconds,
            pool_timeout_seconds=pool_timeout_seconds,
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        )
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
        )
        self._client = self._httpx.Client(timeout=timeout, limits=limits, http2=False)

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

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
        response_format: Any | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if top_p is not None:
            payload["top_p"] = top_p
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
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
        write_timeout_seconds: float = 65.0,
        pool_timeout_seconds: float = 5.0,
    ) -> None:
        super().__init__(
            httpx_module=httpx_module,
            endpoint=endpoint,
            api_key=api_key,
            logger=logger,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            write_timeout_seconds=write_timeout_seconds,
            pool_timeout_seconds=pool_timeout_seconds,
        )
        self._model = model
        self._temperature = temperature
        self._top_p = top_p
        self._max_tokens = max_tokens

    def invoke(self, payload: Any) -> Any:
        messages = normalize_messages(payload)
        if not messages:
            return SimpleNamespace(content="")
        started_at = time.monotonic()
        response = self._client.post(
            self._cfg.endpoint,
            headers=self._headers(),
            json=self._build_payload(
                model=self._model,
                messages=messages,
                stream=False,
                temperature=self._temperature,
                top_p=self._top_p,
                max_tokens=self._max_tokens,
            ),
        )
        response.raise_for_status()
        body = response.json()
        content = extract_openai_compatible_text(body)
        self._log_timing("openai_compat_invoke_done", started_at, message_count=len(messages), answer_chars=len(content))
        return SimpleNamespace(content=content)

    def stream(self, payload: Any) -> Iterator[Any]:
        messages = normalize_messages(payload)
        if not messages:
            return
        request_started_at = time.monotonic()
        self._log_timing("openai_compat_stream_start", request_started_at, message_count=len(messages))
        first_chunk_logged = False
        with self._client.stream(
            "POST",
            self._cfg.endpoint,
            headers=self._headers(),
            json=self._build_payload(
                model=self._model,
                messages=messages,
                stream=True,
                temperature=self._temperature,
                top_p=self._top_p,
                max_tokens=self._max_tokens,
            ),
        ) as response:
            response.raise_for_status()
            self._log_timing("openai_compat_stream_connected", request_started_at, message_count=len(messages))
            iter_started_at = time.monotonic()
            for payload_json in self._iter_sse_json(response):
                content = extract_openai_compatible_text(payload_json)
                if not content:
                    continue
                if not first_chunk_logged:
                    self._log_timing("openai_compat_first_chunk", iter_started_at, first_chunk_chars=len(content))
                    first_chunk_logged = True
                yield SimpleNamespace(content=content)


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
        response_format: Any | None = None,
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
                response_format=response_format,
            )
        return self._parent._invoke(
            model=model,
            messages=normalized,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            extra_body=extra_body,
            response_format=response_format,
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
        write_timeout_seconds: float = 65.0,
        pool_timeout_seconds: float = 5.0,
    ) -> None:
        super().__init__(
            httpx_module=httpx_module,
            endpoint=endpoint,
            api_key=api_key,
            logger=logger,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            write_timeout_seconds=write_timeout_seconds,
            pool_timeout_seconds=pool_timeout_seconds,
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
        response_format: Any | None,
    ) -> Any:
        started_at = time.monotonic()
        response = self._client.post(
            self._cfg.endpoint,
            headers=self._headers(),
            json=self._build_payload(
                model=model,
                messages=messages,
                stream=False,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                extra_body=extra_body,
                response_format=response_format,
            ),
        )
        response.raise_for_status()
        body = response.json()
        content = extract_openai_compatible_text(body)
        self._log_timing("openai_compat_client_invoke_done", started_at, message_count=len(messages), answer_chars=len(content))
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
        response_format: Any | None,
    ) -> Iterator[Any]:
        request_started_at = time.monotonic()
        self._log_timing("openai_compat_client_stream_start", request_started_at, message_count=len(messages))
        first_chunk_logged = False
        with self._client.stream(
            "POST",
            self._cfg.endpoint,
            headers=self._headers(),
            json=self._build_payload(
                model=model,
                messages=messages,
                stream=True,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                extra_body=extra_body,
                response_format=response_format,
            ),
        ) as response:
            response.raise_for_status()
            self._log_timing("openai_compat_client_stream_connected", request_started_at, message_count=len(messages))
            iter_started_at = time.monotonic()
            for payload_json in self._iter_sse_json(response):
                content = extract_openai_compatible_text(payload_json)
                if not content:
                    continue
                if not first_chunk_logged:
                    self._log_timing("openai_compat_client_first_chunk", iter_started_at, first_chunk_chars=len(content))
                    first_chunk_logged = True
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


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
    write_timeout_seconds: float = 65.0,
    pool_timeout_seconds: float = 5.0,
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
        write_timeout_seconds=write_timeout_seconds,
        pool_timeout_seconds=pool_timeout_seconds,
    )


def build_chat_completions_client(
    *,
    api_key: str,
    base_url: str,
    logger: Any | None = None,
    connect_timeout_seconds: float = 10.0,
    read_timeout_seconds: float = 65.0,
    write_timeout_seconds: float = 65.0,
    pool_timeout_seconds: float = 5.0,
) -> OpenAICompatClient:
    import httpx

    return OpenAICompatClient(
        httpx_module=httpx,
        endpoint=normalize_openai_compatible_endpoint(base_url),
        api_key=api_key,
        logger=logger,
        connect_timeout_seconds=connect_timeout_seconds,
        read_timeout_seconds=read_timeout_seconds,
        write_timeout_seconds=write_timeout_seconds,
        pool_timeout_seconds=pool_timeout_seconds,
    )
