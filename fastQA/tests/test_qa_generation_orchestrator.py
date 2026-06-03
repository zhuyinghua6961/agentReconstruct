from __future__ import annotations

import logging
from dataclasses import dataclass

from app.integrations.redis import RedisService
from app.modules.graph_kb.models import GraphRagPayload
from app.modules.qa_cache import reset_cache_metrics, snapshot_cache_metrics
from app.modules.qa_kb.orchestrators.generation import GenerationPipelineOrchestrator, select_source_dois_for_evidence


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
    comparison_profile_payload: dict | None = None
    comparison_profile_calls: list[dict] | None = None

    def _get_vector_db_context_for_prompt(self) -> str:
        return "context"

    def stage1_pre_answer_and_planning(self, user_question: str) -> dict:
        return dict(self.stage1_payload)

    def generate_comparison_retrieval_profile(self, *, user_question: str, comparison_plan: dict, retrieval_claims: list[dict]) -> dict:
        if self.comparison_profile_calls is not None:
            self.comparison_profile_calls.append(
                {
                    "user_question": user_question,
                    "comparison_plan": comparison_plan,
                    "retrieval_claims": retrieval_claims,
                    "model": self.model,
                }
            )
        return dict(self.comparison_profile_payload or {})

    def stage2_targeted_retrieval(self, retrieval_claims, n_results_per_claim=10, user_question=None, should_cancel=None, active_stream_count=None) -> dict:
        return dict(self.stage2_payload)

    def stage25_md_expansion(self, *, retrieval_results: dict, user_question: str, dois: list[str]) -> dict:
        return dict(self.stage25_payload)

    def _extract_dois_from_results(self, retrieval_results: dict) -> list[str]:
        return list(self.doi_payload)

    def stage3_load_pdf_chunks(self, dois, max_chunks_per_doi=3, should_cancel=None):
        return {key: list(value) for key, value in self.stage3_payload.items()}

    def stage4_synthesis_with_pdf_chunks(self, user_question, deep_answer, pdf_chunks, retrieval_results=None, should_cancel=None, conversation_context=None):
        for item in self.stage4_payload:
            yield item


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def get(self, key: str):
        return self.values.get(key)

    def set(self, key: str, value, ex=None, nx=False):
        _ = ex
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def delete(self, key: str):
        return 1 if self.values.pop(key, None) is not None else 0


class _CountingStage25:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def run(self, *, runtime, retrieval_results, user_question, dois):
        _ = (runtime, retrieval_results, user_question, dois)
        self.calls += 1
        return {
            "enabled": bool(self.payload.get("enabled")),
            "applied": bool(self.payload.get("applied")),
            "md_chunks_by_doi": {
                str(doi): [dict(chunk) for chunk in chunks]
                for doi, chunks in dict(self.payload.get("md_chunks_by_doi") or {}).items()
            },
            "stats": dict(self.payload.get("stats") or {}),
        }


class _CountingStage3:
    def __init__(self, payload: dict[str, list[dict]]) -> None:
        self.payload = payload
        self.calls = 0

    def run(self, *, runtime, dois, max_chunks_per_doi=3, should_cancel=None):
        _ = (runtime, dois, max_chunks_per_doi, should_cancel)
        self.calls += 1
        return {str(doi): [dict(chunk) for chunk in chunks] for doi, chunks in self.payload.items()}


def _logger():
    return logging.getLogger("test")


def _merge_stage2_identity(**kwargs):
    """Orchestrator tests stub Stage2 docs without expecting retrieval-snippet injection."""
    return dict(kwargs.get("pdf_chunks") or {})


def test_orchestrator_run_returns_fallback_when_stage2_fails():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": False, "error": "retrieval_failed"},
        doi_payload=[],
        stage25_payload={},
        stage3_payload={},
        stage4_payload=[],
    )
    orchestrator = GenerationPipelineOrchestrator()

    result = orchestrator.run(
        question="hello",
        runtime=runtime,
        redis_service=None,
        n_results_per_claim=5,
        should_cancel=None,
        active_stream_count=None,
        logger=_logger(),
    )

    assert result.success is True
    assert result.final_answer == "deep"
    assert result.metadata.query_mode == "生成驱动检索（检索失败，仅预回答）"


def test_orchestrator_run_returns_final_result_when_stage4_succeeds():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": True, "documents": ["doc"], "metadatas": [{"doi": "10.1"}], "distances": [0.1]},
        doi_payload=["10.1"],
        stage25_payload={"enabled": False, "applied": False, "md_chunks_by_doi": {}, "stats": {}},
        stage3_payload={"10.1": [{"text": "evidence"}]},
        stage4_payload=[{"success": True, "final_answer": "final", "query_mode": "生成驱动检索（PDF溯源）", "references": [{"doi": "10.1"}]}],
    )
    orchestrator = GenerationPipelineOrchestrator(merge_stage2_retrieval_evidence_fn=_merge_stage2_identity)

    result = orchestrator.run(
        question="hello",
        runtime=runtime,
        redis_service=None,
        n_results_per_claim=5,
        should_cancel=None,
        active_stream_count=None,
        logger=_logger(),
    )

    assert result.success is True
    assert result.final_answer == "final"
    assert result.metadata.doi_count == 1
    assert result.metadata.chunk_count == 1
    assert result.metadata.source_count == 1


