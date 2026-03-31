from __future__ import annotations

import agent_core.decomposer as decomposer
import agent_core.direct_answerer as direct_answerer
import agent_core.sub_answerer as sub_answerer


def test_decompose_uses_stage_model(monkeypatch):
    captured = {}

    monkeypatch.setattr(decomposer, "load_prompt_template", lambda _: "{question}")

    def fake_chat_completion(**kwargs):
        captured.update(kwargs)
        return '["q1"]'

    monkeypatch.setattr(decomposer, "chat_completion", fake_chat_completion)
    monkeypatch.setattr(decomposer.config, "DECOMPOSE_MODEL", "decompose-test-model")

    result = decomposer.decompose_question("demo")

    assert result[0] == "q1"
    assert captured["model"] == "decompose-test-model"


def test_direct_answer_uses_stage_model(monkeypatch):
    captured = {}

    monkeypatch.setattr(direct_answerer, "load_prompt_template", lambda _: "{question}")

    def fake_chat_completion_stream(**kwargs):
        captured.update(kwargs)
        yield "ans"
        yield "wer"

    monkeypatch.setattr(direct_answerer, "chat_completion_stream", fake_chat_completion_stream)
    monkeypatch.setattr(direct_answerer.config, "DIRECT_ANSWER_MODEL", "direct-test-model")

    result = direct_answerer.direct_answer("demo")

    assert result == "answer"
    assert captured["model"] == "direct-test-model"


def test_direct_answer_uses_stage_runtime_bounds(monkeypatch):
    captured = {}

    monkeypatch.setattr(direct_answerer, "load_prompt_template", lambda _: "{question}")

    def fake_chat_completion_stream(**kwargs):
        captured.update(kwargs)
        yield "answer"

    monkeypatch.setattr(direct_answerer, "chat_completion_stream", fake_chat_completion_stream)
    monkeypatch.setattr(direct_answerer.config, "DIRECT_ANSWER_MAX_TOKENS", 1536, raising=False)
    monkeypatch.setattr(direct_answerer.config, "DIRECT_ANSWER_REQUEST_TIMEOUT_SECONDS", 45, raising=False)

    result = direct_answerer.direct_answer("demo")

    assert result == "answer"
    assert captured["max_tokens"] == 1536
    assert captured["timeout_seconds"] == 45


def test_sub_answer_kwargs_use_stage_model(monkeypatch):
    monkeypatch.setattr(sub_answerer.config, "SUB_ANSWER_MODEL", "sub-answer-test-model")

    kwargs = sub_answerer._build_sub_answer_kwargs("demo")

    assert kwargs["model"] == "sub-answer-test-model"
