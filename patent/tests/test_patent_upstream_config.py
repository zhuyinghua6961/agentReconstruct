from __future__ import annotations

import httpx

from server.patent.intent_detect import run_intent_detect_quick_tag
from server.patent.models import PatentRetrievalClaim
from server.patent.runtime import PatentPlanningClient
from server.patent.stages.retrieval import build_stage2_queries_for_claim
from server.patent.thinking import (
    LLM_STAGE_CONTROL,
    LLM_STAGE_STAGE4_FINAL_ANSWER,
    auth_headers,
    resolve_thinking_controls,
)


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
