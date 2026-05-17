from __future__ import annotations

import httpx
from types import SimpleNamespace

from app.modules.generation_pipeline.stage1_planning import run_stage1_pre_answer_and_planning


class _FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


class _ResponseFormatRejectingClient(_FakeClient):
    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if "response_format" in kwargs:
            raise RuntimeError("response_format not supported")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


class _AlwaysFailingClient(_FakeClient):
    def _create(self, **kwargs):
        self.calls.append(kwargs)
        raise RuntimeError("invalid api key")


class _PoolTimeoutClient(_FakeClient):
    def _create(self, **kwargs):
        self.calls.append(kwargs)
        raise httpx.PoolTimeout("pool exhausted")


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


class _CaptureLogger(_Logger):
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def info(self, msg, *args, **kwargs):
        self.records.append(("info", msg % args if args else msg))

    def error(self, msg, *args, **kwargs):
        self.records.append(("error", msg % args if args else msg))

    def warning(self, msg, *args, **kwargs):
        self.records.append(("warning", msg % args if args else msg))


def test_stage1_planning_parses_json_and_normalizes_claims():
    client = _FakeClient(
        '{"deep_answer":"answer","retrieval_claims":[{"claim":"c1","keywords":["k1"],"preferred_sections":["methods"],"filters":{"must_contains":["LFP"]}},"plain"]}'
    )
    result = run_stage1_pre_answer_and_planning(
        user_question="what is lfp?",
        stage1_prompt="prompt",
        vector_db_context="context",
        client=client,
        model="gpt-test",
        logger=_Logger(),
    )

    assert result["success"] is True
    assert result["deep_answer"] == "answer"
    assert result.get("query_focus_terms") == []
    assert result["retrieval_claims"][0]["claim"] == "c1"
    assert result["retrieval_claims"][1]["claim"] == "plain"
    assert client.calls[0]["response_format"] == {"type": "json_object"}


def test_stage1_planning_normalizes_query_focus_terms():
    client = _FakeClient(
        '{"deep_answer":"ok","query_focus_terms":["高压实型","高压实型","辊压"],"retrieval_claims":[]}'
    )
    result = run_stage1_pre_answer_and_planning(
        user_question="如何制备高压实型LFP",
        stage1_prompt="prompt",
        vector_db_context="",
        client=client,
        model="gpt-test",
        logger=_Logger(),
    )
    assert result["success"] is True
    assert result["query_focus_terms"] == ["高压实型", "辊压"]


def test_stage1_planning_preserves_structured_answer_plan():
    client = _FakeClient(
        """{
          "deep_answer": "answer",
          "answer_plan": {
            "answer_type": "multi_object_comparison",
            "objects": [{"label": "磷酸铁"}, {"label": "草酸亚铁"}],
            "dimensions": [{"name": "成本", "evidence_needed": "原料成本和规模化生产数据"}],
            "summary_plan": {"decision_axes": ["高性能选型", "低成本选型"]}
          },
          "retrieval_claims": []
        }"""
    )

    result = run_stage1_pre_answer_and_planning(
        user_question="磷酸铁、草酸亚铁各有什么优劣势？",
        stage1_prompt="prompt",
        vector_db_context="context",
        client=client,
        model="gpt-test",
        logger=_Logger(),
    )

    assert result["success"] is True
    assert result["answer_plan"]["answer_type"] == "multi_object_comparison"
    assert [item["label"] for item in result["answer_plan"]["objects"]] == ["磷酸铁", "草酸亚铁"]
    assert result["answer_plan"]["dimensions"][0]["evidence_needed"] == "原料成本和规模化生产数据"
    assert result.get("query_focus_terms") == []


def test_stage1_planning_returns_cancelled_without_dispatching_llm_when_cancelled_first():
    client = _FakeClient('{"deep_answer":"answer","retrieval_claims":[]}')

    result = run_stage1_pre_answer_and_planning(
        user_question="what is lfp?",
        stage1_prompt="prompt",
        vector_db_context="context",
        client=client,
        model="gpt-test",
        logger=_Logger(),
        should_cancel=lambda: True,
    )

    assert result["success"] is False
    assert result["metadata"]["cancelled"] is True
    assert client.calls == []