def test_orchestrator_logs_stage3_handoff_merge_and_rerank_counts(monkeypatch, caplog):
    monkeypatch.setenv("QA_STAGE3_DIAGNOSTIC_LOG", "1")
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={
            "success": True,
            "documents": ["doc"],
            "metadatas": [{"doi": "10.1/a"}],
            "distances": [0.1],
            "unique_count": 1,
            "total_count": 1,
        },
        doi_payload=["10.1/a"],
        stage25_payload={"enabled": False, "applied": False, "md_chunks_by_doi": {}, "stats": {}},
        stage3_payload={"10.1/a": [{"text": "stage3 evidence"}]},
        stage4_payload=[{"success": True, "final_answer": "final", "references": [{"doi": "10.1/a"}]}],
    )

    def _merge(**kwargs):
        chunks = dict(kwargs.get("pdf_chunks") or {})
        chunks.setdefault("10.1/a", []).append({"text": "stage2 evidence", "source": "stage2_retrieval"})
        return chunks

    def _rerank(**kwargs):
        return {
            "pdf_chunks": dict(kwargs["pdf_chunks"]),
            "stats": {"enabled": True, "before_chunk_count": 2, "after_chunk_count": 2},
        }

    logger = logging.getLogger("test.fastqa.orchestrator.stage3")
    orchestrator = GenerationPipelineOrchestrator(
        merge_stage2_retrieval_evidence_fn=_merge,
        evidence_rerank_fn=_rerank,
    )

    with caplog.at_level(logging.INFO, logger="test.fastqa.orchestrator.stage3"):
        result = orchestrator.run(
            question="hello",
            runtime=runtime,
            redis_service=None,
            n_results_per_claim=5,
            should_cancel=None,
            active_stream_count=None,
            logger=logger,
        )

    assert result.success is True
    messages = [record.message for record in caplog.records if record.name == "test.fastqa.orchestrator.stage3"]
    assert any(
        "fastqa stage3 handoff" in message
        and "doi_count=1" in message
        and "all_stage2_dois=1" in message
        and "doi_source=retrieval" in message
        for message in messages
    )
    assert any(
        "fastqa stage3 raw completed" in message
        and "skip_pdf=False" in message
        and "source_count=1" in message
        and "chunk_count=1" in message
        for message in messages
    )
    assert any(
        "fastqa stage3 evidence merge completed" in message
        and "before_chunks=1" in message
        and "after_chunks=2" in message
        for message in messages
    )
    assert any(
        "fastqa stage35 completed" in message
        and "before_chunk_count" in message
        and "source_count=1" in message
        and "chunk_count=2" in message
        for message in messages
    )


def test_orchestrator_passes_reranked_evidence_chunks_to_stage4(monkeypatch):
    monkeypatch.setenv("QA_STAGE35_EVIDENCE_RERANK_ENABLED", "true")
    seen_stage4_chunks: dict[str, list[dict]] = {}
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": True, "documents": ["doc"], "metadatas": [{"doi": "10.1"}], "distances": [0.1]},
        doi_payload=["10.1"],
        stage25_payload={"enabled": False, "applied": False, "md_chunks_by_doi": {}, "stats": {}},
        stage3_payload={"10.1": [{"text": "raw"}]},
        stage4_payload=[{"success": True, "final_answer": "final", "references": [{"doi": "10.1"}]}],
    )

    def _rerank(**kwargs):
        assert kwargs["pdf_chunks"] == {"10.1": [{"text": "raw"}]}
        return {
            "pdf_chunks": {"10.1": [{"text": "reranked", "evidence_score": 0.9}]},
            "stats": {"before_chunk_count": 1, "after_chunk_count": 1},
        }

    class _Stage4:
        def stream(self, **kwargs):
            seen_stage4_chunks.update(kwargs["pdf_chunks"])
            return runtime.stage4_synthesis_with_pdf_chunks(
                user_question=kwargs["user_question"],
                deep_answer=kwargs["deep_answer"],
                pdf_chunks=kwargs["pdf_chunks"],
                retrieval_results=kwargs["retrieval_results"],
                should_cancel=kwargs["should_cancel"],
                conversation_context=kwargs["conversation_context"],
            )

    orchestrator = GenerationPipelineOrchestrator(
        stage4=_Stage4(),
        evidence_rerank_fn=_rerank,
        merge_stage2_retrieval_evidence_fn=_merge_stage2_identity,
    )

    result = orchestrator.run(
        question="hello",
        runtime=runtime,
        redis_service=None,
        n_results_per_claim=5,
        should_cancel=None,
        active_stream_count=None,
        logger=_logger(),
    )

    assert result.success is True
    assert seen_stage4_chunks == {"10.1": [{"text": "reranked", "evidence_score": 0.9}]}
    assert result.raw["evidence_rerank"]["stats"]["after_chunk_count"] == 1


def test_orchestrator_uses_answer_plan_for_retrieval_and_stage4():
    captured: dict = {}
    runtime = _Runtime(
        stage1_payload={
            "success": True,
            "deep_answer": "draft",
            "answer_plan": {
                "answer_type": "process_comparison",
                "dimensions": [{"name": "成本", "evidence_needed": "原料成本和规模化生产数据"}],
                "object_analysis_plan": [
                    {"object": "铁红", "must_verify_with_evidence": ["还原气氛要求", "产物电化学性能"]}
                ],
            },
            "retrieval_claims": [{"claim": "base claim"}],
        },
        stage2_payload={
            "success": True,
            "documents": ["doc"],
            "metadatas": [{"doi": "10.1"}],
            "distances": [0.1],
            "claim_to_results": {},
            "unique_count": 1,
            "total_count": 1,
        },
        doi_payload=["10.1"],
        stage25_payload={"enabled": False, "applied": False, "md_chunks_by_doi": {}, "stats": {}},
        stage3_payload={"10.1": [{"text": "evidence"}]},
        stage4_payload=[],
    )

    class _Stage2:
        def run(self, *, runtime, retrieval_claims, **kwargs):
            _ = (runtime, kwargs)
            captured["retrieval_claims"] = retrieval_claims
            return dict(runtime.stage2_payload)

    class _Stage4:
        def stream(self, *, answer_plan=None, **kwargs):
            _ = kwargs
            captured["answer_plan"] = answer_plan
            yield {"success": True, "final_answer": "final", "references": [{"doi": "10.1"}]}

    orchestrator = GenerationPipelineOrchestrator(stage2=_Stage2(), stage4=_Stage4())

    result = orchestrator.run(
        question="铁红作为原料有什么优劣势？",
        runtime=runtime,
        redis_service=None,
        n_results_per_claim=3,
        should_cancel=None,
        active_stream_count=None,
        logger=_logger(),
    )

    claim_texts = [item["claim"] for item in captured["retrieval_claims"]]
    assert result.final_answer == "final"
    assert "base claim" in claim_texts
    assert any("原料成本和规模化生产数据" in claim for claim in claim_texts)
    assert any("铁红" in claim and "还原气氛要求" in claim for claim in claim_texts)
    assert captured["answer_plan"]["answer_type"] == "process_comparison"


