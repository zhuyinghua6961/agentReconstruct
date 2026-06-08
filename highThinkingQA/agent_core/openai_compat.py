from __future__ import annotations

import json
import logging
import time
from types import SimpleNamespace
from typing import Any, AsyncIterator, Iterator, Mapping

import httpx

from agent_core.thinking import auth_headers, resolve_auth_mode

DEFAULT_LLM_COMPATIBLE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
_LOGGER = logging.getLogger(__name__)


def normalize_openai_compatible_endpoint(base_url: str | None) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        value = DEFAULT_LLM_COMPATIBLE_BASE_URL
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if value.endswith(suffix):
            value = value[: -len(suffix)].rstrip("/")
            break
    if not value.endswith("/v1"):
        value = value.rstrip("/") + "/v1"
    return value.rstrip("/") + "/chat/completions"


def normalize_openai_compatible_embedding_endpoint(base_url: str | None) -> str:
    value = str(base_url or "").strip().rstrip("/")
    if not value:
        value = DEFAULT_LLM_COMPATIBLE_BASE_URL
    for suffix in ("/v1/embeddings", "/embeddings"):
        if value.endswith(suffix):
            value = value[: -len(suffix)].rstrip("/")
            break
    if not value.endswith("/v1"):
        value = value.rstrip("/") + "/v1"
    return value.rstrip("/") + "/embeddings"


def _coerce_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return str(value)


def _build_payload(kwargs: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": kwargs.get("model"),
        "messages": list(kwargs.get("messages") or []),
        "stream": bool(kwargs.get("stream", False)),
    }
    for key in ("temperature", "top_p", "max_tokens", "reasoning_effort", "response_format"):
        if key in kwargs and kwargs[key] is not None:
            payload[key] = kwargs[key]
    extra_body = kwargs.get("extra_body")
    if isinstance(extra_body, Mapping):
        payload.update(dict(extra_body))
    return payload


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


def _message_count(messages: Any) -> int:
    if isinstance(messages, list):
        return len(messages)
    return 0


def _message_chars(messages: Any) -> int:
    if not isinstance(messages, list):
        return 0
    total = 0
    for item in messages:
        if isinstance(item, Mapping):
            total += len(_coerce_text(item.get("content")) or "")
    return total


def _input_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if value is None:
        return 0
    return 1


def _input_chars(value: Any) -> int:
    if isinstance(value, list):
        return sum(len(str(item or "")) for item in value)
    return len(str(value or ""))


def _embedding_shape(response: Any) -> tuple[int, int]:
    data = list(getattr(response, "data", None) or [])
    if not data:
        return 0, 0
    first_embedding = list(getattr(data[0], "embedding", None) or [])
    return len(data), len(first_embedding)


def _log_model_call_start(
    *,
    component: str,
    model: str,
    endpoint: str,
    auth_mode: str,
    stream: bool | None = None,
    message_count: int | None = None,
    message_chars: int | None = None,
    input_count: int | None = None,
    input_chars: int | None = None,
    dimensions: Any | None = None,
) -> float:
    started_at = time.perf_counter()
    fields = [
        "model_call start service=highThinkingQA",
        f"component={component}",
        f"model={model}",
        f"endpoint={endpoint}",
        f"auth_mode={auth_mode}",
    ]
    if stream is not None:
        fields.append(f"stream={_bool_text(stream)}")
    if message_count is not None:
        fields.append(f"message_count={message_count}")
    if message_chars is not None:
        fields.append(f"message_chars={message_chars}")
    if input_count is not None:
        fields.append(f"input_count={input_count}")
    if input_chars is not None:
        fields.append(f"input_chars={input_chars}")
    if dimensions is not None:
        fields.append(f"dimensions={dimensions}")
    _LOGGER.info(" ".join(fields))
    return started_at


