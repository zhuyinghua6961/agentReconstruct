from __future__ import annotations

import logging
from types import SimpleNamespace

from agent_core.llm_client import chat_completion, chat_completion_stream, get_async_llm_client, get_llm_client
from agent_core.openai_compat import (
    OpenAICompatibleChatClient,
    OpenAICompatibleEmbeddingClient,
    normalize_openai_compatible_embedding_endpoint,
    normalize_openai_compatible_endpoint,
)
from agent_core.upstream_auth_logging import reset_upstream_auth_log_state_for_tests
from agent_core.thinking import LLM_STAGE_STAGE4_FINAL_ANSWER, auth_headers, local_sdk_api_key, resolve_auth_mode
from server.services.documents_service import DocumentsService


class _FakeCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok", reasoning_content=None))]
        )


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, completions):
        self.chat = _FakeChat(completions)


class _FakeStreamCompletions(_FakeCompletions):
    def __init__(self, chunks):
        super().__init__()
        self._chunks = chunks

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        return iter(self._chunks)


class _FakeUnauthorizedCompletions(_FakeCompletions):
    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        exc = RuntimeError("401 Unauthorized")
        exc.status_code = 401
        raise exc


class _FakeHttpResponse:
    def __init__(self, *, payload=None, lines=None, status_code: int = 200) -> None:
        self._payload = payload or {}
        self._lines = list(lines or [])
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            exc = RuntimeError(f"http {self.status_code}")
            exc.status_code = self.status_code
            raise exc

    def json(self):
        return self._payload

    def iter_lines(self):
        yield from self._lines

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class _FakeHttpClient:
    def __init__(self, *, post_response: _FakeHttpResponse, stream_response: _FakeHttpResponse | None = None) -> None:
        self.post_response = post_response
        self.stream_response = stream_response or _FakeHttpResponse(lines=["data: [DONE]"])
        self.calls: list[tuple[str, str, dict]] = []

    def post(self, url: str, **kwargs):
        self.calls.append(("post", url, kwargs))
        return self.post_response

    def stream(self, method: str, url: str, **kwargs):
        self.calls.append((method.lower(), url, kwargs))
        return self.stream_response


def test_highthinking_auth_headers_supports_configurable_auth_modes(monkeypatch):
    monkeypatch.setenv("LLM_AUTH_MODE", "authorization")
    assert resolve_auth_mode() == "authorization"
    assert auth_headers("Bearer token")["Authorization"] == "token"

    assert auth_headers("Bearer token", auth_mode="bearer")["Authorization"] == "Bearer token"
    assert auth_headers("Bearer token", auth_mode="x-api-key")["X-API-Key"] == "token"
    assert "Authorization" not in auth_headers("Bearer token", auth_mode="none")


def test_openai_compatible_client_uses_configurable_auth_and_endpoint():
    response = _FakeHttpResponse(payload={"choices": [{"message": {"content": "answer"}}]})
    http_client = _FakeHttpClient(post_response=response)
    client = OpenAICompatibleChatClient(
        base_url="https://llm.example/v1",
        api_key="Bearer token",
        auth_mode="authorization",
        http_client=http_client,
    )

    result = client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])

    assert normalize_openai_compatible_endpoint("https://llm.example/v1") == "https://llm.example/v1/chat/completions"
    assert result.choices[0].message.content == "answer"
    method, url, kwargs = http_client.calls[0]
    assert method == "post"
    assert url == "https://llm.example/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "token"


def test_openai_compatible_client_logs_model_call_success(caplog):
    response = _FakeHttpResponse(payload={"choices": [{"message": {"content": "answer"}}]})
    http_client = _FakeHttpClient(post_response=response)
    client = OpenAICompatibleChatClient(
        base_url="https://llm.example/v1",
        api_key="Bearer token",
        auth_mode="authorization",
        http_client=http_client,
    )
    caplog.set_level(logging.INFO, logger="agent_core.openai_compat")

    client.chat.completions.create(model="qwen", messages=[{"role": "user", "content": "hi"}])

    messages = [record.message for record in caplog.records]
    assert any(
        "model_call start" in message
        and "service=highThinkingQA" in message
        and "component=llm" in message
        and "model=qwen" in message
        and "auth_mode=authorization" in message
        and "message_count=1" in message
        and "stream=false" in message
        for message in messages
    )
    assert any(
        "model_call success" in message
        and "component=llm" in message
        and "status_code=200" in message
        and "answer_chars=6" in message
        and "elapsed_ms=" in message
        for message in messages
    )