def test_orchestrator_builds_comparison_claims_when_stage1_has_no_claims():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": []},
        stage2_payload={"success": True, "documents": ["doc"], "metadatas": [{"doi": "10.1"}], "distances": [0.1]},
        doi_payload=["10.1"],
        stage25_payload={"enabled": False, "applied": False, "md_chunks_by_doi": {}, "stats": {}},
        stage3_payload={"10.1": [{"text": "evidence"}]},
        stage4_payload=[{"success": True, "final_answer": "final", "query_mode": "生成驱动检索（PDF溯源）", "references": [{"doi": "10.1"}]}],
    )
    captured: dict[str, object] = {}

    class _Stage2:
        def run(
            self,
            *,
            runtime,
            retrieval_claims,
            n_results_per_claim,
            user_question,
            should_cancel=None,
            active_stream_count=None,
            graph_evidence=None,
            comparison_plan=None,
        ):
            captured["retrieval_claims"] = retrieval_claims
            captured["comparison_plan"] = comparison_plan
            return dict(runtime.stage2_payload)

    orchestrator = GenerationPipelineOrchestrator(stage2=_Stage2())

    result = orchestrator.run(
        question="磷酸铁、草酸亚铁、铁红作为原料制备磷酸铁锂粉体各有什么优劣势？",
        runtime=runtime,
        redis_service=None,
        n_results_per_claim=3,
        should_cancel=None,
        active_stream_count=None,
        logger=_logger(),
    )

    assert result.success is True
    assert captured["comparison_plan"]["enabled"] is True
    assert [claim["comparison_object"] for claim in captured["retrieval_claims"]] == ["磷酸铁", "草酸亚铁", "铁红"]
    assert result.raw["comparison_plan"]["enabled"] is True


def test_orchestrator_uses_llm_comparison_retrieval_profile():
    calls: list[dict] = []
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": []},
        stage2_payload={"success": True, "documents": ["doc"], "metadatas": [{"doi": "10.1/fe-po4"}], "distances": [0.1]},
        doi_payload=["10.1/fe-po4"],
        stage25_payload={"enabled": False, "applied": False, "md_chunks_by_doi": {}, "stats": {}},
        stage3_payload={"10.1/fe-po4": [{"text": "evidence"}]},
        stage4_payload=[{"success": True, "final_answer": "final", "references": [{"doi": "10.1/fe-po4"}]}],
        comparison_profile_calls=calls,
        comparison_profile_payload={
            "enabled": True,
            "objects": [
                {
                    "label": "磷酸铁",
                    "aliases": ["FePO4", "iron phosphate"],
                    "retrieval_queries": ["FePO4 as iron source precursor for LiFePO4 synthesis advantages disadvantages"],
                    "must_include_any": ["磷酸铁", "FePO4", "iron phosphate"],
                    "positive_context_terms": ["LiFePO4 synthesis", "iron source", "precursor"],
                    "negative_context_terms": ["recycling", "spent battery", "wastewater"],
                },
                {
                    "label": "草酸亚铁",
                    "aliases": ["FeC2O4", "ferrous oxalate"],
                    "retrieval_queries": ["FeC2O4 as iron source for LiFePO4 synthesis advantages disadvantages"],
                    "must_include_any": ["草酸亚铁", "FeC2O4", "ferrous oxalate"],
                    "positive_context_terms": ["LiFePO4 synthesis", "iron source"],
                    "negative_context_terms": ["recycling", "spent battery"],
                },
                {
                    "label": "铁红",
                    "aliases": ["Fe2O3", "hematite"],
                    "retrieval_queries": ["Fe2O3 hematite as iron source for LiFePO4 synthesis advantages disadvantages"],
                    "must_include_any": ["铁红", "Fe2O3", "hematite"],
                    "positive_context_terms": ["LiFePO4 synthesis", "iron source", "reduction"],
                    "negative_context_terms": ["recycling", "spent battery"],
                },
            ],
        },
    )
    captured: dict[str, object] = {}

    class _Stage2:
        def run(
            self,
            *,
            runtime,
            retrieval_claims,
            n_results_per_claim,
            user_question,
            should_cancel=None,
            active_stream_count=None,
            graph_evidence=None,
            comparison_plan=None,
        ):
            _ = (runtime, n_results_per_claim, user_question, should_cancel, active_stream_count, graph_evidence)
            captured["retrieval_claims"] = retrieval_claims
            captured["comparison_plan"] = comparison_plan
            return dict(runtime.stage2_payload)

    orchestrator = GenerationPipelineOrchestrator(stage2=_Stage2())

    result = orchestrator.run(
        question="磷酸铁、草酸亚铁、铁红作为原料制备磷酸铁锂粉体各有什么优劣势？",
        runtime=runtime,
        redis_service=None,
        n_results_per_claim=3,
        should_cancel=None,
        active_stream_count=None,
        logger=_logger(),
    )

    claims = captured["retrieval_claims"]
    assert result.success is True
    assert calls and calls[0]["model"] == "qwen-test"
    assert claims[0]["query"] == "FePO4 as iron source precursor for LiFePO4 synthesis advantages disadvantages"
    assert claims[0]["positive_context_terms"] == ["LiFePO4 synthesis", "iron source", "precursor"]
    assert claims[0]["negative_context_terms"] == ["recycling", "spent battery", "wastewater"]
    assert captured["comparison_plan"]["objects"][0]["retrieval_queries"] == [
        "FePO4 as iron source precursor for LiFePO4 synthesis advantages disadvantages"
    ]


