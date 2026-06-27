from __future__ import annotations

import httpx

from types import SimpleNamespace

from server.patent.runtime import PatentPlanningClient, PatentRuntime
from server.patent.stages.planning import run_stage1_pre_answer_and_planning


class _FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


class _SequentialClient:
    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        index = len(self.calls) - 1
        if index >= len(self.replies):
            raise AssertionError("unexpected extra completion call")
        body = self.replies[index]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=body))])


class _ResponseFormatRejectingClient(_FakeClient):
    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if "response_format" in kwargs:
            raise RuntimeError("response_format not supported")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _CaptureLogger(_Logger):
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def info(self, msg, *args, **kwargs):
        self.records.append(("info", msg % args if args else msg))

    def warning(self, msg, *args, **kwargs):
        self.records.append(("warning", msg % args if args else msg))

    def error(self, msg, *args, **kwargs):
        self.records.append(("error", msg % args if args else msg))


def test_stage1_planning_parses_json_and_normalizes_patent_retrieval_plan():
    client = _FakeClient(
        """{
  "deep_answer": "初步判断这项技术仍处于导入窗口。",
  "retrieval_claims": [
    {
      "claim": "钠离子储能在目标窗口内可替代 LFP。",
      "keywords": ["钠离子电池", "储能", "替代", "LFP", "时间窗口"],
      "preferred_sections": ["claims", "description", "tables"],
      "filters": {"countries": ["CN", "US"]}
    },
    {
      "claim": "需要定位循环寿命、安全性和成本证据。",
      "keywords": ["循环寿命", "安全性", "成本"],
      "preferred_sections": ["description", "tables"],
      "filters": {}
    }
  ]
}"""
    )

    result = run_stage1_pre_answer_and_planning(
        user_question="对比 CN115132975B 和 US20240001234A1，评估钠离子储能替代 LFP 的时间窗口。",
        client=client,
        model="gpt-test",
        logger=_Logger(),
    )

    retrieval_claims = result["retrieval_claims"]
    retrieval_plan = result["retrieval_plan"]
    assert result["success"] is True
    assert result["deep_answer"] == "初步判断这项技术仍处于导入窗口。"
    assert len(retrieval_claims) == 2
    assert retrieval_claims[0].claim == "钠离子储能在目标窗口内可替代 LFP。"
    assert retrieval_claims[0].keywords == ["钠离子电池", "储能", "替代", "LFP", "时间窗口"]
    assert retrieval_claims[0].preferred_sections == ["claims", "description", "tables"]
    assert retrieval_claims[0].filters == {"countries": ["CN", "US"]}
    assert retrieval_claims[1].claim == "需要定位循环寿命、安全性和成本证据。"
    assert retrieval_claims[1].keywords == ["循环寿命", "安全性", "成本"]
    assert retrieval_claims[1].preferred_sections == ["description", "tables"]
    assert retrieval_plan.question_type == "technology_substitution"
    assert "substitution_risk" in retrieval_plan.analysis_axes
    assert "time_window" in retrieval_plan.analysis_axes
    assert retrieval_plan.explicit_patent_ids == ["CN115132975B", "US20240001234A1"]
    assert retrieval_plan.candidate_recall_queries == [
        "钠离子储能在目标窗口内可替代 LFP。 钠离子电池 储能 替代 LFP 时间窗口",
        "需要定位循环寿命、安全性和成本证据。 循环寿命 安全性 成本",
    ]
    assert retrieval_plan.evidence_localization_queries == retrieval_plan.candidate_recall_queries
    assert retrieval_plan.preferred_sections == ["claims", "description", "tables"]
    assert retrieval_plan.filters == {"countries": ["CN", "US"]}
    assert client.calls[0]["response_format"] == {"type": "json_object"}


def test_stage1_planning_falls_back_to_safe_defaults_when_json_is_invalid():
    client = _FakeClient("not-json pre-answer")

    result = run_stage1_pre_answer_and_planning(
        user_question="请评估 CN115132975B 的风险与时间窗口。",
        client=client,
        model="gpt-test",
        logger=_Logger(),
    )

    retrieval_plan = result["retrieval_plan"]
    assert result["success"] is True
    assert result["deep_answer"] == "not-json pre-answer"
    assert result["fallback"] == "json_parse_failed"
    assert result["retrieval_claims"] == []
    assert retrieval_plan.candidate_recall_queries == []