def test_openai_compatible_client_stream_parses_content_and_ignores_reasoning():
    stream_response = _FakeHttpResponse(
        lines=[
            'data: {"choices":[{"delta":{"reasoning_content":"hidden"}}]}',
            'data: {"choices":[{"delta":{"content":"ok"}}]}',
            "data: [DONE]",
        ]
    )
    http_client = _FakeHttpClient(post_response=_FakeHttpResponse(), stream_response=stream_response)
    client = OpenAICompatibleChatClient(
        base_url="https://llm.example/v1/chat/completions",
        api_key="token",
        auth_mode="bearer",
        http_client=http_client,
    )

    chunks = list(client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}], stream=True))

    assert [chunk.choices[0].delta.content for chunk in chunks] == [None, "ok"]
    assert chunks[0].choices[0].delta.reasoning_content == "hidden"


def test_openai_compatible_client_logs_stream_model_call(caplog):
    stream_response = _FakeHttpResponse(
        lines=[
            'data: {"choices":[{"delta":{"content":"ok"}}]}',
            "data: [DONE]",
        ]
    )
    http_client = _FakeHttpClient(post_response=_FakeHttpResponse(), stream_response=stream_response)
    client = OpenAICompatibleChatClient(
        base_url="https://llm.example/v1/chat/completions",
        api_key="token",
        auth_mode="bearer",
        http_client=http_client,
    )
    caplog.set_level(logging.INFO, logger="agent_core.openai_compat")

    chunks = list(client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}], stream=True))

    messages = [record.message for record in caplog.records]
    assert [chunk.choices[0].delta.content for chunk in chunks] == ["ok"]
    assert any("model_call start" in message and "component=llm" in message and "stream=true" in message for message in messages)
    assert any(
        "model_call success" in message
        and "component=llm" in message
        and "stream=true" in message
        and "chunk_count=1" in message
        and "answer_chars=2" in message
        for message in messages
    )


def test_openai_compatible_embedding_client_normalizes_bearer_and_endpoint():
    response = _FakeHttpResponse(payload={"data": [{"embedding": [0.1, 0.2]}]})
    http_client = _FakeHttpClient(post_response=response)
    client = OpenAICompatibleEmbeddingClient(
        base_url="https://embedding.example/v1",
        api_key="Bearer embedding-token",
        auth_mode="bearer",
        http_client=http_client,
    )

    result = client.embeddings.create(model="m", input=["hello"], dimensions=2, encoding_format="float")

    assert normalize_openai_compatible_embedding_endpoint("https://embedding.example/v1") == "https://embedding.example/v1/embeddings"
    assert result.data[0].embedding == [0.1, 0.2]
    method, url, kwargs = http_client.calls[0]
    assert method == "post"
    assert url == "https://embedding.example/v1/embeddings"
    assert kwargs["headers"]["Authorization"] == "Bearer embedding-token"
    assert kwargs["json"]["dimensions"] == 2


def test_openai_compatible_embedding_client_logs_model_call_success(caplog):
    response = _FakeHttpResponse(payload={"data": [{"embedding": [0.1, 0.2]}]})
    http_client = _FakeHttpClient(post_response=response)
    client = OpenAICompatibleEmbeddingClient(
        base_url="https://embedding.example/v1",
        api_key="Bearer embedding-token",
        auth_mode="bearer",
        http_client=http_client,
    )
    caplog.set_level(logging.INFO, logger="agent_core.openai_compat")

    client.embeddings.create(model="m", input=["hello", "world"], encoding_format="float")

    messages = [record.message for record in caplog.records]
    assert any(
        "model_call start" in message
        and "service=highThinkingQA" in message
        and "component=embedding" in message
        and "model=m" in message
        and "input_count=2" in message
        for message in messages
    )
    assert any(
        "model_call success" in message
        and "component=embedding" in message
        and "status_code=200" in message
        and "embedding_count=1" in message
        and "embedding_dim=2" in message
        and "elapsed_ms=" in message
        for message in messages
    )