def test_select_source_dois_limits_comparison_dois_per_object(monkeypatch):
    monkeypatch.setenv("QA_SOURCE_DOI_MAX_PER_COMPARISON_OBJECT", "2")
    monkeypatch.setenv("QA_SOURCE_DOI_MAX_TOTAL", "5")
    retrieval_results = {
        "comparison_groups": [
            {"label": "磷酸铁", "doi_candidates": ["10.1/a", "10.1/b", "10.1/c"]},
            {"label": "草酸亚铁", "doi_candidates": ["10.2/a", "10.2/b", "10.2/c"]},
            {"label": "铁红", "doi_candidates": ["10.3/a", "10.3/b", "10.3/c"]},
        ]
    }

    selected = select_source_dois_for_evidence(
        retrieval_results=retrieval_results,
        dois=["10.1/a", "10.1/b", "10.1/c", "10.2/a", "10.2/b", "10.2/c", "10.3/a", "10.3/b", "10.3/c"],
    )

    assert selected == ["10.1/a", "10.2/a", "10.3/a", "10.1/b", "10.2/b"]


def test_orchestrator_passes_selected_dois_to_stage25_and_stage3(monkeypatch):
    monkeypatch.setenv("QA_SOURCE_DOI_MAX_PER_COMPARISON_OBJECT", "1")
    monkeypatch.setenv("QA_SOURCE_DOI_MAX_TOTAL", "3")
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": []},
        stage2_payload={
            "success": True,
            "documents": ["doc"],
            "metadatas": [{"doi": "10.1/a"}],
            "distances": [0.1],
            "comparison_groups": [
                {"label": "磷酸铁", "doi_candidates": ["10.1/a", "10.1/b"]},
                {"label": "草酸亚铁", "doi_candidates": ["10.2/a", "10.2/b"]},
                {"label": "铁红", "doi_candidates": ["10.3/a", "10.3/b"]},
            ],
        },
        doi_payload=["10.1/a", "10.1/b", "10.2/a", "10.2/b", "10.3/a", "10.3/b"],
        stage25_payload={"enabled": False, "applied": False, "md_chunks_by_doi": {}, "stats": {}},
        stage3_payload={
            "10.1/a": [{"text": "a"}],
            "10.2/a": [{"text": "b"}],
            "10.3/a": [{"text": "c"}],
        },
        stage4_payload=[{"success": True, "final_answer": "final", "references": []}],
    )

    class _RecordingStage25:
        def __init__(self):
            self.dois = None

        def run(self, *, runtime, retrieval_results, user_question, dois):
            _ = (runtime, retrieval_results, user_question)
            self.dois = list(dois)
            return {"enabled": False, "applied": False, "md_chunks_by_doi": {}, "stats": {}}

    class _RecordingStage3:
        def __init__(self):
            self.dois = None

        def run(self, *, runtime, dois, max_chunks_per_doi=3, should_cancel=None):
            _ = (runtime, max_chunks_per_doi, should_cancel)
            self.dois = list(dois)
            return {doi: [{"text": doi}] for doi in dois}

    stage25 = _RecordingStage25()
    stage3 = _RecordingStage3()
    orchestrator = GenerationPipelineOrchestrator(stage25=stage25, stage3=stage3)

    result = orchestrator.run(
        question="磷酸铁、草酸亚铁、铁红作为原料制备磷酸铁锂粉体各有什么优劣势？",
        runtime=runtime,
        redis_service=None,
        n_results_per_claim=3,
        should_cancel=None,
        active_stream_count=None,
        logger=_logger(),
    )

    assert result.success is True
    assert stage25.dois == ["10.1/a", "10.2/a", "10.3/a"]
    assert stage3.dois == ["10.1/a", "10.2/a", "10.3/a"]
    assert result.raw["dois"] == ["10.1/a", "10.2/a", "10.3/a"]
    assert result.raw["all_stage2_dois"] == ["10.1/a", "10.1/b", "10.2/a", "10.2/b", "10.3/a", "10.3/b"]



def test_orchestrator_passes_conversation_context_to_stage1():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": []},
        stage2_payload={"success": False, "error": "retrieval_failed"},
        doi_payload=[],
        stage25_payload={},
        stage3_payload={},
        stage4_payload=[],
    )
    captured: dict[str, object] = {}

    class _Stage1:
        def run(self, *, runtime, user_question, conversation_context=None):
            captured["runtime"] = runtime
            captured["user_question"] = user_question
            captured["conversation_context"] = conversation_context
            return {"success": True, "deep_answer": "deep", "retrieval_claims": []}

    orchestrator = GenerationPipelineOrchestrator(stage1=_Stage1())

    result = orchestrator.run(
        question="hello",
        runtime=runtime,
        redis_service=None,
        n_results_per_claim=5,
        should_cancel=None,
        active_stream_count=None,
        logger=_logger(),
        conversation_context={
            "recent_turns_for_llm": [{"role": "assistant", "content": "prev"}],
            "summary_for_llm": {"short_summary": "sum"},
        },
    )

    assert result.success is True
    assert captured["user_question"] == "hello"
    assert captured["conversation_context"] == {
        "recent_turns_for_llm": [{"role": "assistant", "content": "prev"}],
        "summary_for_llm": {"short_summary": "sum"},
    }


def test_orchestrator_passes_should_cancel_to_stage1():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": []},
        stage2_payload={"success": False, "error": "retrieval_failed"},
        doi_payload=[],
        stage25_payload={},
        stage3_payload={},
        stage4_payload=[],
    )
    captured: dict[str, object] = {}

    class _Stage1:
        def run(self, *, runtime, user_question, conversation_context=None, should_cancel=None):
            captured["should_cancel"] = should_cancel
            return {"success": True, "deep_answer": "deep", "retrieval_claims": []}

    should_cancel = lambda: False
    orchestrator = GenerationPipelineOrchestrator(stage1=_Stage1())

    orchestrator.run(
        question="hello",
        runtime=runtime,
        redis_service=None,
        n_results_per_claim=5,
        should_cancel=should_cancel,
        active_stream_count=None,
        logger=_logger(),
    )

    assert captured["should_cancel"] is should_cancel


