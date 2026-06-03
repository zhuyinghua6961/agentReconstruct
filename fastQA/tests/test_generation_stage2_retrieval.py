from __future__ import annotations

import httpx
import logging
from contextlib import contextmanager
from threading import Event
import time
from types import SimpleNamespace

from app.modules.graph_kb.models import GraphRagPayload
from app.integrations.llm.upstream_gate import Stage2UpstreamGateCancelled
from app.modules.generation_pipeline.stage2_retrieval import (
    resolve_stage2_upstream_gate_limit,
    run_stage2_targeted_retrieval,
)
from app.modules.microscopic_expert import MicroscopicSemanticExpert


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


class _PoolTimeoutClient(_FakeClient):
    def _create(self, **kwargs):
        self.calls.append(kwargs)
        raise httpx.PoolTimeout("pool exhausted")


class _LanePool:
    def __init__(self, lane_client) -> None:
        self.lane_client = lane_client
        self.lease_called = False
        self.used_trace_labels: list[str | None] = []

    @contextmanager
    def lease_lane(self, *, trace_label: str | None = None):
        self.lease_called = True
        self.used_trace_labels.append(trace_label)
        yield SimpleNamespace(client=self.lane_client, lane_id=0)

    def snapshot(self):
        return {"ready_lanes": 1}


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
    claim = result["claim_to_results"]["claim one"]
    query = claim["query"]
    embedding_q = claim["embedding_query"]
    assert "LFP" in query
    assert "Ti" in query
    assert "cycle life" in query
    assert claim["query_guardrail"]["injected_entities"] == ["Ti"]
    assert expert.calls[0]["query"] == embedding_q
    assert "Ti" in embedding_q
    assert "LFP" in embedding_q
    assert client.calls[0]["model"] == "gpt-test"


def test_stage2_targeted_retrieval_groups_comparison_objects(monkeypatch):
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    monkeypatch.setenv("QA_STAGE2_QUERY_EXPANSION_ENABLED", "false")
    comparison_plan = {
        "enabled": True,
        "objects": [
            {
                "label": "草酸亚铁",
                "aliases": ["FeC2O4", "ferrous oxalate"],
                "must_include_any": ["草酸亚铁", "FeC2O4", "ferrous oxalate"],
                "avoid_confusions": [],
            },
            {
                "label": "铁红",
                "aliases": ["Fe2O3", "hematite", "red iron oxide"],
                "must_include_any": ["铁红", "Fe2O3", "hematite", "red iron oxide"],
                "avoid_confusions": [],
            },
        ],
        "dimensions": ["优势", "劣势"],
        "context_keywords": ["LFP"],
        "min_docs_per_object": 1,
    }
    expert = _Expert(
        default_response={
            "documents": ["doc"],
            "metadatas": [{"doi": "10.1/group"}],
            "distances": [0.1],
        }
    )

    result = run_stage2_targeted_retrieval(
        retrieval_claims=[],
        n_results_per_claim=1,
        user_question="草酸亚铁、铁红作为原料各有什么优劣势？",
        literature_expert=expert,
        logger=logging.getLogger("test.stage2"),
        client=None,
        model=None,
        preprocess_retrieval_query_fn=lambda query: query,
        validate_retrieval_relevance_fn=lambda results, query, claim: results,
        comparison_plan=comparison_plan,
    )

    assert result["success"] is True
    assert [group["label"] for group in result["comparison_groups"]] == ["草酸亚铁", "铁红"]
    assert [group["evidence_status"] for group in result["comparison_groups"]] == ["sufficient", "sufficient"]
    assert len(expert.calls) == 2
    assert "FeC2O4" in expert.calls[0]["query"]
    assert "Fe2O3" in expert.calls[1]["query"]


