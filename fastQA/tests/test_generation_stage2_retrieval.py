from __future__ import annotations

import logging

from app.modules.generation_pipeline.stage2_retrieval import run_stage2_targeted_retrieval


class _Expert:
    def __init__(self, responses: dict[str, dict] | None = None, default_response: dict | None = None) -> None:
        self._responses = responses or {}
        self._default_response = default_response or {"documents": [], "metadatas": [], "distances": []}
        self.calls: list[dict[str, object]] = []

    def search(self, query: str, **kwargs):
        self.calls.append({"query": query, **kwargs})
        return dict(self._responses.get(query, self._default_response))


class _FakeClient:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list[dict[str, object]] = []
        self.chat = type(
            "Chat",
            (),
            {
                "completions": type(
                    "Completions",
                    (),
                    {"create": self._create},
                )()
            },
        )()

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return type(
            "Resp",
            (),
            {"choices": [type("Choice", (), {"message": type("Msg", (), {"content": self.response_text})()})]},
        )()


def test_stage2_targeted_retrieval_applies_keyword_and_entity_guardrails(monkeypatch):
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "true")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "true")
    expert = _Expert(
        default_response={
            "documents": ["doc-1"],
            "metadatas": [{"doi": "10.1/a"}],
            "distances": [0.1],
        }
    )
    client = _FakeClient("cycle life")

    result = run_stage2_targeted_retrieval(
        retrieval_claims=[{"claim": "claim one", "keywords": ["LFP"]}],
        n_results_per_claim=2,
        user_question="Ti 掺杂 LFP 的 cycle life",
        literature_expert=expert,
        logger=logging.getLogger("test.stage2"),
        client=client,
        model="gpt-test",
        preprocess_retrieval_query_fn=lambda query: query,
        validate_retrieval_relevance_fn=lambda results, query, claim: results,
        extract_question_keywords_fn=lambda question: ["LFP", "cycle life"],
    )

    assert result["success"] is True
    query = result["claim_to_results"]["claim one"]["query"]
    assert "LFP" in query
    assert "Ti" in query
    assert "cycle life" in query
    assert result["claim_to_results"]["claim one"]["query_guardrail"]["injected_entities"] == ["Ti"]
    assert expert.calls[0]["query"] == query
    assert client.calls[0]["model"] == "gpt-test"


def test_stage2_targeted_retrieval_uses_query_expansion_when_enabled(monkeypatch):
    monkeypatch.setenv("QA_STAGE2_QUERY_EXPANSION_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    expert = _Expert(
        responses={
            "expanded term": {
                "documents": ["doc-a"],
                "metadatas": [{"doi": "10.2/b"}],
                "distances": [0.1],
            }
        }
    )

    result = run_stage2_targeted_retrieval(
        retrieval_claims=[{"claim": "claim one", "keywords": []}],
        n_results_per_claim=1,
        user_question="battery cycle life",
        literature_expert=expert,
        logger=logging.getLogger("test.stage2"),
        client=None,
        model=None,
        preprocess_retrieval_query_fn=lambda query: query,
        validate_retrieval_relevance_fn=lambda results, query, claim: results,
        expand_query_fn=lambda query: "expanded term",
    )

    assert result["success"] is True
    assert result["claim_to_results"]["claim one"]["query"] == "expanded term"
    assert expert.calls[0]["query"] == "expanded term"


def test_stage2_targeted_retrieval_passes_rerank_arguments(monkeypatch):
    monkeypatch.setenv("QA_RETRIEVAL_RERANK_ENABLED", "true")
    monkeypatch.setenv("QA_RETRIEVAL_RERANK_CANDIDATES", "7")
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    expert = _Expert(
        responses={
            "claim one": {
                "documents": ["doc-a"],
                "metadatas": [{"doi": "10.3/c"}],
                "distances": [0.1],
                "rerank": {"provider": "test"},
            }
        }
    )

    result = run_stage2_targeted_retrieval(
        retrieval_claims=[{"claim": "claim one", "keywords": []}],
        n_results_per_claim=1,
        user_question="battery cycle life",
        literature_expert=expert,
        logger=logging.getLogger("test.stage2"),
        client=None,
        model=None,
        preprocess_retrieval_query_fn=lambda query: query,
        validate_retrieval_relevance_fn=lambda results, query, claim: results,
    )

    assert result["success"] is True
    assert expert.calls[0]["use_rerank"] is True
    assert expert.calls[0]["rerank_candidates"] == 7
    assert result["claim_to_results"]["claim one"]["rerank"] == {"provider": "test"}


def test_stage2_targeted_retrieval_records_relevance_validation(monkeypatch):
    monkeypatch.delenv("QA_STAGE2_QUERY_EXPANSION_ENABLED", raising=False)
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    expert = _Expert(
        responses={
            "claim one": {
                "documents": ["doc-a", "doc-b"],
                "metadatas": [{"doi": "10.1/a"}, {"doi": "10.1/b"}],
                "distances": [0.1, 0.3],
            }
        }
    )
    calls: list[tuple[str, str]] = []

    def _validate(results, query, claim):
        calls.append((query, claim))
        return {
            "documents": ["doc-a"],
            "metadatas": [{"doi": "10.1/a"}],
            "distances": [0.1],
        }

    result = run_stage2_targeted_retrieval(
        retrieval_claims=[{"claim": "claim one", "keywords": []}],
        n_results_per_claim=1,
        user_question="battery cycle life",
        literature_expert=expert,
        logger=logging.getLogger("test.stage2"),
        client=None,
        model=None,
        preprocess_retrieval_query_fn=lambda query: query,
        validate_retrieval_relevance_fn=_validate,
    )

    assert result["success"] is True
    assert calls == [("claim one", "claim one")]
    assert result["documents"] == ["doc-a"]
    assert result["claim_to_results"]["claim one"]["relevance_validation"] == {"before": 2, "after": 1}


def test_stage2_targeted_retrieval_returns_cancelled_payload():
    result = run_stage2_targeted_retrieval(
        retrieval_claims=[{"claim": "x"}],
        n_results_per_claim=1,
        user_question="q",
        literature_expert=None,
        logger=logging.getLogger("test.stage2"),
        should_cancel=lambda: True,
    )

    assert result["success"] is False
    assert result["cancelled"] is True
    assert result["documents"] == []