def _log_model_call_success(
    *,
    component: str,
    model: str,
    endpoint: str,
    auth_mode: str,
    started_at: float,
    status_code: Any = None,
    stream: bool | None = None,
    answer_chars: int | None = None,
    chunk_count: int | None = None,
    embedding_count: int | None = None,
    embedding_dim: int | None = None,
) -> None:
    fields = [
        "model_call success service=highThinkingQA",
        f"component={component}",
        f"model={model}",
        f"endpoint={endpoint}",
        f"auth_mode={auth_mode}",
    ]
    if status_code is not None:
        fields.append(f"status_code={status_code}")
    if stream is not None:
        fields.append(f"stream={_bool_text(stream)}")
    if answer_chars is not None:
        fields.append(f"answer_chars={answer_chars}")
    if chunk_count is not None:
        fields.append(f"chunk_count={chunk_count}")
    if embedding_count is not None:
        fields.append(f"embedding_count={embedding_count}")
    if embedding_dim is not None:
        fields.append(f"embedding_dim={embedding_dim}")
    fields.append(f"elapsed_ms={(time.perf_counter() - started_at) * 1000.0:.2f}")
    _LOGGER.info(" ".join(fields))


def _log_model_call_failed(
    *,
    component: str,
    model: str,
    endpoint: str,
    auth_mode: str,
    started_at: float,
    exc: Exception,
    status_code: Any = None,
    stream: bool | None = None,
) -> None:
    fields = [
        "model_call failed service=highThinkingQA",
        f"component={component}",
        f"model={model}",
        f"endpoint={endpoint}",
        f"auth_mode={auth_mode}",
    ]
    if status_code is not None:
        fields.append(f"status_code={status_code}")
    if stream is not None:
        fields.append(f"stream={_bool_text(stream)}")
    fields.extend(
        [
            f"elapsed_ms={(time.perf_counter() - started_at) * 1000.0:.2f}",
            f"error_type={type(exc).__name__}",
        ]
    )
    _LOGGER.warning(" ".join(fields))


def _message_response(payload: Any) -> Any:
    data = payload if isinstance(payload, Mapping) else {}
    choices: list[Any] = []
    for choice in list(data.get("choices") or []):
        choice_data = choice if isinstance(choice, Mapping) else {}
        message_data = choice_data.get("message") if isinstance(choice_data.get("message"), Mapping) else {}
        message = SimpleNamespace(
            content=_coerce_text(message_data.get("content")) or "",
            reasoning_content=_coerce_text(message_data.get("reasoning_content")),
            model_extra={k: v for k, v in message_data.items() if k not in {"content", "reasoning_content"}},
        )
        choices.append(
            SimpleNamespace(
                message=message,
                finish_reason=choice_data.get("finish_reason"),
                index=choice_data.get("index", 0),
            )
        )
    return SimpleNamespace(
        choices=choices,
        model=data.get("model"),
        usage=data.get("usage"),
        id=data.get("id"),
    )


def _chunk_response(payload: Any) -> Any:
    data = payload if isinstance(payload, Mapping) else {}
    choices: list[Any] = []
    for choice in list(data.get("choices") or []):
        choice_data = choice if isinstance(choice, Mapping) else {}
        delta_data = choice_data.get("delta") if isinstance(choice_data.get("delta"), Mapping) else {}
        delta = SimpleNamespace(
            content=_coerce_text(delta_data.get("content")),
            reasoning_content=_coerce_text(delta_data.get("reasoning_content")),
            model_extra={k: v for k, v in delta_data.items() if k not in {"content", "reasoning_content"}},
        )
        choices.append(
            SimpleNamespace(
                delta=delta,
                finish_reason=choice_data.get("finish_reason"),
                index=choice_data.get("index", 0),
            )
        )
    return SimpleNamespace(choices=choices, model=data.get("model"), id=data.get("id"))


def _embedding_response(payload: Any) -> Any:
    data = payload if isinstance(payload, Mapping) else {}
    items: list[Any] = []
    for index, item in enumerate(list(data.get("data") or [])):
        item_data = item if isinstance(item, Mapping) else {}
        items.append(
            SimpleNamespace(
                embedding=list(item_data.get("embedding") or []),
                index=item_data.get("index", index),
                object=item_data.get("object"),
            )
        )
    if not items and isinstance(data.get("embedding"), list):
        items.append(SimpleNamespace(embedding=list(data.get("embedding") or []), index=0, object=None))
    return SimpleNamespace(data=items, model=data.get("model"), usage=data.get("usage"), id=data.get("id"))