def test_stage1_planning_logs_structured_quality_and_response_preview(monkeypatch):
    monkeypatch.setenv("QA_STAGE1_LOG_RESPONSE_MAX_CHARS", "120")
    monkeypatch.delenv("QA_STAGE1_LOG_FULL_RESPONSE", raising=False)
    client = _FakeClient(
        """{
  "deep_answer": "patent answer body",
  "retrieval_claims": [
    {
      "claim": "钠离子储能在目标窗口内可替代 LFP。",
      "keywords": ["钠离子电池", "LFP"]
    }
  ]
}"""
    )
    logger = _CaptureLogger()

    result = run_stage1_pre_answer_and_planning(
        user_question="对比钠离子储能替代 LFP 的时间窗口。",
        client=client,
        model="gpt-test",
        logger=logger,
    )

    assert result["success"] is True
    messages = [message for _level, message in logger.records]
    assert any(
        "patent stage1 structured quality" in message
        and "json_parsed=true" in message
        and "schema_valid=true" in message
        and "retrieval_claims_count=1" in message
        and "valid_claims_count=1" in message
        and "stage2_eligible=true" in message
        for message in messages
    )
    assert any("patent stage1 raw response preview" in message and "patent answer body" in message for message in messages)


def test_stage1_planning_logs_json_parse_failure_quality_and_response_preview(monkeypatch):
    monkeypatch.setenv("QA_STAGE1_LOG_RESPONSE_MAX_CHARS", "120")
    client = _FakeClient("not-json-stage1-answer")
    logger = _CaptureLogger()

    result = run_stage1_pre_answer_and_planning(
        user_question="请评估 CN115132975B 的风险与时间窗口。",
        client=client,
        model="gpt-test",
        logger=logger,
    )

    assert result["success"] is True
    assert result["fallback"] == "json_parse_failed"
    messages = [message for _level, message in logger.records]
    assert any(
        "patent stage1 structured quality" in message
        and "json_parsed=false" in message
        and "schema_valid=false" in message
        and "fallback=json_parse_failed" in message
        and "stage2_eligible=false" in message
        for message in messages
    )
    assert any("patent stage1 raw response preview" in message and "not-json-stage1-answer" in message for message in messages)


def test_stage1_planning_includes_normalized_context_and_retries_without_response_format():
    client = _ResponseFormatRejectingClient('{"deep_answer":"answer","retrieval_plan":{}}')

    run_stage1_pre_answer_and_planning(
        user_question="what should we check?",
        client=client,
        model="gpt-test",
        logger=_Logger(),
        conversation_context={
            "recent_turns_for_llm": [
                {"role": "user", "content": "  earlier   question "},
                {"role": "assistant", "content": " prior   answer "},
            ],
            "summary_for_llm": {
                "short_summary": " discussing sodium ion ",
                "open_threads": [" substitution risk ", ""],
                "memory_facts": [" table metrics ", ""],
                "trace_id": "should-not-leak",
            },
        },
    )

    assert len(client.calls) == 2
    assert client.calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in client.calls[1]
    user_message = client.calls[1]["messages"][1]["content"]
    assert "会话摘要：discussing sodium ion" in user_message
    assert "待继续话题：substitution risk" in user_message
    assert "已知事实：table metrics" in user_message
    assert "用户: earlier question" in user_message
    assert "助手: prior answer" in user_message
    assert "should-not-leak" not in user_message


def test_stage1_planning_uses_fallback_deep_answer_for_empty_json_payload():
    client = _FakeClient("{}")

    result = run_stage1_pre_answer_and_planning(
        user_question="评估钠离子替代 LFP 的时间窗口。",
        client=client,
        model="gpt-test",
        logger=_Logger(),
    )

    retrieval_plan = result["retrieval_plan"]
    assert result["success"] is True
    assert result["deep_answer"] != ""
    assert result["retrieval_claims"] == []
    assert retrieval_plan.candidate_recall_queries == []