def test_orchestrator_does_not_cache_cancelled_stage1_result():
    reset_cache_metrics()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = _Runtime(
        stage1_payload={"success": False, "deep_answer": "", "retrieval_claims": [], "metadata": {"cancelled": True}},
        stage2_payload={"success": False, "error": "retrieval_failed"},
        doi_payload=[],
        stage25_payload={},
        stage3_payload={},
        stage4_payload=[],
    )
    orchestrator = GenerationPipelineOrchestrator()

    first = orchestrator.run(
        question="hello",
        runtime=runtime,
        redis_service=redis_service,
        n_results_per_claim=5,
        should_cancel=lambda: True,
        active_stream_count=None,
        logger=_logger(),
    )
    second = orchestrator.run(
        question="hello",
        runtime=runtime,
        redis_service=redis_service,
        n_results_per_claim=5,
        should_cancel=lambda: True,
        active_stream_count=None,
        logger=_logger(),
    )

    metrics = snapshot_cache_metrics()
    assert first.success is False
    assert second.success is False
    assert metrics["stage1"].get("cache_hit", 0) == 0


def test_orchestrator_does_not_cache_stage1_result_when_cancel_flips_after_return():
    reset_cache_metrics()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = _Runtime(
        stage1_payload={"success": False, "deep_answer": "", "retrieval_claims": [], "metadata": {}},
        stage2_payload={"success": False, "error": "retrieval_failed"},
        doi_payload=[],
        stage25_payload={},
        stage3_payload={},
        stage4_payload=[],
    )
    orchestrator = GenerationPipelineOrchestrator()
    cancel_checks = {"count": 0}

    def _should_cancel() -> bool:
        cancel_checks["count"] += 1
        return cancel_checks["count"] >= 1

    first = orchestrator.run(
        question="hello",
        runtime=runtime,
        redis_service=redis_service,
        n_results_per_claim=5,
        should_cancel=_should_cancel,
        active_stream_count=None,
        logger=_logger(),
    )
    second = orchestrator.run(
        question="hello",
        runtime=runtime,
        redis_service=redis_service,
        n_results_per_claim=5,
        should_cancel=_should_cancel,
        active_stream_count=None,
        logger=_logger(),
    )

    metrics = snapshot_cache_metrics()
    assert first.success is False
    assert second.success is False
    assert metrics["stage1"].get("cache_hit", 0) == 0


def test_orchestrator_does_not_cache_stage2_result_when_cancel_flips_after_return():
    reset_cache_metrics()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": False, "error": "retrieval_failed", "metadata": {}},
        doi_payload=[],
        stage25_payload={},
        stage3_payload={},
        stage4_payload=[],
    )
    orchestrator = GenerationPipelineOrchestrator()
    cancel_checks = {"count": 0}

    def _should_cancel() -> bool:
        cancel_checks["count"] += 1
        return cancel_checks["count"] >= 2

    first = orchestrator.run(
        question="hello",
        runtime=runtime,
        redis_service=redis_service,
        n_results_per_claim=5,
        should_cancel=_should_cancel,
        active_stream_count=None,
        logger=_logger(),
    )
    second = orchestrator.run(
        question="hello",
        runtime=runtime,
        redis_service=redis_service,
        n_results_per_claim=5,
        should_cancel=_should_cancel,
        active_stream_count=None,
        logger=_logger(),
    )

    metrics = snapshot_cache_metrics()
    assert first.success is True
    assert second.success is True
    assert metrics["stage2"].get("cache_hit", 0) == 0


def test_orchestrator_stream_passes_conversation_context_to_stage4():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": True, "documents": ["doc"], "metadatas": [{"doi": "10.1"}], "distances": [0.1]},
        doi_payload=["10.1"],
        stage25_payload={"enabled": False, "applied": False, "md_chunks_by_doi": {}, "stats": {}},
        stage3_payload={"10.1": [{"text": "evidence"}]},
        stage4_payload=[],
    )
    captured: dict[str, object] = {}

    class _Stage4:
        def stream(
            self,
            *,
            runtime,
            user_question,
            deep_answer,
            pdf_chunks,
            retrieval_results=None,
            should_cancel=None,
            conversation_context=None,
        ):
            captured["runtime"] = runtime
            captured["user_question"] = user_question
            captured["deep_answer"] = deep_answer
            captured["pdf_chunks"] = pdf_chunks
            captured["retrieval_results"] = retrieval_results
            captured["should_cancel"] = should_cancel
            captured["conversation_context"] = conversation_context
            yield {"success": True, "final_answer": "final", "query_mode": "kb_qa", "references": []}

    orchestrator = GenerationPipelineOrchestrator(stage4=_Stage4())

    events = list(
        orchestrator.stream(
            question="hello",
            runtime=runtime,
            redis_service=None,
            n_results_per_claim=5,
            should_cancel=None,
            active_stream_count=None,
            logger=_logger(),
            sse_event=lambda payload: payload,
            conversation_context={
                "recent_turns_for_llm": [{"role": "assistant", "content": "prev"}],
                "summary_for_llm": {"short_summary": "sum"},
            },
        )
    )

    assert events[-1]["type"] == "done"
    assert captured["conversation_context"] == {
        "recent_turns_for_llm": [{"role": "assistant", "content": "prev"}],
        "summary_for_llm": {"short_summary": "sum"},
    }


def test_orchestrator_stream_emits_content_and_done():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": True, "documents": ["doc"], "metadatas": [{"doi": "10.1"}], "distances": [0.1]},
        doi_payload=["10.1"],
        stage25_payload={"enabled": False, "applied": False, "md_chunks_by_doi": {}, "stats": {}},
        stage3_payload={"10.1": [{"text": "evidence"}]},
        stage4_payload=["hel", "lo", {"success": True, "final_answer": "hello", "query_mode": "生成驱动检索（PDF溯源）", "references": [{"doi": "10.1"}]}],
    )
    orchestrator = GenerationPipelineOrchestrator()

    events = list(
        orchestrator.stream(
            question="hello",
            runtime=runtime,
            redis_service=None,
            n_results_per_claim=5,
            should_cancel=None,
            active_stream_count=None,
            logger=_logger(),
            sse_event=lambda payload: payload,
        )
    )

    assert any(event.get("type") == "metadata" for event in events)
    assert [event["content"] for event in events if event.get("type") == "content"] == ["hel", "lo"]
    assert events[-1]["type"] == "done"
    assert events[-1]["final_answer"] == "hello"