def test_stage2_comparison_keeps_reranked_candidates_without_hard_noise_filter(monkeypatch):
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    monkeypatch.setenv("QA_STAGE2_QUERY_EXPANSION_ENABLED", "false")
    comparison_plan = {
        "enabled": True,
        "objects": [
            {
                "label": "磷酸铁",
                "aliases": ["FePO4", "iron phosphate"],
                "must_include_any": ["磷酸铁", "FePO4", "iron phosphate"],
                "positive_context_terms": ["LiFePO4 synthesis", "iron source", "precursor"],
                "negative_context_terms": ["recycling", "spent battery", "wastewater"],
                "retrieval_queries": ["FePO4 as iron source precursor for LiFePO4 synthesis advantages disadvantages"],
            }
        ],
        "dimensions": ["优势", "劣势"],
        "context_keywords": ["LFP"],
        "min_docs_per_object": 1,
    }
    expert = _Expert(
        default_response={
            "documents": [
                "FePO4 is used as an iron source precursor for LiFePO4 synthesis and improves phase purity.",
                "Spent battery recycling recovers FePO4 from wastewater separation residue.",
                "General LiFePO4 energy storage application review without iron phosphate route details.",
            ],
            "metadatas": [{"doi": "10.1/good"}, {"doi": "10.1/recycling"}, {"doi": "10.1/application"}],
            "distances": [0.1, 0.2, 0.3],
            "rerank": {"enabled": True, "applied": True},
        }
    )

    result = run_stage2_targeted_retrieval(
        retrieval_claims=[],
        n_results_per_claim=3,
        user_question="磷酸铁作为原料制备磷酸铁锂粉体有什么优劣势？",
        literature_expert=expert,
        logger=logging.getLogger("test.stage2"),
        client=None,
        model=None,
        preprocess_retrieval_query_fn=lambda query: query,
        validate_retrieval_relevance_fn=lambda results, query, claim: results,
        comparison_plan=comparison_plan,
    )

    assert result["documents"] == [
        "FePO4 is used as an iron source precursor for LiFePO4 synthesis and improves phase purity.",
        "Spent battery recycling recovers FePO4 from wastewater separation residue.",
        "General LiFePO4 energy storage application review without iron phosphate route details.",
    ]
    assert result["metadatas"] == [{"doi": "10.1/good"}, {"doi": "10.1/recycling"}, {"doi": "10.1/application"}]
    assert result["comparison_groups"][0]["doi_candidates"] == ["10.1/good", "10.1/recycling", "10.1/application"]
    comparison_claim_key = next(iter(result["claim_to_results"]))
    assert comparison_claim_key.startswith("围绕当前问题检索「磷酸铁」")
    assert result["claim_to_results"][comparison_claim_key]["noise_filter"] == {
        "enabled": False,
        "before": 3,
        "after": 3,
        "reason": "disabled_stage2_preserve_rerank_candidates",
    }


def test_stage2_comparison_uses_profile_retrieval_query(monkeypatch):
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    monkeypatch.setenv("QA_STAGE2_QUERY_EXPANSION_ENABLED", "false")
    comparison_plan = {
        "enabled": True,
        "objects": [
            {
                "label": "磷酸铁",
                "aliases": ["FePO4"],
                "must_include_any": ["磷酸铁", "FePO4"],
                "retrieval_queries": ["FePO4 as iron source precursor for LiFePO4 synthesis route evidence"],
            }
        ],
        "dimensions": ["优势", "劣势"],
        "context_keywords": ["LFP"],
        "min_docs_per_object": 1,
    }
    expert = _Expert(default_response={"documents": ["doc"], "metadatas": [{"doi": "10.1/a"}], "distances": [0.1]})

    result = run_stage2_targeted_retrieval(
        retrieval_claims=[],
        n_results_per_claim=1,
        user_question="磷酸铁作为原料有什么优劣势？",
        literature_expert=expert,
        logger=logging.getLogger("test.stage2"),
        client=None,
        model=None,
        preprocess_retrieval_query_fn=lambda query: query,
        validate_retrieval_relevance_fn=lambda results, query, claim: results,
        comparison_plan=comparison_plan,
    )

    assert result["success"] is True
    emb = next(iter(result["claim_to_results"].values()))["embedding_query"]
    assert expert.calls[0]["query"] == emb
    assert "iron source precursor" in emb.lower()
    assert "fepo4" in emb.lower() or "FePO4" in emb
    assert result["comparison_groups"][0]["queries"] == [emb]