def test_stage1_planning_normalizes_singleton_strings_and_punctuated_patent_ids():
    client = _FakeClient(
        """{
  "deep_answer": "answer",
  "retrieval_claims": [
    {
      "claim": "assess substitution risk",
      "keywords": "battery safety",
      "preferred_sections": "tables"
    }
  ]
}"""
    )

    result = run_stage1_pre_answer_and_planning(
        user_question="请评估 CN202110320984.1 和 US2024-0001234-A1 的可替代风险。",
        client=client,
        model="gpt-test",
        logger=_Logger(),
    )

    retrieval_claims = result["retrieval_claims"]
    retrieval_plan = result["retrieval_plan"]
    assert retrieval_claims[0].keywords == ["battery safety"]
    assert retrieval_claims[0].preferred_sections == ["tables"]
    assert "risk" in retrieval_plan.analysis_axes
    assert retrieval_plan.candidate_recall_queries == ["assess substitution risk battery safety"]
    assert retrieval_plan.evidence_localization_queries == ["assess substitution risk battery safety"]
    assert retrieval_plan.explicit_patent_ids == ["CN2021103209841", "US20240001234A1"]


def test_patent_runtime_stage1_uses_configured_planning_client():
    client = _FakeClient('{"deep_answer":"runtime answer","retrieval_claims":[{"claim":"runtime answer","keywords":["compare"],"preferred_sections":["claims"]}]}')
    runtime = PatentRuntime(
        retrieval_service=object(),
        resources=[],
        planning_client=client,
        planning_model="gpt-test",
        stage1_prompt="prompt",
    )

    result = runtime.stage1_pre_answer_and_planning(
        "Compare sodium ion and LFP",
        conversation_context={"recent_turns_for_llm": []},
    )

    assert result["success"] is True
    assert result["deep_answer"] == "runtime answer"
    assert result["retrieval_claims"][0].claim == "runtime answer"


def test_patent_runtime_stage1_uses_planning_hot_pool_proxy_client():
    fallback_client = _FakeClient('{"deep_answer":"fallback answer","retrieval_claims":[]}')
    hot_client = _FakeClient('{"deep_answer":"hot pool answer","retrieval_claims":[]}')

    class _PlanningHotPool:
        def __init__(self) -> None:
            self.proxy_calls: list[object] = []

        def proxy_client(self, *, fallback_client=None):
            self.proxy_calls.append(fallback_client)
            return hot_client

    hot_pool = _PlanningHotPool()
    runtime = PatentRuntime(
        retrieval_service=object(),
        resources=[],
        planning_client=fallback_client,
        planning_hot_pool=hot_pool,
        planning_model="gpt-test",
        stage1_prompt="prompt",
    )

    result = runtime.stage1_pre_answer_and_planning("Compare sodium ion and LFP")

    assert result["success"] is True
    assert result["deep_answer"] == "hot pool answer"
    assert hot_pool.proxy_calls == [fallback_client]
    assert len(hot_client.calls) == 1
    assert fallback_client.calls == []


def test_patent_runtime_stage1_without_hot_pool_uses_configured_planning_client():
    client = _FakeClient('{"deep_answer":"runtime answer","retrieval_claims":[]}')
    runtime = PatentRuntime(
        retrieval_service=object(),
        resources=[],
        planning_client=client,
        planning_model="gpt-test",
        stage1_prompt="prompt",
    )

    result = runtime.stage1_pre_answer_and_planning("Compare sodium ion and LFP")

    assert result["success"] is True
    assert result["deep_answer"] == "runtime answer"
    assert len(client.calls) == 1


def test_patent_runtime_stage1_enters_the_gate():
    client = _FakeClient('{"deep_answer":"runtime answer","retrieval_claims":[]}')

    class _Gate:
        def __init__(self) -> None:
            self.proxy_calls: list[dict[str, object]] = []

        def proxy_client(self, *, base_client=None, trace_label="", should_cancel=None):
            self.proxy_calls.append(
                {
                    "base_client": base_client,
                    "trace_label": trace_label,
                    "should_cancel": should_cancel,
                }
            )
            return base_client

    gate = _Gate()
    runtime = PatentRuntime(
        retrieval_service=object(),
        resources=[],
        planning_client=client,
        planning_upstream_gate=gate,
        planning_model="gpt-test",
        stage1_prompt="prompt",
    )

    result = runtime.stage1_pre_answer_and_planning("Compare sodium ion and LFP")

    assert result["success"] is True
    assert gate.proxy_calls == [
        {
            "base_client": client,
            "trace_label": "stage1_planning",
            "should_cancel": None,
        }
    ]


def test_patent_runtime_stage1_bypass_the_gate_when_disabled():
    client = _FakeClient('{"deep_answer":"runtime answer","retrieval_claims":[]}')
    runtime = PatentRuntime(
        retrieval_service=object(),
        resources=[],
        planning_client=client,
        planning_model="gpt-test",
        stage1_prompt="prompt",
    )

    result = runtime.stage1_pre_answer_and_planning("Compare sodium ion and LFP")

    assert result["success"] is True
    assert len(client.calls) == 1


