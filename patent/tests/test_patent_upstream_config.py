from __future__ import annotations

import httpx
import logging
from types import SimpleNamespace

from server.patent.intent_detect import run_intent_detect_quick_tag
from server.patent.models import PatentRetrievalClaim
from server.patent.query_expander import QueryExpander, _chat_completions_url
from server.patent.runtime import PatentPlanningClient
from server.patent.stages.retrieval import build_stage2_queries_for_claim
from server.patent.thinking import (
    LLM_STAGE_CONTROL,
    LLM_STAGE_STAGE4_FINAL_ANSWER,
    auth_headers,
    local_sdk_api_key,
    resolve_auth_mode,
    resolve_thinking_controls,
)
from server.patent.upstream_auth_logging import reset_upstream_auth_log_state_for_tests


class _NullLogger:
    def info(self, *args, **kwargs) -> None:
        return None

    def warning(self, *args, **kwargs) -> None:
        return None


class _FakePlanningHttpClient:
    def __init__(self, *contents: str) -> None:
        self._contents = list(contents) or ["ok"]
        self.calls: list[dict[str, object]] = []

    def post(self, url, *, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": dict(headers or {}), "json": dict(json or {}), "timeout": timeout})
        request = httpx.Request("POST", str(url), headers=headers, json=json)
        content = self._contents.pop(0) if self._contents else "ok"
        return httpx.Response(200, request=request, json={"choices": [{"message": {"content": content}}]})


class _FakeQueryExpansionHttpClient:
    def __init__(self, content: str = "expanded query") -> None:
        self.content = content
        self.calls: list[dict[str, object]] = []

    def post(self, url, *, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": dict(headers or {}), "json": dict(json or {}), "timeout": timeout})
        return SimpleNamespace(
            status_code=200,
            raise_for_status=lambda: None,
            json=lambda: {"choices": [{"message": {"content": self.content}}]},
        )


def _planning_client(http_client: _FakePlanningHttpClient) -> PatentPlanningClient:
    return PatentPlanningClient(
        api_key="",
        base_url="http://example.invalid/v1",
        timeout_seconds=10.0,
        http_client=http_client,
    )


def test_patent_thinking_helper_matches_stage_policy():
    disabled = resolve_thinking_controls(
        is_thinking_model=True,
        thinking_enabled=True,
        stage=LLM_STAGE_CONTROL,
        max_tokens=1000,
        stream=False,
    )
    enabled = resolve_thinking_controls(
        is_thinking_model=True,
        thinking_enabled=True,
        stage=LLM_STAGE_STAGE4_FINAL_ANSWER,
        max_tokens=4000,
        stream=True,
    )

    assert disabled.raw_payload_fields == {"thinking": {"type": "disabled"}}
    assert enabled.raw_payload_fields["thinking"] == {"type": "enabled"}
    assert enabled.raw_payload_fields["reasoning_effort"] == "high"
    assert enabled.max_tokens == 8192
    assert "Authorization" not in auth_headers("")
    assert auth_headers("Bearer token")["Authorization"] == "Bearer token"
    assert auth_headers("bearer token")["Authorization"] == "Bearer token"
    assert local_sdk_api_key("Bearer token") == "token"


def test_patent_auth_headers_supports_configurable_auth_modes(monkeypatch):
    monkeypatch.setenv("LLM_AUTH_MODE", "authorization")
    assert resolve_auth_mode() == "authorization"
    assert auth_headers("Bearer token")["Authorization"] == "token"

    assert auth_headers("Bearer token", auth_mode="bearer")["Authorization"] == "Bearer token"
    assert auth_headers("Bearer token", auth_mode="x-api-key")["X-API-Key"] == "token"
    assert "Authorization" not in auth_headers("Bearer token", auth_mode="none")
    assert "X-API-Key" not in auth_headers("Bearer token", auth_mode="none")


def test_patent_planning_client_accepts_sdk_style_disabled_thinking_kwargs(monkeypatch):
    monkeypatch.setenv("LLM_IS_THINKING_MODEL", "true")
    monkeypatch.setenv("LLM_THINKING_ENABLED", "true")

    http_client = _FakePlanningHttpClient("ok")
    client = _planning_client(http_client)

    response = client.chat.completions.create(
        model="planner-model",
        messages=[{"role": "user", "content": "demo"}],
        temperature=0.0,
        max_tokens=64,
        stream=False,
        extra_body={"thinking": {"type": "disabled"}},
    )

    assert response.choices[0].message.content == "ok"
    payload = http_client.calls[0]["json"]
    headers = http_client.calls[0]["headers"]
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["stream"] is False
    assert "reasoning_effort" not in payload
    assert "Authorization" not in headers


def test_patent_planning_client_logs_llm_auth_success_once(caplog):
    reset_upstream_auth_log_state_for_tests()
    caplog.set_level(logging.INFO)
    http_client = _FakePlanningHttpClient("ok")
    client = PatentPlanningClient(
        api_key="Bearer sk-demo-secret",
        base_url="http://example.invalid/v1",
        timeout_seconds=10.0,
        http_client=http_client,
    )

    client.chat.completions.create(
        model="planner-model",
        messages=[{"role": "user", "content": "demo"}],
        temperature=0.0,
        max_tokens=64,
        stream=False,
    )
    client.chat.completions.create(
        model="planner-model",
        messages=[{"role": "user", "content": "demo"}],
        temperature=0.0,
        max_tokens=64,
        stream=False,
    )

    messages = [record.message for record in caplog.records]
    auth_ok = [message for message in messages if "LLM upstream auth ok" in message]
    assert len(auth_ok) == 1
    assert "service=patent" in auth_ok[0]
    assert "model=planner-model" in auth_ok[0]
    assert "key_input_has_bearer=True" in auth_ok[0]
    assert "sk-demo-secret" not in auth_ok[0]


def test_patent_stage2_query_generation_uses_runtime_client_with_disabled_thinking(monkeypatch):
    monkeypatch.setenv("LLM_IS_THINKING_MODEL", "true")
    monkeypatch.setenv("LLM_THINKING_ENABLED", "true")
    http_client = _FakePlanningHttpClient("battery coating")
    client = _planning_client(http_client)

    queries = build_stage2_queries_for_claim(
        user_question="包覆材料的效果？",
        retrieval_claim=PatentRetrievalClaim(
            claim="碳包覆提升电池倍率性能",
            keywords=["碳包覆", "倍率"],
            preferred_sections=[],
            filters={},
        ),
        client=client,
        model="planner-model",
        logger=_NullLogger(),
        stage2_prompt="{claim_text}",
        stage2_system_prompt="system",
    )

    assert queries == ["battery coating"]
    payload = http_client.calls[0]["json"]
    assert payload["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in payload
    assert "enable_thinking" not in payload


def test_patent_intent_detect_uses_runtime_client_with_disabled_thinking(monkeypatch):
    monkeypatch.setenv("LLM_IS_THINKING_MODEL", "true")
    monkeypatch.setenv("LLM_THINKING_ENABLED", "true")
    monkeypatch.delenv("INTENT_MODEL_API_KEY", raising=False)
    http_client = _FakePlanningHttpClient("mechanism_analysis")
    client = _planning_client(http_client)

    result = run_intent_detect_quick_tag(
        client=client,
        user_question="碳包覆机理是什么？",
        logger=_NullLogger(),
    )

    assert result["ok"] is True
    payload = http_client.calls[0]["json"]
    assert payload["enable_thinking"] is False
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["stream"] is False
    assert "reasoning_effort" not in payload


def test_patent_query_expander_uses_configurable_http_auth(monkeypatch):
    monkeypatch.setenv("LLM_AUTH_MODE", "authorization")
    monkeypatch.setenv("LLM_IS_THINKING_MODEL", "true")
    monkeypatch.setenv("LLM_THINKING_ENABLED", "true")
    http_client = _FakeQueryExpansionHttpClient("expanded query")
    expander = QueryExpander(
        api_key="Bearer query-token",
        base_url="http://example.invalid/v1",
        model="query-model",
        http_client=http_client,
    )

    result = expander.expand("battery coating")

    assert result == "expanded query"
    call = http_client.calls[0]
    assert call["url"] == "http://example.invalid/v1/chat/completions"
    assert call["headers"]["Authorization"] == "query-token"
    payload = call["json"]
    assert payload["model"] == "query-model"
    assert payload["stream"] is False
    assert payload["thinking"] == {"type": "disabled"}


def test_patent_query_expander_logs_model_call_success(caplog):
    http_client = _FakeQueryExpansionHttpClient("expanded query")
    expander = QueryExpander(
        api_key="query-token",
        base_url="http://example.invalid/v1",
        model="query-model",
        http_client=http_client,
    )

    with caplog.at_level(logging.INFO, logger="patent.query_expander"):
        result = expander.expand("battery coating")

    assert result == "expanded query"
    messages = [record.message for record in caplog.records if record.name == "patent.query_expander"]
    assert any("model_call start" in message and "component=llm_query_expansion" in message and "model=query-model" in message for message in messages)
    assert any("model_call success" in message and "component=llm_query_expansion" in message and "answer_chars=14" in message for message in messages)


def test_patent_query_expander_normalizes_empty_and_full_chat_endpoint():
    assert _chat_completions_url("") == "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert _chat_completions_url("http://example.invalid/v1/chat/completions") == "http://example.invalid/v1/chat/completions"