def test_stage2_comparison_query_expansion_keeps_object_lock(monkeypatch):
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    monkeypatch.setenv("QA_STAGE2_QUERY_EXPANSION_ENABLED", "true")
    comparison_plan = {
        "enabled": True,
        "objects": [
            {
                "label": "草酸亚铁",
                "aliases": ["FeC2O4", "ferrous oxalate"],
                "must_include_any": ["草酸亚铁", "FeC2O4", "ferrous oxalate"],
                "avoid_confusions": [],
            }
        ],
        "dimensions": ["优势", "劣势"],
        "context_keywords": ["LFP"],
        "min_docs_per_object": 1,
    }
    expert = _Expert(default_response={"documents": ["doc"], "metadatas": [{"doi": "10.1/a"}], "distances": [0.1]})

    result = run_stage2_targeted_retrieval(
        retrieval_claims=[],
        n_results_per_claim=1,
        user_question="草酸亚铁作为原料有什么优劣势？",
        literature_expert=expert,
        logger=logging.getLogger("test.stage2"),
        client=None,
        model=None,
        preprocess_retrieval_query_fn=lambda query: query,
        validate_retrieval_relevance_fn=lambda results, query, claim: results,
        expand_query_fn=lambda query: "generic LFP synthesis advantages disadvantages",
        comparison_plan=comparison_plan,
    )

    assert result["success"] is True
    assert "草酸亚铁" in expert.calls[0]["query"] or "FeC2O4" in expert.calls[0]["query"]
    assert result["comparison_groups"][0]["queries"] == [expert.calls[0]["query"]]


def test_stage2_query_generation_uses_leased_chat_lane(monkeypatch):
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    expert = _Expert(
        responses={
            "lane generated query": {
                "documents": ["doc-a"],
                "metadatas": [{"doi": "10.2/a"}],
                "distances": [0.1],
            }
        }
    )
    fallback_client = _FakeClient("fallback query")
    lane_client = _FakeClient("lane generated query")
    lane_pool = _LanePool(lane_client)

    result = run_stage2_targeted_retrieval(
        retrieval_claims=[{"claim": "claim one", "keywords": []}],
        n_results_per_claim=1,
        user_question="battery cycle life",
        literature_expert=expert,
        logger=logging.getLogger("test.stage2"),
        client=fallback_client,
        model="gpt-test",
        preprocess_retrieval_query_fn=lambda query: query,
        validate_retrieval_relevance_fn=lambda results, query, claim: results,
        chat_lane_pool=lane_pool,
    )

    assert result["success"] is True
    assert lane_pool.lease_called is True
    assert lane_pool.used_trace_labels == ["claim_1"]
    assert fallback_client.calls == []
    assert lane_client.calls[0]["model"] == "gpt-test"
    assert result["claim_to_results"]["claim one"]["query"] == "lane generated query"


def test_stage2_query_generation_disables_thinking_for_thinking_model(monkeypatch):
    monkeypatch.setenv("LLM_IS_THINKING_MODEL", "true")
    monkeypatch.setenv("LLM_THINKING_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    expert = _Expert(
        responses={
            "generated query": {
                "documents": ["doc-a"],
                "metadatas": [{"doi": "10.2/a"}],
                "distances": [0.1],
            }
        }
    )
    client = _FakeClient("generated query")

    result = run_stage2_targeted_retrieval(
        retrieval_claims=[{"claim": "claim one", "keywords": []}],
        n_results_per_claim=1,
        user_question="battery cycle life",
        literature_expert=expert,
        logger=logging.getLogger("test.stage2"),
        client=client,
        model="gpt-test",
        preprocess_retrieval_query_fn=lambda query: query,
        validate_retrieval_relevance_fn=lambda results, query, claim: results,
    )

    assert result["success"] is True
    assert client.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_stage2_query_generation_logs_chat_lane_lease(monkeypatch, caplog):
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    expert = _Expert(
        responses={
            "lane generated query": {
                "documents": ["doc-a"],
                "metadatas": [{"doi": "10.2/a"}],
                "distances": [0.1],
            }
        }
    )
    lane_pool = _LanePool(_FakeClient("lane generated query"))
    test_logger = logging.getLogger("test.stage2.lease")

    with caplog.at_level(logging.INFO, logger="test.stage2.lease"):
        result = run_stage2_targeted_retrieval(
            retrieval_claims=[{"claim": "claim one", "keywords": []}],
            n_results_per_claim=1,
            user_question="battery cycle life",
            literature_expert=expert,
            logger=test_logger,
            client=_FakeClient("fallback query"),
            model="gpt-test",
            preprocess_retrieval_query_fn=lambda query: query,
            validate_retrieval_relevance_fn=lambda results, query, claim: results,
            chat_lane_pool=lane_pool,
        )

    assert result["success"] is True
    assert "stage2 chat lane lease trace_label=claim_1 lane=0 ready=true" in caplog.text