def _build_embedding_payload(kwargs: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": kwargs.get("model"),
        "input": kwargs.get("input"),
    }
    for key in ("dimensions", "encoding_format", "user"):
        if key in kwargs and kwargs[key] is not None:
            payload[key] = kwargs[key]
    return payload


def _iter_sse_payloads(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    for raw_line in lines:
        line = str(raw_line or "").strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            yield dict(payload)


async def _aiter_sse_payloads(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    async for raw_line in lines:
        line = str(raw_line or "").strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            yield dict(payload)


class _SyncCompletions:
    def __init__(self, parent: "OpenAICompatibleChatClient") -> None:
        self._parent = parent

    def create(self, **kwargs: Any) -> Any:
        return self._parent._create(**kwargs)


class _AsyncCompletions:
    def __init__(self, parent: "AsyncOpenAICompatibleChatClient") -> None:
        self._parent = parent

    async def create(self, **kwargs: Any) -> Any:
        return await self._parent._create(**kwargs)


class _SyncEmbeddings:
    def __init__(self, parent: "OpenAICompatibleEmbeddingClient") -> None:
        self._parent = parent

    def create(self, **kwargs: Any) -> Any:
        return self._parent._create(**kwargs)


class OpenAICompatibleChatClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        auth_mode: str | None = None,
        http_client: Any | None = None,
        timeout_seconds: float = 60.0,
        max_retries: int | None = None,
    ) -> None:
        del max_retries
        self.endpoint = normalize_openai_compatible_endpoint(base_url)
        self.api_key = str(api_key or "")
        self.auth_mode = auth_mode
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=float(timeout_seconds), http2=False)
        self.chat = SimpleNamespace(completions=_SyncCompletions(self))

    def close(self) -> None:
        if not self._owns_client:
            return
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def _headers(self) -> dict[str, str]:
        return auth_headers(self.api_key, accept="application/json, text/event-stream", auth_mode=self.auth_mode)

    def _auth_mode(self) -> str:
        return resolve_auth_mode(self.auth_mode)

    def _create(self, **kwargs: Any) -> Any:
        payload = _build_payload(kwargs)
        timeout = kwargs.get("timeout")
        if payload.get("stream"):
            return self._stream(payload=payload, timeout=timeout)
        request_kwargs: dict[str, Any] = {"headers": self._headers(), "json": payload}
        if timeout is not None:
            request_kwargs["timeout"] = float(timeout)
        started_at = _log_model_call_start(
            component="llm",
            model=str(payload.get("model") or ""),
            endpoint=self.endpoint,
            auth_mode=self._auth_mode(),
            stream=False,
            message_count=_message_count(payload.get("messages")),
            message_chars=_message_chars(payload.get("messages")),
        )
        response = None
        try:
            response = self._client.post(self.endpoint, **request_kwargs)
            response.raise_for_status()
            parsed = _message_response(response.json())
        except Exception as exc:
            _log_model_call_failed(
                component="llm",
                model=str(payload.get("model") or ""),
                endpoint=self.endpoint,
                auth_mode=self._auth_mode(),
                started_at=started_at,
                exc=exc,
                status_code=getattr(response, "status_code", None),
                stream=False,
            )
            raise
        answer = parsed.choices[0].message.content if getattr(parsed, "choices", None) else ""
        _log_model_call_success(
            component="llm",
            model=str(payload.get("model") or ""),
            endpoint=self.endpoint,
            auth_mode=self._auth_mode(),
            started_at=started_at,
            status_code=getattr(response, "status_code", None),
            stream=False,
            answer_chars=len(str(answer or "")),
        )
        return parsed

    def _stream(self, *, payload: dict[str, Any], timeout: Any | None = None) -> Iterator[Any]:
        request_kwargs: dict[str, Any] = {"headers": self._headers(), "json": payload}
        if timeout is not None:
            request_kwargs["timeout"] = float(timeout)
        started_at = _log_model_call_start(
            component="llm",
            model=str(payload.get("model") or ""),
            endpoint=self.endpoint,
            auth_mode=self._auth_mode(),
            stream=True,
            message_count=_message_count(payload.get("messages")),
            message_chars=_message_chars(payload.get("messages")),
        )
        chunk_count = 0
        answer_chars = 0
        response = None
        try:
            with self._client.stream("POST", self.endpoint, **request_kwargs) as response:
                response.raise_for_status()
                for item in _iter_sse_payloads(response.iter_lines()):
                    chunk = _chunk_response(item)
                    content = None
                    if getattr(chunk, "choices", None):
                        content = getattr(chunk.choices[0].delta, "content", None)
                    if content:
                        chunk_count += 1
                        answer_chars += len(str(content))
                    yield chunk
        except Exception as exc:
            _log_model_call_failed(
                component="llm",
                model=str(payload.get("model") or ""),
                endpoint=self.endpoint,
                auth_mode=self._auth_mode(),
                started_at=started_at,
                exc=exc,
                status_code=getattr(response, "status_code", None),
                stream=True,
            )
            raise
        _log_model_call_success(
            component="llm",
            model=str(payload.get("model") or ""),
            endpoint=self.endpoint,
            auth_mode=self._auth_mode(),
            started_at=started_at,
            status_code=getattr(response, "status_code", None),
            stream=True,
            answer_chars=answer_chars,
            chunk_count=chunk_count,
        )


class AsyncOpenAICompatibleChatClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        auth_mode: str | None = None,
        http_client: Any | None = None,
        timeout_seconds: float = 60.0,
        max_retries: int | None = None,
    ) -> None:
        del max_retries
        self.endpoint = normalize_openai_compatible_endpoint(base_url)
        self.api_key = str(api_key or "")
        self.auth_mode = auth_mode
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=float(timeout_seconds), http2=False)
        self.chat = SimpleNamespace(completions=_AsyncCompletions(self))

    async def aclose(self) -> None:
        if not self._owns_client:
            return
        close = getattr(self._client, "aclose", None)
        if callable(close):
            await close()

    def _headers(self) -> dict[str, str]:
        return auth_headers(self.api_key, accept="application/json, text/event-stream", auth_mode=self.auth_mode)

    def _auth_mode(self) -> str:
        return resolve_auth_mode(self.auth_mode)

    async def _create(self, **kwargs: Any) -> Any:
        payload = _build_payload(kwargs)
        timeout = kwargs.get("timeout")
        if payload.get("stream"):
            return self._stream(payload=payload, timeout=timeout)
        request_kwargs: dict[str, Any] = {"headers": self._headers(), "json": payload}
        if timeout is not None:
            request_kwargs["timeout"] = float(timeout)
        started_at = _log_model_call_start(
            component="llm",
            model=str(payload.get("model") or ""),
            endpoint=self.endpoint,
            auth_mode=self._auth_mode(),
            stream=False,
            message_count=_message_count(payload.get("messages")),
            message_chars=_message_chars(payload.get("messages")),
        )
        response = None
        try:
            response = await self._client.post(self.endpoint, **request_kwargs)
            response.raise_for_status()
            parsed = _message_response(response.json())
        except Exception as exc:
            _log_model_call_failed(
                component="llm",
                model=str(payload.get("model") or ""),
                endpoint=self.endpoint,
                auth_mode=self._auth_mode(),
                started_at=started_at,
                exc=exc,
                status_code=getattr(response, "status_code", None),
                stream=False,
            )
            raise
        answer = parsed.choices[0].message.content if getattr(parsed, "choices", None) else ""
        _log_model_call_success(
            component="llm",
            model=str(payload.get("model") or ""),
            endpoint=self.endpoint,
            auth_mode=self._auth_mode(),
            started_at=started_at,
            status_code=getattr(response, "status_code", None),
            stream=False,
            answer_chars=len(str(answer or "")),
        )
        return parsed

    async def _stream(self, *, payload: dict[str, Any], timeout: Any | None = None) -> AsyncIterator[Any]:
        request_kwargs: dict[str, Any] = {"headers": self._headers(), "json": payload}
        if timeout is not None:
            request_kwargs["timeout"] = float(timeout)
        started_at = _log_model_call_start(
            component="llm",
            model=str(payload.get("model") or ""),
            endpoint=self.endpoint,
            auth_mode=self._auth_mode(),
            stream=True,
            message_count=_message_count(payload.get("messages")),
            message_chars=_message_chars(payload.get("messages")),
        )
        chunk_count = 0
        answer_chars = 0
        response = None
        try:
            async with self._client.stream("POST", self.endpoint, **request_kwargs) as response:
                response.raise_for_status()
                async for item in _aiter_sse_payloads(response.aiter_lines()):
                    chunk = _chunk_response(item)
                    content = None
                    if getattr(chunk, "choices", None):
                        content = getattr(chunk.choices[0].delta, "content", None)
                    if content:
                        chunk_count += 1
                        answer_chars += len(str(content))
                    yield chunk
        except Exception as exc:
            _log_model_call_failed(
                component="llm",
                model=str(payload.get("model") or ""),
                endpoint=self.endpoint,
                auth_mode=self._auth_mode(),
                started_at=started_at,
                exc=exc,
                status_code=getattr(response, "status_code", None),
                stream=True,
            )
            raise
        _log_model_call_success(
            component="llm",
            model=str(payload.get("model") or ""),
            endpoint=self.endpoint,
            auth_mode=self._auth_mode(),
            started_at=started_at,
            status_code=getattr(response, "status_code", None),
            stream=True,
            answer_chars=answer_chars,
            chunk_count=chunk_count,
        )


class OpenAICompatibleEmbeddingClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        auth_mode: str | None = None,
        http_client: Any | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int | None = None,
    ) -> None:
        del max_retries
        self.endpoint = normalize_openai_compatible_embedding_endpoint(base_url)
        self.api_key = str(api_key or "")
        self.auth_mode = auth_mode
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=float(timeout_seconds), http2=False)
        self.embeddings = SimpleNamespace(create=_SyncEmbeddings(self).create)

    def close(self) -> None:
        if not self._owns_client:
            return
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    def _headers(self) -> dict[str, str]:
        return auth_headers(self.api_key, accept="application/json", auth_mode=self.auth_mode)

    def _auth_mode(self) -> str:
        return resolve_auth_mode(self.auth_mode)

    def _create(self, **kwargs: Any) -> Any:
        payload = _build_embedding_payload(kwargs)
        request_kwargs: dict[str, Any] = {"headers": self._headers(), "json": payload}
        timeout = kwargs.get("timeout")
        if timeout is not None:
            request_kwargs["timeout"] = float(timeout)
        started_at = _log_model_call_start(
            component="embedding",
            model=str(payload.get("model") or ""),
            endpoint=self.endpoint,
            auth_mode=self._auth_mode(),
            input_count=_input_count(payload.get("input")),
            input_chars=_input_chars(payload.get("input")),
            dimensions=payload.get("dimensions"),
        )
        response = None
        try:
            response = self._client.post(self.endpoint, **request_kwargs)
            response.raise_for_status()
            parsed = _embedding_response(response.json())
        except Exception as exc:
            _log_model_call_failed(
                component="embedding",
                model=str(payload.get("model") or ""),
                endpoint=self.endpoint,
                auth_mode=self._auth_mode(),
                started_at=started_at,
                exc=exc,
                status_code=getattr(response, "status_code", None),
            )
            raise
        embedding_count, embedding_dim = _embedding_shape(parsed)
        _log_model_call_success(
            component="embedding",
            model=str(payload.get("model") or ""),
            endpoint=self.endpoint,
            auth_mode=self._auth_mode(),
            started_at=started_at,
            status_code=getattr(response, "status_code", None),
            embedding_count=embedding_count,
            embedding_dim=embedding_dim,
        )
        return parsed