def test_orchestrator_stream_logs_stage3_handoff_merge_and_rerank_counts(monkeypatch, caplog):
    monkeypatch.setenv("QA_STAGE3_DIAGNOSTIC_LOG", "1")
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={
            "success": True,
            "documents": ["doc"],
            "metadatas": [{"doi": "10.1/a"}],
            "distances": [0.1],
            "unique_count": 1,
            "total_count": 1,
        },
        doi_payload=["10.1/a"],
        stage25_payload={"enabled": False, "applied": False, "md_chunks_by_doi": {}, "stats": {}},
        stage3_payload={"10.1/a": [{"text": "stage3 evidence"}]},
        stage4_payload=[{"success": True, "final_answer": "final", "references": [{"doi": "10.1/a"}]}],
    )

    def _merge(**kwargs):
        chunks = dict(kwargs.get("pdf_chunks") or {})
        chunks.setdefault("10.1/a", []).append({"text": "stage2 evidence", "source": "stage2_retrieval"})
        return chunks

    def _rerank(**kwargs):
        return {
            "pdf_chunks": dict(kwargs["pdf_chunks"]),
            "stats": {"enabled": True, "before_chunk_count": 2, "after_chunk_count": 2},
        }

    logger = logging.getLogger("test.fastqa.orchestrator.stream.stage3")
    orchestrator = GenerationPipelineOrchestrator(
        merge_stage2_retrieval_evidence_fn=_merge,
        evidence_rerank_fn=_rerank,
    )

    with caplog.at_level(logging.INFO, logger="test.fastqa.orchestrator.stream.stage3"):
        events = list(
            orchestrator.stream(
                question="hello",
                runtime=runtime,
                redis_service=None,
                n_results_per_claim=5,
                should_cancel=None,
                active_stream_count=None,
                logger=logger,
                sse_event=lambda payload: payload,
            )
        )

    assert events[-1]["type"] == "done"
    messages = [record.message for record in caplog.records if record.name == "test.fastqa.orchestrator.stream.stage3"]
    assert any(
        "fastqa stream stage3 handoff" in message
        and "doi_count=1" in message
        and "doi_source=retrieval" in message
        for message in messages
    )
    assert any(
        "fastqa stream stage3 evidence merge completed" in message
        and "before_chunks=1" in message
        and "after_chunks=2" in message
        for message in messages
    )
    assert any(
        "fastqa stream stage35 completed" in message
        and "before_chunk_count" in message
        and "pdf_chunk_count=2" in message
        for message in messages
    )


def test_orchestrator_passes_graph_evidence_to_stage1_and_stage4():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": True, "documents": ["doc"], "metadatas": [{"doi": "10.1"}], "distances": [0.1]},
        doi_payload=["10.1"],
        stage25_payload={"enabled": False, "applied": False, "md_chunks_by_doi": {}, "stats": {}},
        stage3_payload={"10.1": [{"text": "evidence"}]},
        stage4_payload=[{"success": True, "final_answer": "final", "query_mode": "kb_qa", "references": []}],
    )
    captured: dict[str, object] = {}

    class _Stage1:
        def run(self, *, runtime, user_question, conversation_context=None, graph_evidence=None):
            captured["stage1_graph_evidence"] = graph_evidence
            return {"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]}

    class _Stage4:
        def stream(self, *, runtime, user_question, deep_answer, pdf_chunks, retrieval_results=None, should_cancel=None, conversation_context=None, graph_evidence=None):
            captured["stage4_graph_evidence"] = graph_evidence
            yield {"success": True, "final_answer": "final", "query_mode": "kb_qa", "references": []}

    orchestrator = GenerationPipelineOrchestrator(stage1=_Stage1(), stage4=_Stage4())
    payload = GraphRagPayload(stage1_context_block="doi:10.1000/test", stage4_fact_block="structured graph facts", cache_fingerprint="graph:abc")

    result = orchestrator.run(
        question="hello",
        runtime=runtime,
        redis_service=None,
        n_results_per_claim=5,
        should_cancel=None,
        active_stream_count=None,
        logger=_logger(),
        graph_evidence=payload,
    )

    assert result.success is True
    assert captured["stage1_graph_evidence"] is payload
    assert captured["stage4_graph_evidence"] is payload


def test_orchestrator_uses_graph_seeded_doi_fallback_when_stage2_has_no_doi():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": True, "documents": [], "metadatas": [], "distances": []},
        doi_payload=[],
        stage25_payload={"enabled": False, "applied": False, "md_chunks_by_doi": {}, "stats": {}},
        stage3_payload={"10.1000/test": [{"text": "evidence"}]},
        stage4_payload=[{"success": True, "final_answer": "final", "query_mode": "kb_qa", "references": [{"doi": "10.1000/test"}]}],
    )
    orchestrator = GenerationPipelineOrchestrator()

    result = orchestrator.run(
        question="hello",
        runtime=runtime,
        redis_service=None,
        n_results_per_claim=5,
        should_cancel=None,
        active_stream_count=None,
        logger=_logger(),
        graph_evidence=GraphRagPayload(
            stage2_doi_candidates=("10.1000/test",),
            cache_fingerprint="graph:abc",
        ),
    )

    assert result.success is True
    assert result.metadata.doi_count == 1
    assert result.raw["doi_source"] == "graph_seeded"
    assert result.raw["dois"] == ["10.1000/test"]


def test_orchestrator_graph_seeded_doi_can_use_md_only_source_evidence():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": True, "documents": [], "metadatas": [], "distances": []},
        doi_payload=[],
        stage25_payload={
            "enabled": True,
            "applied": True,
            "md_chunks_by_doi": {"10.1000/test": [{"doi": "10.1000/test", "text": "md evidence"}]},
            "stats": {"hit_doi_count": 1, "total_md_chunks": 1},
        },
        stage3_payload={},
        stage4_payload=[{"success": True, "final_answer": "final", "references": [{"doi": "10.1000/test"}]}],
    )
    orchestrator = GenerationPipelineOrchestrator(
        evaluate_stage3_pdf_skip_fn=lambda **_kwargs: {
            "should_skip": True,
            "reason": "md_evidence_threshold",
            "hit_doi_count": 1,
            "total_md_chunks": 1,
        }
    )

    result = orchestrator.run(
        question="hello",
        runtime=runtime,
        redis_service=None,
        n_results_per_claim=5,
        should_cancel=None,
        active_stream_count=None,
        logger=_logger(),
        graph_evidence=GraphRagPayload(stage2_doi_candidates=("10.1000/test",), cache_fingerprint="graph:abc"),
    )

    assert result.success is True
    assert result.raw["doi_source"] == "graph_seeded"
    assert result.raw["pdf_chunks"] == {"10.1000/test": [{"doi": "10.1000/test", "text": "md evidence"}]}
    assert result.metadata.stage3_pdf_skipped is True
    assert result.metadata.query_mode == "生成驱动检索（MD直读）"