def test_patent_planning_client_uses_injected_http_client_and_request_timeout(caplog):
    class _FakeSharedPool:
        def __init__(self) -> None:
            self.config = SimpleNamespace(
                connect_timeout_seconds=1.5,
                read_timeout_seconds=2.5,
                stream_read_timeout_seconds=9.5,
                write_timeout_seconds=3.5,
                pool_timeout_seconds=4.5,
            )
            self.wait_calls: list[float] = []
            self.timeout_calls: list[float] = []

        def snapshot(self) -> dict[str, object]:
            return {
                "pool_owner": "app",
                "client_owner": "shared",
                "shared_client_id": "planner-shared",
                "pid": 1,
                "bootstrap_source": "startup",
                "pool_timeout_count": len(self.timeout_calls),
                "pool_wait_ms": self.wait_calls[-1] if self.wait_calls else 0.0,
            }

        def record_pool_wait(self, *, wait_ms: float) -> None:
            self.wait_calls.append(wait_ms)

        def record_pool_timeout(self, *, wait_ms: float) -> None:
            self.timeout_calls.append(wait_ms)

    class _FakeHttpClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.closed = False
            self.requests: list[httpx.Request] = []
            self._patent_shared_pool = _FakeSharedPool()

        def build_request(self, method, url, *, headers=None, json=None, timeout=None, extensions=None):
            request = httpx.Request(str(method), str(url), headers=headers, json=json)
            request.extensions.update(dict(extensions or {}))
            if timeout is not None:
                request.extensions["timeout"] = timeout.as_dict()
            self.requests.append(request)
            return request

        def send(self, request, *, stream=False):
            self.calls.append(
                {
                    "url": str(request.url),
                    "headers": dict(request.headers),
                    "json": request.read().decode("utf-8"),
                    "stream": stream,
                    "timeout": dict(request.extensions.get("timeout") or {}),
                }
            )
            return httpx.Response(
                200,
                request=request,
                json={"choices": [{"message": {"content": "planner response"}}]},
            )

        def close(self):
            self.closed = True

    http_client = _FakeHttpClient()
    client = PatentPlanningClient(
        api_key="test-key",
        base_url="http://example.invalid",
        timeout_seconds=17.0,
        http_client=http_client,
    )

    with caplog.at_level("INFO", logger="patent.runtime"):
        response = client.chat.completions.create(
            model="planner-model",
            messages=[{"role": "user", "content": "what should we check?"}],
            temperature=0.2,
            max_tokens=128,
            response_format={"type": "json_object"},
        )

    assert response.choices[0].message.content == "planner response"
    assert len(http_client.calls) == 1
    timeout = http_client.calls[0]["timeout"]
    assert timeout == {"connect": 1.5, "read": 2.5, "write": 3.5, "pool": 4.5}
    assert http_client.calls[0]["stream"] is False
    assert http_client._patent_shared_pool.wait_calls
    assert http_client._patent_shared_pool.timeout_calls == []
    assert any(
        "Patent planning client request payload ready" in record.message and "message_count=1" in record.message
        for record in caplog.records
    )
    assert any(
        "Patent planning client request object built" in record.message and "method=POST" in record.message
        for record in caplog.records
    )
    assert any(
        "Patent planning client request dispatch start" in record.message and "timeout_seconds=17.0" in record.message
        for record in caplog.records
    )
    assert any(
        "Patent planning client request dispatch returned" in record.message and "status_code=200" in record.message
        for record in caplog.records
    )
    assert any(
        "Patent planning client response headers received" in record.message and "status_code=200" in record.message
        for record in caplog.records
    )
    assert any(
        "Patent planning client response body parsed" in record.message and "response_chars=16" in record.message
        for record in caplog.records
    )
    client.close()
    assert http_client.closed is False