def test_openai_compatible_embedding_client_supports_x_api_key_auth_mode():
    response = _FakeHttpResponse(payload={"data": [{"embedding": [0.1]}]})
    http_client = _FakeHttpClient(post_response=response)
    client = OpenAICompatibleEmbeddingClient(
        base_url="https://embedding.example/v1/embeddings",
        api_key="Bearer embedding-token",
        auth_mode="x-api-key",
        http_client=http_client,
    )

    client.embeddings.create(model="m", input=["hello"])

    _, _, kwargs = http_client.calls[0]
    assert kwargs["headers"]["X-API-Key"] == "embedding-token"
    assert "Authorization" not in kwargs["headers"]


def test_chat_completion_forwards_timeout_to_sdk_call():
    completions = _FakeCompletions()
    client = _FakeClient(completions)

    result = chat_completion(
        prompt="demo",
        client=client,
        enable_thinking=False,
        timeout_seconds=12.5,
    )

    assert result == "ok"
    assert completions.calls[0]["timeout"] == 12.5


def test_chat_completion_logs_llm_auth_success_once(monkeypatch, caplog):
    reset_upstream_auth_log_state_for_tests()
    monkeypatch.setattr("agent_core.llm_client.config.LLM_MODEL", "demo-model", raising=False)
    monkeypatch.setattr("agent_core.llm_client.config.LLM_BASE_URL", "https://example.invalid/v1", raising=False)
    monkeypatch.setattr("agent_core.llm_client.config.LLM_API_KEY", "Bearer sk-demo-secret", raising=False)
    caplog.set_level(logging.INFO)
    completions = _FakeCompletions()
    client = _FakeClient(completions)

    assert chat_completion(prompt="demo", client=client, enable_thinking=False) == "ok"
    assert chat_completion(prompt="demo", client=client, enable_thinking=False) == "ok"

    messages = [record.message for record in caplog.records]
    auth_ok = [message for message in messages if "LLM upstream auth ok" in message]
    assert len(auth_ok) == 1
    assert "service=highThinkingQA" in auth_ok[0]
    assert "model=demo-model" in auth_ok[0]
    assert "key_present=True" in auth_ok[0]
    assert "key_input_has_bearer=True" in auth_ok[0]
    assert "sk-demo-secret" not in auth_ok[0]


def test_chat_completion_logs_llm_auth_failure_status(monkeypatch, caplog):
    reset_upstream_auth_log_state_for_tests()
    monkeypatch.setattr("agent_core.llm_client.config.LLM_MODEL", "demo-model", raising=False)
    monkeypatch.setattr("agent_core.llm_client.config.LLM_BASE_URL", "https://example.invalid/v1", raising=False)
    monkeypatch.setattr("agent_core.llm_client.config.LLM_API_KEY", "sk-demo-secret", raising=False)
    caplog.set_level(logging.WARNING)
    completions = _FakeUnauthorizedCompletions()
    client = _FakeClient(completions)

    try:
        chat_completion(prompt="demo", client=client, enable_thinking=False)
    except RuntimeError:
        pass

    messages = [record.message for record in caplog.records]
    auth_failed = [message for message in messages if "LLM upstream auth failed" in message]
    assert len(auth_failed) == 1
    assert "service=highThinkingQA" in auth_failed[0]
    assert "status_code=401" in auth_failed[0]
    assert "sk-demo-secret" not in auth_failed[0]


def test_chat_completion_omits_enable_thinking_for_non_stream_calls(monkeypatch):
    monkeypatch.setattr("agent_core.llm_client.config.LLM_IS_THINKING_MODEL", False, raising=False)
    monkeypatch.setattr("agent_core.llm_client.config.LLM_THINKING_ENABLED", False, raising=False)
    completions = _FakeCompletions()
    client = _FakeClient(completions)

    result = chat_completion(
        prompt="demo",
        client=client,
        enable_thinking=True,
    )

    assert result == "ok"
    call = completions.calls[0]
    assert call.get("stream") in (None, False)
    assert "extra_body" not in call
    assert call["temperature"] == 0.7


def test_chat_completion_disables_thinking_for_control_stage_when_model_supports_thinking(monkeypatch):
    monkeypatch.setattr("agent_core.llm_client.config.LLM_IS_THINKING_MODEL", True, raising=False)
    monkeypatch.setattr("agent_core.llm_client.config.LLM_THINKING_ENABLED", True, raising=False)
    completions = _FakeCompletions()
    client = _FakeClient(completions)

    result = chat_completion(
        prompt="demo",
        client=client,
        enable_thinking=True,
    )

    assert result == "ok"
    call = completions.calls[0]
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in call
    assert call["temperature"] == 0.7