def test_stage1_planning_marks_cancelled_after_llm_response():
    client = _FakeClient('{"deep_answer":"answer","retrieval_claims":[{"claim":"c1"}]}')
    calls = {"value": 0}

    def _should_cancel() -> bool:
        calls["value"] += 1
        return calls["value"] >= 2

    result = run_stage1_pre_answer_and_planning(
        user_question="what is lfp?",
        stage1_prompt="prompt",
        vector_db_context="context",
        client=client,
        model="gpt-test",
        logger=_Logger(),
        should_cancel=_should_cancel,
    )

    assert result["success"] is False
    assert result["metadata"]["cancelled"] is True


def test_stage1_planning_falls_back_when_json_invalid():
    client = _FakeClient("not-json")
    result = run_stage1_pre_answer_and_planning(
        user_question="what is lfp?",
        stage1_prompt="prompt",
        vector_db_context="",
        client=client,
        model="gpt-test",
        logger=_Logger(),
    )

    assert result["success"] is True
    assert result["retrieval_claims"] == []
    assert result["fallback"] == "json_parse_failed"


def test_stage1_planning_parses_fenced_json_when_deep_answer_contains_inner_code_fence():
    client = _FakeClient(
        """```json
{
  "deep_answer": "先给方案。\\n\\n```python\\nprint('demo')\\n```\\n\\n补充说明。",
  "retrieval_claims": [
    {
      "claim": "c1",
      "keywords": ["k1"],
      "preferred_sections": ["methods"],
      "filters": {"must_contains": ["LFP"]}
    }
  ]
}
```"""
    )
    result = run_stage1_pre_answer_and_planning(
        user_question="what is lfp?",
        stage1_prompt="prompt",
        vector_db_context="",
        client=client,
        model="gpt-test",
        logger=_Logger(),
    )

    assert result["success"] is True
    assert result["fallback"] if "fallback" in result else None is None
    assert "```python" in result["deep_answer"]
    assert result["retrieval_claims"] == [
        {
            "claim": "c1",
            "keywords": ["k1"],
            "preferred_sections": ["methods"],
            "filters": {"must_contains": ["LFP"]},
        }
    ]


def test_stage1_planning_does_not_accept_legacy_alias_fields_for_normal_qa():
    client = _FakeClient(
        '{"answer":"legacy-answer","claims":[{"claim":"c1","keywords":["k1"]},"plain-claim"]}'
    )
    result = run_stage1_pre_answer_and_planning(
        user_question="what is lfp?",
        stage1_prompt="prompt",
        vector_db_context="",
        client=client,
        model="gpt-test",
        logger=_Logger(),
    )

    assert result["success"] is True
    assert result["deep_answer"] == ""
    assert result["retrieval_claims"] == []


def test_stage1_planning_does_not_synthesize_claims_from_user_question():
    client = _FakeClient('{"unknown_field":"fallback-answer","retrieval_claims":[]}')
    result = run_stage1_pre_answer_and_planning(
        user_question="what is lfp?",
        stage1_prompt="prompt",
        vector_db_context="",
        client=client,
        model="gpt-test",
        logger=_Logger(),
    )

    assert result["success"] is True
    assert result["deep_answer"] == ""
    assert result["retrieval_claims"] == []


def test_stage1_planning_retries_without_response_format_when_backend_rejects_it():
    client = _ResponseFormatRejectingClient('{"deep_answer":"answer","retrieval_claims":[]}')
    result = run_stage1_pre_answer_and_planning(
        user_question="what is lfp?",
        stage1_prompt="prompt",
        vector_db_context="context",
        client=client,
        model="gpt-test",
        logger=_Logger(),
    )

    assert result["success"] is True
    assert result["deep_answer"] == "answer"
    assert len(client.calls) == 2
    assert client.calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in client.calls[1]


