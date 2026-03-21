from __future__ import annotations

from app.integrations.llm import (
    build_chat_adapter,
    build_chat_completions_client,
    extract_openai_compatible_text,
    normalize_messages,
    normalize_openai_compatible_endpoint,
)
from app.integrations.llm.openai_compat import OpenAICompatChatAdapter, OpenAICompatClient


class _FakeResponse:
    def __init__(self, *, payload=None, lines=None, status_code: int = 200) -> None:
        self._payload = payload or {}
        self._lines = list(lines or [])
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload

    def iter_lines(self):
        for item in self._lines:
            yield item

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return None


class _FakeClient:
    def __init__(self, *, post_response: _FakeResponse, stream_response: _FakeResponse) -> None:
        self.post_response = post_response
        self.stream_response = stream_response
        self.calls: list[tuple[str, str, dict]] = []
        self.closed = False

    def post(self, url: str, **kwargs):
        self.calls.append(("post", url, kwargs))
        return self.post_response

    def stream(self, method: str, url: str, **kwargs):
        self.calls.append((method.lower(), url, kwargs))
        return self.stream_response

    def close(self):
        self.closed = True


class _FakeHttpx:
    class Timeout:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class Limits:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    def __init__(self, *, client: _FakeClient) -> None:
        self._client = client

    def Client(self, **_kwargs):
        return self._client


def test_normalize_messages_and_endpoint():
    assert normalize_messages("hello") == [{"role": "user", "content": "hello"}]
    assert normalize_messages([{"role": "human", "content": ["a", {"text": "b"}]}]) == [{"role": "user", "content": "ab"}]
    assert normalize_openai_compatible_endpoint("https://example.com/v1") == "https://example.com/v1/chat/completions"
    assert normalize_openai_compatible_endpoint("https://example.com/chat/completions") == "https://example.com/v1/chat/completions"


def test_extract_openai_compatible_text_supports_message_and_delta():
    assert extract_openai_compatible_text({"choices": [{"message": {"content": "answer"}}]}) == "answer"
    assert extract_openai_compatible_text({"choices": [{"delta": {"content": "chunk"}}]}) == "chunk"


def test_openai_compat_chat_adapter_invoke_and_stream():
    post_response = _FakeResponse(payload={"choices": [{"message": {"content": "final answer"}}]})
    stream_response = _FakeResponse(
        lines=[
            'data: {"choices":[{"delta":{"content":"hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            "data: [DONE]",
        ]
    )
    fake_client = _FakeClient(post_response=post_response, stream_response=stream_response)
    fake_httpx = _FakeHttpx(client=fake_client)
    adapter = OpenAICompatChatAdapter(
        httpx_module=fake_httpx,
        endpoint="https://example.com/v1/chat/completions",
        api_key="token",
        model="test-model",
    )

    invoked = adapter.invoke([{"role": "user", "content": "hi"}])
    streamed = [item.content for item in adapter.stream([{"role": "user", "content": "hi"}])]

    assert invoked.content == "final answer"
    assert streamed == ["hel", "lo"]
    assert fake_client.calls[0][0] == "post"
    assert fake_client.calls[1][0] == "post"
    adapter.close()
    assert fake_client.closed is True


def test_openai_compat_client_matches_openai_shape():
    post_response = _FakeResponse(payload={"choices": [{"message": {"content": "answer"}}]})
    stream_response = _FakeResponse(lines=['data: {"choices":[{"delta":{"content":"a"}}]}', "data: [DONE]"])
    fake_client = _FakeClient(post_response=post_response, stream_response=stream_response)
    fake_httpx = _FakeHttpx(client=fake_client)
    client = OpenAICompatClient(
        httpx_module=fake_httpx,
        endpoint="https://example.com/v1/chat/completions",
        api_key="token",
    )

    response = client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}], stream=False)
    stream = list(client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}], stream=True))

    assert response.choices[0].message.content == "answer"
    assert stream[0].choices[0].delta.content == "a"


def test_builders_return_openai_compat_types():
    adapter = build_chat_adapter(api_key="token", base_url="https://example.com/v1", model="m")
    client = build_chat_completions_client(api_key="token", base_url="https://example.com/v1")
    assert isinstance(adapter, OpenAICompatChatAdapter)
    assert isinstance(client, OpenAICompatClient)
    adapter.close()
    client.close()


def test_openai_compat_stream_ignores_bad_json_and_finish_only_frames():
    post_response = _FakeResponse(payload={"choices": [{"message": {"content": "unused"}}]})
    stream_response = _FakeResponse(
        lines=[
            "event: message",
            "data: {bad json",
            'data: {"choices":[{"delta":{"role":"assistant"}}]}',
            'data: {"choices":[{"delta":{"content":""},"finish_reason":"stop"}]}',
            'data: {"choices":[{"delta":{"content":["a",{"text":"b"}]}}]}',
            "data: [DONE]",
        ]
    )
    fake_client = _FakeClient(post_response=post_response, stream_response=stream_response)
    fake_httpx = _FakeHttpx(client=fake_client)
    client = OpenAICompatClient(
        httpx_module=fake_httpx,
        endpoint="https://example.com/v1/chat/completions",
        api_key="token",
    )

    stream = list(client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}], stream=True))

    assert [chunk.choices[0].delta.content for chunk in stream] == ["ab"]


def test_openai_compat_stream_raises_on_error_frame():
    post_response = _FakeResponse(payload={"choices": [{"message": {"content": "unused"}}]})
    stream_response = _FakeResponse(lines=['data: {"error":{"message":"upstream failed"}}'])
    fake_client = _FakeClient(post_response=post_response, stream_response=stream_response)
    fake_httpx = _FakeHttpx(client=fake_client)
    adapter = OpenAICompatChatAdapter(
        httpx_module=fake_httpx,
        endpoint="https://example.com/v1/chat/completions",
        api_key="token",
        model="test-model",
    )

    try:
        list(adapter.stream([{"role": "user", "content": "hi"}]))
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "upstream failed" in str(exc)
