from __future__ import annotations

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


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


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
    assert result["retrieval_claims"][0]["claim"] == "c1"
    assert result["retrieval_claims"][1]["claim"] == "plain"
    assert client.calls[0]["response_format"] == {"type": "json_object"}


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