def test_stage2_chat_gate_uses_ready_lane_count():
    assert resolve_stage2_upstream_gate_limit(configured_limit=5, ready_lanes=2, effective_parallel_workers=5) == 2


def test_stage2_rerank_gate_uses_ready_lane_count():
    assert resolve_stage2_upstream_gate_limit(configured_limit=5, ready_lanes=3, effective_parallel_workers=4) == 3


def test_stage2_bypasses_gate_when_no_ready_lanes():
    assert resolve_stage2_upstream_gate_limit(configured_limit=5, ready_lanes=0, effective_parallel_workers=5) is None


def test_stage2_query_generation_passes_request_limit_and_cancel_to_shared_gate(monkeypatch):
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    monkeypatch.setenv("FASTQA_STAGE2_CHAT_GATE_MAX_IN_FLIGHT", "3")
    expert = _Expert(
        responses={
            "lane generated query": {
                "documents": ["doc-a"],
                "metadatas": [{"doi": "10.2/a"}],
                "distances": [0.1],
            }
        }
    )
    lane_pool = _LanePool(_FakeClient("lane generated query"))
    gate_calls: list[dict[str, object]] = []

    class _Gate:
        @contextmanager
        def enter(self, *, trace_label=None, request_limit=None, should_cancel=None):
            gate_calls.append(
                {
                    "trace_label": trace_label,
                    "request_limit": request_limit,
                    "cancelled": bool(should_cancel and should_cancel()),
                }
            )
            yield

    result = run_stage2_targeted_retrieval(
        retrieval_claims=[{"claim": "claim one", "keywords": []}],
        n_results_per_claim=1,
        user_question="battery cycle life",
        literature_expert=expert,
        logger=logging.getLogger("test.stage2"),
        client=_FakeClient("fallback query"),
        model="gpt-test",
        preprocess_retrieval_query_fn=lambda query: query,
        validate_retrieval_relevance_fn=lambda results, query, claim: results,
        chat_lane_pool=lane_pool,
        chat_gate=_Gate(),
    )

    assert result["success"] is True
    assert gate_calls == [{"trace_label": "claim_1", "request_limit": 1, "cancelled": False}]


def test_stage2_targeted_retrieval_returns_cancelled_payload_when_gate_wait_is_cancelled(monkeypatch):
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    expert = _Expert()

    class _Gate:
        def enter(self, *, trace_label=None, request_limit=None, should_cancel=None):
            raise Stage2UpstreamGateCancelled("stage2 chat gate wait cancelled")

    result = run_stage2_targeted_retrieval(
        retrieval_claims=[{"claim": "claim one", "keywords": []}],
        n_results_per_claim=1,
        user_question="battery cycle life",
        literature_expert=expert,
        logger=logging.getLogger("test.stage2"),
        client=_FakeClient("fallback query"),
        model="gpt-test",
        preprocess_retrieval_query_fn=lambda query: query,
        validate_retrieval_relevance_fn=lambda results, query, claim: results,
        chat_lane_pool=_LanePool(_FakeClient("lane generated query")),
        chat_gate=_Gate(),
        should_cancel=lambda: False,
    )

    assert result["success"] is True
    assert result["claim_to_results"] == {}