def test_stage1_planning_includes_normalized_conversation_context_in_user_message():
    client = _FakeClient('{"deep_answer":"answer","retrieval_claims":[]}')
    run_stage1_pre_answer_and_planning(
        user_question="what is lfp?",
        stage1_prompt="prompt",
        vector_db_context="context",
        client=client,
        model="gpt-test",
        logger=_Logger(),
        conversation_context={
            "recent_turns_for_llm": [
                {"role": "user", "content": "  explain   LFP  "},
                {"role": "assistant", "content": "  safe   chemistry "},
                {"role": "assistant", "content": "   "},
            ],
            "summary_for_llm": {
                "short_summary": " discussing   LFP ",
                "open_threads": [" cycle life ", "   "],
                "memory_facts": [" cathode ", ""],
                "trace_id": "should-not-leak",
            },
        },
    )

    user_message = client.calls[0]["messages"][1]["content"]
    assert "会话摘要：discussing LFP" in user_message
    assert "待继续话题：cycle life" in user_message
    assert "已知事实：cathode" in user_message
    assert "用户: explain LFP" in user_message
    assert "助手: safe chemistry" in user_message
    assert "should-not-leak" not in user_message


def test_stage1_planning_includes_graph_context_in_user_message():
    client = _FakeClient('{"deep_answer":"answer","retrieval_claims":[]}')
    run_stage1_pre_answer_and_planning(
        user_question="what is lfp?",
        stage1_prompt="prompt",
        vector_db_context="context",
        client=client,
        model="gpt-test",
        logger=_Logger(),
        graph_context="doi:10.1000/test",
    )

    user_message = client.calls[0]["messages"][1]["content"]
    assert "doi:10.1000/test" in user_message


def test_stage1_planning_graph_context_supplements_original_question():
    client = _FakeClient('{"deep_answer":"answer","retrieval_claims":[]}')
    run_stage1_pre_answer_and_planning(
        user_question="放电容量超过150 mAh/g的LFP有哪些特点？",
        stage1_prompt="prompt",
        vector_db_context="context",
        client=client,
        model="gpt-test",
        logger=_Logger(),
        graph_context="graph_route_family: hybrid\ngraph_execution_mode: graph_for_rag",
    )

    user_message = client.calls[0]["messages"][1]["content"]
    assert "图谱结构化线索" in user_message
    assert "graph_route_family: hybrid" in user_message
    assert "用户问题：放电容量超过150 mAh/g的LFP有哪些特点？" in user_message


def test_stage1_planning_does_not_retry_without_response_format_for_unrelated_errors():
    client = _AlwaysFailingClient('ignored')
    result = run_stage1_pre_answer_and_planning(
        user_question="what is lfp?",
        stage1_prompt="prompt",
        vector_db_context="context",
        client=client,
        model="gpt-test",
        logger=_Logger(),
    )

    assert result["success"] is False
    assert len(client.calls) == 1
    assert client.calls[0]["response_format"] == {"type": "json_object"}


def test_stage1_planning_propagates_pool_timeout_without_swallowing():
    client = _PoolTimeoutClient("ignored")

    try:
        run_stage1_pre_answer_and_planning(
            user_question="what is lfp?",
            stage1_prompt="prompt",
            vector_db_context="context",
            client=client,
            model="gpt-test",
            logger=_Logger(),
        )
    except httpx.PoolTimeout:
        pass
    else:  # pragma: no cover - enforced by failing test before fix
        raise AssertionError("expected PoolTimeout to propagate")

    assert len(client.calls) == 1
    assert client.calls[0]["response_format"] == {"type": "json_object"}


def test_stage1_planning_logs_prompt_and_llm_boundaries():
    client = _FakeClient('{"deep_answer":"answer","retrieval_claims":[]}')
    logger = _CaptureLogger()

    result = run_stage1_pre_answer_and_planning(
        user_question="what is lfp?",
        stage1_prompt="prompt",
        vector_db_context="context",
        client=client,
        model="gpt-test",
        logger=logger,
    )

    assert result["success"] is True
    messages = [message for _level, message in logger.records]
    assert any("阶段一提示词拼装完成" in message and "prompt_chars=" in message for message in messages)
    assert any("阶段一 LLM 请求发起" in message and "model=gpt-test" in message for message in messages)
    assert any("阶段一 LLM 响应已接收" in message and "response_chars=" in message for message in messages)