def test_patent_planning_client_logs_model_call_success(caplog):
    class _FakeHttpClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def post(self, url, *, headers=None, json=None, timeout=None):
            self.calls.append({"url": str(url), "headers": headers, "json": json, "timeout": timeout})
            return httpx.Response(
                200,
                request=httpx.Request("POST", str(url)),
                json={"choices": [{"message": {"content": "planner response"}}]},
            )

    http_client = _FakeHttpClient()
    client = PatentPlanningClient(
        api_key="test-key",
        base_url="http://example.invalid",
        timeout_seconds=17.0,
        http_client=http_client,
    )

    with caplog.at_level("INFO", logger="patent.runtime"):
        response = client.chat.completions.create(
            model="planner-model",
            messages=[{"role": "user", "content": "what should we check?"}],
            temperature=0.2,
            max_tokens=128,
            response_format={"type": "json_object"},
        )

    assert response.choices[0].message.content == "planner response"
    messages = [record.message for record in caplog.records]
    assert any(
        "model_call start" in message
        and "service=patent" in message
        and "component=llm_planning" in message
        and "model=planner-model" in message
        and "message_count=1" in message
        and "key_present=True" in message
        for message in messages
    )
    assert any(
        "model_call success" in message
        and "service=patent" in message
        and "component=llm_planning" in message
        and "status_code=200" in message
        and "answer_chars=16" in message
        for message in messages
    )


def test_patent_stage1_planning_logs_prompt_and_llm_boundaries():
    client = _FakeClient('{"deep_answer":"answer","retrieval_claims":[]}')
    logger = _CaptureLogger()

    result = run_stage1_pre_answer_and_planning(
        user_question="请评估 CN115132975B。",
        client=client,
        model="gpt-test",
        logger=logger,
    )

    assert result["success"] is True
    messages = [message for _level, message in logger.records]
    assert any("patent stage1 planning prompt prepared" in message and "prompt_chars=" in message for message in messages)
    assert any("patent stage1 planning llm request start" in message and "model=gpt-test" in message for message in messages)
    assert any("patent stage1 planning llm response received" in message and "response_chars=" in message for message in messages)


def test_stage1_planning_disables_thinking_for_thinking_model(monkeypatch):
    monkeypatch.setenv("LLM_IS_THINKING_MODEL", "true")
    monkeypatch.setenv("LLM_THINKING_ENABLED", "true")
    client = _FakeClient('{"deep_answer":"answer","retrieval_claims":[]}')

    result = run_stage1_pre_answer_and_planning(
        user_question="请评估 CN115132975B。",
        client=client,
        model="gpt-test",
        logger=_Logger(),
    )

    assert result["success"] is True
    assert client.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in client.calls[0]


def test_stage1_prepends_intent_hint_when_patent_intent_detect_enabled(monkeypatch):
    monkeypatch.delenv("INTENT_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("INTENT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("INTENT_MODEL", raising=False)
    monkeypatch.setenv("PATENT_INTENT_DETECT_ENABLED", "true")
    monkeypatch.delenv("QA_INTENT_DETECT_ENABLED", raising=False)
    monkeypatch.delenv("PATENT_INTENT_DETECT_MODEL", raising=False)
    monkeypatch.delenv("QA_INTENT_DETECT_MODEL", raising=False)
    planner_json = (
        '{"deep_answer":"初步判断这项技术仍处于导入窗口。",'
        '"retrieval_claims":[{"claim":"主张一","keywords":["LiFePO4"],'
        '"preferred_sections":["methods"],"filters":{}}]}'
    )
    intent_json = (
        '{"intent_tag":"electrochemical_performance","anchor_terms":["LiFePO4","碳包覆","倍率"]}'
    )
    client = _SequentialClient([intent_json, planner_json])
    result = run_stage1_pre_answer_and_planning(
        user_question="LiFePO4 碳包覆对倍率有何影响？",
        client=client,
        model="gpt-test",
        logger=_Logger(),
    )
    assert result["success"] is True
    assert len(client.calls) == 2
    intent_call = client.calls[0]
    plan_call = client.calls[1]
    assert intent_call["model"] == "qwen3-8b"
    assert intent_call["max_tokens"] == 256
    assert plan_call["model"] == "gpt-test"
    plan_user = str(plan_call["messages"][1]["content"])
    assert "快速意图识别" in plan_user
    assert "检索锚词" in plan_user
    assert plan_user.startswith("【快速意图识别")
    assert "用户问题：" in plan_user
    intent_meta = dict(result.get("intent_detect") or {})
    assert intent_meta.get("ok") is True
    assert intent_meta.get("intent_tag") == "electrochemical_performance"
    claim_keywords = list(result["retrieval_claims"][0].keywords)
    assert "LiFePO4" in claim_keywords
    assert "碳包覆" in claim_keywords
    assert "倍率" in claim_keywords