def test_stage2_targeted_retrieval_aborts_chat_lane_when_cancelled_mid_call(monkeypatch):
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    expert = _Expert()
    started = Event()
    cancel_request = Event()

    class _BlockingClient(_FakeClient):
        def _create(self, **kwargs):
            self.calls.append(kwargs)
            started.set()
            while not cancel_request.is_set():
                time.sleep(0.01)
            time.sleep(0.2)
            return super()._create(**kwargs)

    class _AbortableLanePool(_LanePool):
        def __init__(self, lane_client) -> None:
            super().__init__(lane_client)
            self.abort_calls: list[tuple[int, str]] = []

        def abort_lane(self, lane_id: int, *, error_summary: str = "cancelled") -> None:
            self.abort_calls.append((lane_id, error_summary))

    lane_pool = _AbortableLanePool(_BlockingClient("lane generated query"))

    def _should_cancel() -> bool:
        if started.is_set():
            cancel_request.set()
        return cancel_request.is_set()

    result = run_stage2_targeted_retrieval(
        retrieval_claims=[{"claim": "claim one", "keywords": []}],
        n_results_per_claim=1,
        user_question="battery cycle life",
        literature_expert=expert,
        logger=logging.getLogger("test.stage2"),
        client=_FakeClient("fallback query"),
        model="gpt-test",
        preprocess_retrieval_query_fn=lambda query: query,
        validate_retrieval_relevance_fn=lambda results, query, claim: results,
        chat_lane_pool=lane_pool,
        should_cancel=_should_cancel,
    )

    assert result["success"] is False
    assert result["cancelled"] is True
    assert lane_pool.abort_calls == [(0, "cancelled")]


def test_stage2_targeted_retrieval_propagates_rerank_cancellation_from_real_microscopic_expert(monkeypatch):
    monkeypatch.setenv("QA_RETRIEVAL_RERANK_ENABLED", "true")
    monkeypatch.setenv("QA_RETRIEVAL_RERANK_CANDIDATES", "8")
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    started = Event()
    cancel_request = Event()

    class _Embedding:
        def encode(self, _values):
            return type("Array", (), {"tolist": lambda self: [[0.1, 0.2]]})()

    class _Collection:
        def count(self):
            return 8

        def query(self, **_kwargs):
            return {
                "documents": [["doc-1", "doc-2"]],
                "distances": [[0.1, 0.2]],
                "metadatas": [[{"doi": "10.1/a"}, {"doi": "10.2/b"}]],
                "ids": [["id-1", "id-2"]],
            }

    class _LanePool:
        def __init__(self) -> None:
            self.abort_calls: list[tuple[int, str]] = []

        @contextmanager
        def lease_lane(self, *, trace_label=None):
            yield SimpleNamespace(session=object(), lane_id=0)

        def abort_lane(self, lane_id: int, *, error_summary: str = "cancelled") -> None:
            self.abort_calls.append((lane_id, error_summary))

    def _fake_rerank_documents(**kwargs):
        started.set()
        while not cancel_request.is_set():
            time.sleep(0.01)
        return {
            "documents": ["doc-1"],
            "metadatas": [{"doi": "10.1/a"}],
            "rerank_scores": [0.9],
            "fallback": False,
            "provider": "test",
        }

    monkeypatch.setattr("app.modules.microscopic_expert.rerank_documents_impl", _fake_rerank_documents)

    expert = MicroscopicSemanticExpert.__new__(MicroscopicSemanticExpert)
    expert.available = True
    expert.embedding_model = _Embedding()
    expert.collection = _Collection()
    expert.translator = None
    expert.client = None
    expert.rerank_session_pool = _LanePool()

    def _should_cancel() -> bool:
        if started.is_set():
            cancel_request.set()
        return cancel_request.is_set()

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
        should_cancel=_should_cancel,
    )

    assert result["success"] is False
    assert result["cancelled"] is True
    assert expert.rerank_session_pool.abort_calls == [(0, "cancelled")]


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


def test_stage2_targeted_retrieval_keeps_rerank_enabled_when_env_disables(monkeypatch):
    monkeypatch.setenv("QA_RETRIEVAL_RERANK_ENABLED", "false")
    monkeypatch.setenv("QA_RETRIEVAL_RERANK_CANDIDATES", "9")
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
    assert expert.calls[0]["rerank_candidates"] == 9


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


