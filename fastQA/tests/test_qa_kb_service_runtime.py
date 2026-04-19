from __future__ import annotations

from dataclasses import dataclass

from app.modules.graph_kb.models import GraphRagPayload
from app.modules.qa_kb.models import QaKbRequest
from app.modules.qa_kb.service import qa_kb_service


@dataclass
class _Runtime:
    stage1_payload: dict
    stage2_payload: dict
    doi_payload: list[str]
    stage25_payload: dict
    stage3_payload: dict[str, list[dict]]
    stage4_payload: list
    model: str = "qwen-test"
    stage1_prompt: str = "prompt"

    def __post_init__(self) -> None:
        self.stage1_graph_context = None
        self.stage4_graph_fact_block = None

    def _get_vector_db_context_for_prompt(self) -> str:
        return "context"

    def stage1_pre_answer_and_planning(self, user_question: str, graph_context=None) -> dict:
        self.stage1_graph_context = graph_context
        return dict(self.stage1_payload)

    def stage2_targeted_retrieval(self, retrieval_claims, n_results_per_claim=10, user_question=None, should_cancel=None, active_stream_count=None) -> dict:
        return dict(self.stage2_payload)

    def stage25_md_expansion(self, *, retrieval_results: dict, user_question: str, dois: list[str]) -> dict:
        return dict(self.stage25_payload)

    def _extract_dois_from_results(self, retrieval_results: dict) -> list[str]:
        return list(self.doi_payload)

    def stage3_load_pdf_chunks(self, dois, max_chunks_per_doi=3, should_cancel=None):
        return {key: list(value) for key, value in self.stage3_payload.items()}

    def stage4_synthesis_with_pdf_chunks(self, user_question, deep_answer, pdf_chunks, retrieval_results=None, should_cancel=None, conversation_context=None, graph_fact_block=""):
        self.stage4_graph_fact_block = graph_fact_block
        for item in self.stage4_payload:
            yield item


def test_service_run_generation_pipeline_uses_real_orchestrator():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": False, "error": "retrieval_failed"},
        doi_payload=[],
        stage25_payload={},
        stage3_payload={},
        stage4_payload=[],
    )

    result = qa_kb_service.run_generation_pipeline(
        question="hello",
        generation_runtime=runtime,
        redis_service=None,
        n_results_per_claim=5,
    )

    assert result.success is True
    assert result.final_answer == "deep"


def test_service_iter_generation_answer_events_streams_payloads():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": True, "documents": ["doc"], "metadatas": [{"doi": "10.1"}], "distances": [0.1]},
        doi_payload=["10.1"],
        stage25_payload={"enabled": False, "applied": False, "md_chunks_by_doi": {}, "stats": {}},
        stage3_payload={"10.1": [{"text": "evidence"}]},
        stage4_payload=["a", "b", {"success": True, "final_answer": "ab", "query_mode": "生成驱动检索（PDF溯源）", "references": []}],
    )

    events = list(
        qa_kb_service.iter_generation_answer_events(
            question="hello",
            generation_runtime=runtime,
            redis_service=None,
            sse_event=lambda payload: payload,
            n_results_per_claim=5,
        )
    )

    assert any(event.get("type") == "content" for event in events)
    assert events[-1]["type"] == "done"


def test_service_iter_answer_events_normalizes_thinking_into_steps():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": False, "error": "retrieval_failed"},
        doi_payload=[],
        stage25_payload={},
        stage3_payload={},
        stage4_payload=[],
    )

    events = list(
        qa_kb_service.iter_answer_events(
            request=QaKbRequest(question="hello", route_hint="kb_qa", trace_id="trace-1"),
            generation_runtime=runtime,
            redis_service=None,
            sse_event=lambda payload: payload,
        )
    )

    assert events[0]["type"] == "step"
    assert events[-1]["type"] == "done"


def test_service_run_generation_pipeline_passes_graph_context_and_fact_block():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": True, "documents": ["doc"], "metadatas": [{"doi": "10.1"}], "distances": [0.1]},
        doi_payload=["10.1"],
        stage25_payload={"enabled": False, "applied": False, "md_chunks_by_doi": {}, "stats": {}},
        stage3_payload={"10.1": [{"text": "evidence"}]},
        stage4_payload=[{"success": True, "final_answer": "final", "query_mode": "生成驱动检索（PDF溯源）", "references": []}],
    )

    result = qa_kb_service.run_generation_pipeline(
        question="hello",
        generation_runtime=runtime,
        redis_service=None,
        n_results_per_claim=5,
        graph_evidence=GraphRagPayload(
            stage1_context_block="doi:10.1000/test",
            stage4_fact_block="structured graph facts",
            cache_fingerprint="graph:abc",
        ),
    )

    assert result.success is True
    assert runtime.stage1_graph_context == "doi:10.1000/test"
    assert runtime.stage4_graph_fact_block == "structured graph facts"