def test_stage4_stream_enables_deepseek_thinking_and_drops_reasoning(monkeypatch):
    monkeypatch.setattr("agent_core.llm_client.config.LLM_IS_THINKING_MODEL", True, raising=False)
    monkeypatch.setattr("agent_core.llm_client.config.LLM_THINKING_ENABLED", True, raising=False)
    completions = _FakeStreamCompletions(
        [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, reasoning_content="secret"))]),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok", reasoning_content=None))]),
        ]
    )
    client = _FakeClient(completions)

    result = "".join(
        chat_completion_stream(
            prompt="demo",
            client=client,
            enable_thinking=True,
            stage=LLM_STAGE_STAGE4_FINAL_ANSWER,
            max_tokens=4096,
        )
    )

    assert result == "ok"
    call = completions.calls[0]
    assert call["stream"] is True
    assert call["extra_body"] == {"thinking": {"type": "enabled"}}
    assert call["reasoning_effort"] == "high"
    assert call["max_tokens"] == 8192
    assert "temperature" not in call


def test_stage4_stream_respects_global_thinking_disabled(monkeypatch):
    monkeypatch.setattr("agent_core.llm_client.config.LLM_IS_THINKING_MODEL", True, raising=False)
    monkeypatch.setattr("agent_core.llm_client.config.LLM_THINKING_ENABLED", False, raising=False)
    completions = _FakeStreamCompletions(
        [
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok", reasoning_content=None))]),
        ]
    )
    client = _FakeClient(completions)

    result = "".join(
        chat_completion_stream(
            prompt="demo",
            client=client,
            enable_thinking=True,
            stage=LLM_STAGE_STAGE4_FINAL_ANSWER,
            max_tokens=4096,
        )
    )

    assert result == "ok"
    call = completions.calls[0]
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in call
    assert call["max_tokens"] == 4096
    assert call["temperature"] == 0.7


def test_get_llm_client_accepts_max_retries_override(monkeypatch):
    monkeypatch.setattr("agent_core.llm_client.config.LLM_API_KEY", "masked")
    monkeypatch.setattr("agent_core.llm_client.config.LLM_BASE_URL", "https://example.invalid/v1")

    client = get_llm_client(max_retries=0)

    assert client is not None
    assert client.endpoint == "https://example.invalid/v1/chat/completions"


def test_local_sdk_api_key_accepts_bearer_prefixed_values():
    assert local_sdk_api_key("Bearer sk-demo") == "sk-demo"
    assert local_sdk_api_key("bearer sk-demo") == "sk-demo"
    assert local_sdk_api_key("  Bearer   sk-demo  ") == "sk-demo"


def test_get_llm_client_strips_bearer_prefix_before_sdk_adds_auth_header(monkeypatch):
    monkeypatch.setattr("agent_core.llm_client.config.LLM_API_KEY", "bearer sk-demo")
    monkeypatch.setattr("agent_core.llm_client.config.LLM_BASE_URL", "https://example.invalid/v1")

    client = get_llm_client()

    assert client is not None
    assert client._headers()["Authorization"] == "Bearer sk-demo"


def test_get_llm_client_omits_auth_for_blank_llm_api_key(monkeypatch):
    monkeypatch.setattr("agent_core.llm_client.config.LLM_API_KEY", "")
    monkeypatch.setattr("agent_core.llm_client.config.LLM_BASE_URL", "https://example.invalid/v1")

    client = get_llm_client()

    assert client is not None
    assert "Authorization" not in client._headers()


def test_get_async_llm_client_omits_auth_for_blank_llm_api_key(monkeypatch):
    monkeypatch.setattr("agent_core.llm_client.config.LLM_API_KEY", "")
    monkeypatch.setattr("agent_core.llm_client.config.LLM_BASE_URL", "https://example.invalid/v1")

    client = get_async_llm_client()

    assert client is not None
    assert "Authorization" not in client._headers()


def test_documents_service_prefers_unified_llm_namespace(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "llm-model")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "openai-model")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dash.example/v1")
    monkeypatch.setenv("DASHSCOPE_MODEL", "dash-model")
    monkeypatch.setenv("DOCUMENTS_LLM_MODEL", "documents-model")

    assert DocumentsService._llm_api_key() == "llm-key"
    assert DocumentsService._llm_base_url() == "https://llm.example/v1"
    assert DocumentsService._llm_model() == "llm-model"