def test_stage2_targeted_retrieval_merges_graph_hints_into_query(monkeypatch):
    monkeypatch.setenv("QA_STAGE2_GRAPH_QUERY_HINT_MERGE_ENABLED", "1")
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    expert = _Expert(
        default_response={
            "documents": ["doc-graph"],
            "metadatas": [{"doi": "10.9/graph"}],
            "distances": [0.1],
        }
    )

    result = run_stage2_targeted_retrieval(
        retrieval_claims=[{"claim": "claim one", "keywords": []}],
        n_results_per_claim=1,
        user_question="battery cycle life",
        literature_expert=expert,
        logger=logging.getLogger("test.stage2"),
        preprocess_retrieval_query_fn=lambda query: query,
        validate_retrieval_relevance_fn=lambda results, query, claim: results,
        graph_evidence=GraphRagPayload(
            stage2_doi_candidates=("10.9/graph",),
            stage2_entity_hints={"materials": ("GRAPH_HINT",)},
            cache_fingerprint="graph:abc",
        ),
    )

    assert result["success"] is True
    assert "GRAPH_HINT" in result["claim_to_results"]["claim one"]["query"]
    assert "10.9/graph" not in result["claim_to_results"]["claim one"]["query"]


def test_stage2_targeted_retrieval_propagates_pool_timeout_from_ai_query_generation(monkeypatch):
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    monkeypatch.setenv("QA_STAGE2_QUERY_EXPANSION_ENABLED", "false")
    expert = _Expert(
        default_response={
            "documents": ["doc-1"],
            "metadatas": [{"doi": "10.1/a"}],
            "distances": [0.1],
        }
    )
    client = _PoolTimeoutClient("ignored")

    try:
        run_stage2_targeted_retrieval(
            retrieval_claims=[{"claim": "claim one", "keywords": []}],
            n_results_per_claim=1,
            user_question="battery cycle life",
            literature_expert=expert,
            logger=logging.getLogger("test.stage2"),
            client=client,
            model="gpt-test",
            preprocess_retrieval_query_fn=lambda query: query,
            validate_retrieval_relevance_fn=lambda results, query, claim: results,
        )
    except httpx.PoolTimeout:
        pass
    else:  # pragma: no cover - enforced by failing test before fix
        raise AssertionError("expected PoolTimeout to propagate")

    assert expert.calls == []


def test_stage2_targeted_retrieval_propagates_pool_timeout_from_query_expansion(monkeypatch):
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    monkeypatch.setenv("QA_STAGE2_QUERY_EXPANSION_ENABLED", "true")
    expert = _Expert(
        default_response={
            "documents": ["doc-1"],
            "metadatas": [{"doi": "10.1/a"}],
            "distances": [0.1],
        }
    )

    def _raise_pool_timeout(_query: str) -> str:
        raise httpx.PoolTimeout("pool exhausted")

    try:
        run_stage2_targeted_retrieval(
            retrieval_claims=[{"claim": "claim one", "keywords": []}],
            n_results_per_claim=1,
            user_question="battery cycle life",
            literature_expert=expert,
            logger=logging.getLogger("test.stage2"),
            client=None,
            model=None,
            preprocess_retrieval_query_fn=lambda query: query,
            validate_retrieval_relevance_fn=lambda results, query, claim: results,
            expand_query_fn=_raise_pool_timeout,
        )
    except httpx.PoolTimeout:
        pass
    else:  # pragma: no cover - enforced by failing test before fix
        raise AssertionError("expected PoolTimeout to propagate")

    assert expert.calls == []


