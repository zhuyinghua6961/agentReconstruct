from __future__ import annotations

from types import SimpleNamespace

from server.patent.runtime import PatentRuntime
from server.patent.stages.planning import run_stage1_pre_answer_and_planning


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


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


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
