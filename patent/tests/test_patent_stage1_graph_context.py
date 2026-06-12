from __future__ import annotations

from types import SimpleNamespace

from server.patent.stages.planning import run_stage1_pre_answer_and_planning


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


class _ResponseFormatRejectingClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if "response_format" in kwargs:
            raise RuntimeError("response_format not supported")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


def _graph_context() -> dict[str, object]:
    return {
        "graph_kb": {
            "mode": "graph_for_rag",
            "cache_fingerprint": "patent-graph:test",
            "stage1_context_block": "graph stage1 block",
            "stage2_patent_candidates": ["CN100355122C", "CN100371239C"],
            "stage2_constraints": [{"field": "person.inventor", "operator": "eq", "value": "张三"}],
            "stage2_entity_hints": {
                "ipc_codes": ["H01M10/0525"],
                "organizations": ["宁德时代新能源科技股份有限公司"],
                "inventors": ["张三"],
            },
            "stage4_fact_block": "- graph fact",
            "stage4_graph_candidate_patent_ids": ["CN100355122C", "CN100371239C"],
            "diagnostics": {"strategy": "parametric"},
        }
    }


def test_stage1_graph_context_is_rendered_into_prompt():
    client = _ResponseFormatRejectingClient('{"deep_answer":"answer","retrieval_plan":{}}')

    run_stage1_pre_answer_and_planning(
        user_question="比较 CN100355122C 和 CN100371239C 的工艺步骤差异",
        client=client,
        model="gpt-test",
        logger=_Logger(),
        conversation_context=_graph_context(),
    )

    user_message = client.calls[-1]["messages"][1]["content"]
    assert "图谱模式：graph_for_rag" in user_message
    assert "图谱候选专利：CN100355122C；CN100371239C" in user_message
    assert "图谱实体提示：ipc_codes=H01M10/0525；organizations=宁德时代新能源科技股份有限公司；inventors=张三" in user_message
    assert "图谱约束：person.inventor eq 张三" in user_message
    assert "图谱事实：- graph fact" in user_message


def test_stage1_planner_unavailable_fallback_seeds_from_graph_payload():
    result = run_stage1_pre_answer_and_planning(
        user_question="比较 CN100355122C 和 CN100371239C 的工艺步骤差异",
        client=None,
        model="",
        logger=_Logger(),
        conversation_context=_graph_context(),
    )

    assert result["fallback"] == "planner_unavailable"
    assert result["retrieval_claims"]
    assert "CN100355122C" in result["retrieval_plan"].explicit_patent_ids
    assert result["retrieval_plan"].candidate_recall_queries


def test_stage1_graph_seed_does_not_create_explicit_patent_id_when_question_has_no_id():
    result = run_stage1_pre_answer_and_planning(
        user_question="如何制备高压实磷酸铁锂",
        client=None,
        model="",
        logger=_Logger(),
        conversation_context=_graph_context(),
    )

    assert result["fallback"] == "planner_unavailable"
    assert result["retrieval_claims"]
    assert result["retrieval_plan"].explicit_patent_ids == []
    assert "CN100355122C" in result["retrieval_plan"].candidate_recall_queries[0]


def test_stage1_json_parse_failed_fallback_seeds_from_graph_payload():
    client = _ResponseFormatRejectingClient("not-json")

    result = run_stage1_pre_answer_and_planning(
        user_question="比较 CN100355122C 和 CN100371239C 的工艺步骤差异",
        client=client,
        model="gpt-test",
        logger=_Logger(),
        conversation_context=_graph_context(),
    )

    assert result["fallback"] == "json_parse_failed"
    assert result["retrieval_claims"]
    assert "CN100355122C" in result["retrieval_plan"].explicit_patent_ids


def test_stage1_planner_error_fallback_seeds_from_graph_payload():
    class _BrokenClient:
        chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))))

    result = run_stage1_pre_answer_and_planning(
        user_question="比较 CN100355122C 和 CN100371239C 的工艺步骤差异",
        client=_BrokenClient(),
        model="gpt-test",
        logger=_Logger(),
        conversation_context=_graph_context(),
    )

    assert result["fallback"] == "planner_error"
    assert result["retrieval_claims"]
    assert "CN100355122C" in result["retrieval_plan"].explicit_patent_ids