def test_stage2_targeted_retrieval_logs_claim_timing_breakdown(monkeypatch, caplog):
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    monkeypatch.setenv("QA_STAGE2_QUERY_EXPANSION_ENABLED", "false")
    expert = _Expert(
        default_response={
            "documents": ["doc-1"],
            "metadatas": [{"doi": "10.1/a"}],
            "distances": [0.1],
        }
    )
    client = _FakeClient("cycle life")
    logger = logging.getLogger("test.stage2.timing")

    with caplog.at_level(logging.INFO, logger=logger.name):
        result = run_stage2_targeted_retrieval(
            retrieval_claims=[{"claim": "claim one", "keywords": []}],
            n_results_per_claim=1,
            user_question="battery cycle life",
            literature_expert=expert,
            logger=logger,
            client=client,
            model="gpt-test",
            preprocess_retrieval_query_fn=lambda query: query,
            validate_retrieval_relevance_fn=lambda results, query, claim: results,
        )

    assert result["success"] is True
    claim_message = next(message for message in caplog.messages if "stage2 claim timing" in message)
    assert "claim=claim one" in claim_message
    assert "ai_query_ms=" in claim_message
    assert "search_total_ms=" in claim_message
    assert "relevance_validation_ms=" in claim_message
    assert "claim_total_ms=" in claim_message


def test_stage2_targeted_retrieval_logs_detailed_diagnostics(monkeypatch, caplog):
    monkeypatch.setenv("QA_STAGE2_DIAGNOSTIC_LOG", "1")
    monkeypatch.setenv("QA_STAGE2_LOG_QUERY_DETAILS", "1")
    monkeypatch.setenv("QA_STAGE2_LOG_HIT_DETAILS", "1")
    monkeypatch.setenv("QA_STAGE2_LOG_HIT_MAX", "3")
    monkeypatch.setenv("QA_STAGE2_FORCE_KEYWORD_INJECTION", "false")
    monkeypatch.setenv("QA_STAGE2_ENTITY_LOCK_ENABLED", "false")
    expert = _Expert(
        default_response={
            "documents": ["PEG 聚乙二醇辅助碳包覆提升 LiFePO4 倍率性能。", "无关背景片段"],
            "metadatas": [{"doi": "10.1/good", "title": "PEG LFP"}, {"doi": "10.1/noise"}],
            "distances": [0.08, 0.42],
        }
    )

    def _validate(results, query, claim):
        return {
            "documents": ["PEG 聚乙二醇辅助碳包覆提升 LiFePO4 倍率性能。"],
            "metadatas": [{"doi": "10.1/good", "title": "PEG LFP"}],
            "distances": [0.08],
        }

    logger = logging.getLogger("test.stage2.diagnostics")
    with caplog.at_level(logging.INFO, logger=logger.name):
        result = run_stage2_targeted_retrieval(
            retrieval_claims=[{"claim": "LiFePO4 PEG 倍率性能", "keywords": ["LiFePO4", "PEG"]}],
            n_results_per_claim=1,
            user_question="PEG 对 LiFePO4 倍率性能有什么影响？",
            literature_expert=expert,
            logger=logger,
            client=None,
            model=None,
            preprocess_retrieval_query_fn=lambda query: query,
            validate_retrieval_relevance_fn=_validate,
        )

    assert result["success"] is True
    messages = [record.message for record in caplog.records if record.name == logger.name]
    assert any("Stage2 diagnostic start" in message and "claim_count=1" in message for message in messages)
    assert any(
        "Stage2 query encoding diagnostic" in message
        and "claim_index=1" in message
        and "embedding_query_chars=" in message
        and "utf8_bytes=" in message
        and "chinese_chars=" in message
        and "has_replacement_char=false" in message
        and "has_mojibake_pattern=false" in message
        for message in messages
    )
    assert any("Stage2 search request" in message and "claim_index=1" in message and "n_results=8" in message for message in messages)
    assert any(
        "Stage2 raw hit detail" in message
        and "claim_index=1" in message
        and "rank=1" in message
        and "doi=10.1/good" in message
        and "distance=0.08" in message
        for message in messages
    )
    assert any(
        "Stage2 relevance validation diagnostic" in message
        and "claim_index=1" in message
        and "before=2" in message
        and "after=1" in message
        and "filtered=1" in message
        for message in messages
    )
    assert any(
        "Stage2 diagnostic summary" in message
        and "ok_claims=1" in message
        and "total_docs=1" in message
        and "unique_docs=1" in message
        and "doi_sample=['10.1/good']" in message
        for message in messages
    )