def test_orchestrator_model_identity_shortcut_matches_legacy_copy():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": True},
        doi_payload=[],
        stage25_payload={},
        stage3_payload={},
        stage4_payload=[],
    )
    orchestrator = GenerationPipelineOrchestrator()

    result = orchestrator.run(
        question="你是什么模型",
        runtime=runtime,
        redis_service=None,
        n_results_per_claim=5,
        should_cancel=None,
        active_stream_count=None,
        logger=_logger(),
    )

    assert result.success is True
    assert "claude-4.5-sonnet-thinking" in result.final_answer



def test_orchestrator_stream_emits_legacy_stage_copy_for_pdf_path():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": True, "documents": ["doc"], "metadatas": [{"doi": "10.1"}], "distances": [0.1]},
        doi_payload=["10.1"],
        stage25_payload={"enabled": True, "applied": False, "md_chunks_by_doi": {}, "stats": {}},
        stage3_payload={"10.1": [{"text": "evidence"}]},
        stage4_payload=[{"success": True, "final_answer": "final", "query_mode": "生成驱动检索（PDF溯源）", "references": [{"doi": "10.1"}]}],
    )
    orchestrator = GenerationPipelineOrchestrator()

    events = list(
        orchestrator.stream(
            question="hello",
            runtime=runtime,
            redis_service=None,
            n_results_per_claim=5,
            should_cancel=None,
            active_stream_count=None,
            logger=_logger(),
            sse_event=lambda payload: payload,
        )
    )

    thinking_events = [event["content"] for event in events if event.get("type") == "thinking"]
    assert thinking_events == [
        "📝 阶段一：生成深度预回答与检索规划...",
        "🔍 阶段二：检索高匹配度DOI...",
        "🧩 阶段二点五：尝试MD原文扩展检索...",
        "📄 阶段三：加载 1 个文献的原文（提取 top 3 个最相关chunk）...",
        "🔎 阶段3.5：重排候选证据chunk...",
        "✍️ 阶段四：综合预回答与原文chunk生成答案...",
    ]


def test_orchestrator_stream_emits_md_hit_and_pdf_skip_copy():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": True, "documents": ["doc"], "metadatas": [{"doi": "10.1"}], "distances": [0.1]},
        doi_payload=["10.1"],
        stage25_payload={
            "enabled": True,
            "applied": True,
            "md_chunks_by_doi": {"10.1": [{"text": "md evidence"}]},
            "stats": {"hit_doi_count": 1, "total_md_chunks": 1},
        },
        stage3_payload={},
        stage4_payload=[{"success": True, "final_answer": "final", "query_mode": "生成驱动检索（PDF溯源）", "references": [{"doi": "10.1"}]}],
    )
    orchestrator = GenerationPipelineOrchestrator(
        evaluate_stage3_pdf_skip_fn=lambda **_kwargs: {
            "should_skip": True,
            "reason": "md_evidence_threshold",
            "hit_doi_count": 1,
            "total_md_chunks": 1,
        }
    )

    events = list(
        orchestrator.stream(
            question="hello",
            runtime=runtime,
            redis_service=None,
            n_results_per_claim=5,
            should_cancel=None,
            active_stream_count=None,
            logger=_logger(),
            sse_event=lambda payload: payload,
        )
    )

    thinking_events = [event["content"] for event in events if event.get("type") == "thinking"]
    assert "🧩 阶段二点五命中：1 个DOI，1 个MD片段" in thinking_events
    assert "📄 阶段三：MD证据命中阈值，跳过PDF溯源...（hit_doi=1, md_chunks=1）" in thinking_events


def test_orchestrator_run_reuses_cached_stage25_and_stage3_results(monkeypatch):
    monkeypatch.setenv("QA_PIPELINE_CACHE_ENABLED", "1")
    reset_cache_metrics()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": True, "documents": ["doc"], "metadatas": [{"doi": "10.1"}], "distances": [0.1]},
        doi_payload=["10.1"],
        stage25_payload={},
        stage3_payload={},
        stage4_payload=[{"success": True, "final_answer": "final", "query_mode": "生成驱动检索（PDF溯源）", "references": [{"doi": "10.1"}]}],
    )
    stage25 = _CountingStage25(
        {
            "enabled": True,
            "applied": True,
            "md_chunks_by_doi": {"10.1": [{"text": "md evidence"}]},
            "stats": {"hit_doi_count": 1, "total_md_chunks": 1, "fallback_reason": ""},
        }
    )
    stage3 = _CountingStage3({"10.1": [{"text": "pdf evidence"}]})
    orchestrator = GenerationPipelineOrchestrator(stage25=stage25, stage3=stage3)

    first = orchestrator.run(
        question="hello",
        runtime=runtime,
        redis_service=redis_service,
        n_results_per_claim=5,
        should_cancel=None,
        active_stream_count=None,
        logger=_logger(),
    )
    second = orchestrator.run(
        question="hello",
        runtime=runtime,
        redis_service=redis_service,
        n_results_per_claim=5,
        should_cancel=None,
        active_stream_count=None,
        logger=_logger(),
    )

    metrics = snapshot_cache_metrics()
    assert first.success is True
    assert second.success is True
    assert stage25.calls == 1
    assert stage3.calls == 1
    assert metrics["stage25"]["lock_acquired"] == 1
    assert metrics["stage25"]["cache_hit"] == 1
    assert metrics["stage3"]["lock_acquired"] == 1
    assert metrics["stage3"]["cache_hit"] == 1


def test_orchestrator_stream_reuses_cached_stage25_and_stage3_results(monkeypatch):
    monkeypatch.setenv("QA_PIPELINE_CACHE_ENABLED", "1")
    reset_cache_metrics()
    redis_service = RedisService.from_prefix(client=_FakeRedis(), key_prefix="agentcode")
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": True, "documents": ["doc"], "metadatas": [{"doi": "10.1"}], "distances": [0.1]},
        doi_payload=["10.1"],
        stage25_payload={},
        stage3_payload={},
        stage4_payload=[{"success": True, "final_answer": "final", "query_mode": "生成驱动检索（PDF溯源）", "references": [{"doi": "10.1"}]}],
    )
    stage25 = _CountingStage25(
        {
            "enabled": True,
            "applied": False,
            "md_chunks_by_doi": {},
            "stats": {"hit_doi_count": 0, "total_md_chunks": 0, "fallback_reason": ""},
        }
    )
    stage3 = _CountingStage3({"10.1": [{"text": "pdf evidence"}]})
    orchestrator = GenerationPipelineOrchestrator(stage25=stage25, stage3=stage3)

    first = list(
        orchestrator.stream(
            question="hello",
            runtime=runtime,
            redis_service=redis_service,
            n_results_per_claim=5,
            should_cancel=None,
            active_stream_count=None,
            logger=_logger(),
            sse_event=lambda payload: payload,
        )
    )
    second = list(
        orchestrator.stream(
            question="hello",
            runtime=runtime,
            redis_service=redis_service,
            n_results_per_claim=5,
            should_cancel=None,
            active_stream_count=None,
            logger=_logger(),
            sse_event=lambda payload: payload,
        )
    )

    metrics = snapshot_cache_metrics()
    assert first[-1]["type"] == "done"
    assert second[-1]["type"] == "done"
    assert stage25.calls == 1
    assert stage3.calls == 1
    assert metrics["stage25"]["cache_hit"] >= 1
    assert metrics["stage3"]["cache_hit"] >= 1


def test_orchestrator_stream_metadata_does_not_hardcode_query_mode_before_final_result():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": True, "documents": ["doc"], "metadatas": [{"doi": "10.1"}], "distances": [0.1]},
        doi_payload=["10.1"],
        stage25_payload={"enabled": False, "applied": False, "md_chunks_by_doi": {}, "stats": {}},
        stage3_payload={"10.1": [{"text": "evidence"}]},
        stage4_payload=[{"success": True, "final_answer": "final", "query_mode": "自定义查询模式", "references": [{"doi": "10.1"}]}],
    )
    orchestrator = GenerationPipelineOrchestrator()

    events = list(
        orchestrator.stream(
            question="hello",
            runtime=runtime,
            redis_service=None,
            n_results_per_claim=5,
            should_cancel=None,
            active_stream_count=None,
            logger=_logger(),
            sse_event=lambda payload: payload,
        )
    )

    metadata_event = next(event for event in events if event.get("type") == "metadata")
    done_event = events[-1]
    assert "query_mode" not in metadata_event
    assert done_event["query_mode"] == "自定义查询模式"


def test_orchestrator_stream_passes_conversation_context_to_stage1():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": []},
        stage2_payload={"success": False, "error": "retrieval_failed"},
        doi_payload=[],
        stage25_payload={},
        stage3_payload={},
        stage4_payload=[],
    )
    captured: dict[str, object] = {}

    class _Stage1:
        def run(self, *, runtime, user_question, conversation_context=None):
            captured["runtime"] = runtime
            captured["user_question"] = user_question
            captured["conversation_context"] = conversation_context
            return {"success": True, "deep_answer": "deep", "retrieval_claims": []}

    orchestrator = GenerationPipelineOrchestrator(stage1=_Stage1())

    events = list(
        orchestrator.stream(
            question="hello",
            runtime=runtime,
            redis_service=None,
            n_results_per_claim=5,
            should_cancel=None,
            active_stream_count=None,
            logger=_logger(),
            sse_event=lambda payload: payload,
            conversation_context={
                "recent_turns_for_llm": [{"role": "assistant", "content": "prev"}],
                "summary_for_llm": {"short_summary": "sum"},
            },
        )
    )

    assert events[-1]["type"] == "done"
    assert captured["conversation_context"] == {
        "recent_turns_for_llm": [{"role": "assistant", "content": "prev"}],
        "summary_for_llm": {"short_summary": "sum"},
    }


def test_orchestrator_uses_md_query_mode_when_stage4_omits_query_mode():
    runtime = _Runtime(
        stage1_payload={"success": True, "deep_answer": "deep", "retrieval_claims": [{"claim": "x"}]},
        stage2_payload={"success": True, "documents": ["doc"], "metadatas": [{"doi": "10.1"}], "distances": [0.1]},
        doi_payload=["10.1"],
        stage25_payload={
            "enabled": True,
            "applied": True,
            "md_chunks_by_doi": {"10.1": [{"text": "md evidence"}]},
            "stats": {"hit_doi_count": 1, "total_md_chunks": 1},
        },
        stage3_payload={},
        stage4_payload=[{"success": True, "final_answer": "final", "references": [{"doi": "10.1"}]}],
    )
    orchestrator = GenerationPipelineOrchestrator(
        evaluate_stage3_pdf_skip_fn=lambda **_kwargs: {
            "should_skip": True,
            "reason": "md_evidence_threshold",
            "hit_doi_count": 1,
            "total_md_chunks": 1,
        }
    )

    run_result = orchestrator.run(
        question="hello",
        runtime=runtime,
        redis_service=None,
        n_results_per_claim=5,
        should_cancel=None,
        active_stream_count=None,
        logger=_logger(),
    )
    stream_events = list(
        orchestrator.stream(
            question="hello",
            runtime=runtime,
            redis_service=None,
            n_results_per_claim=5,
            should_cancel=None,
            active_stream_count=None,
            logger=_logger(),
            sse_event=lambda payload: payload,
        )
    )

    assert run_result.metadata.query_mode == "生成驱动检索（MD直读）"
    assert stream_events[-1]["query_mode"] == "生成驱动检索（MD直读）"
