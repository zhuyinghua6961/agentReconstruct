from __future__ import annotations

import logging
from dataclasses import dataclass

from app.integrations.redis import RedisService
from app.modules.qa_cache import reset_cache_metrics, snapshot_cache_metrics
from app.modules.qa_kb.orchestrators.generation import GenerationPipelineOrchestrator


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

    def _get_vector_db_context_for_prompt(self) -> str:
        return "context"

    def stage1_pre_answer_and_planning(self, user_question: str) -> dict:
        return dict(self.stage1_payload)

    def stage2_targeted_retrieval(self, retrieval_claims, n_results_per_claim=10, user_question=None, should_cancel=None, active_stream_count=None) -> dict:
        return dict(self.stage2_payload)

    def stage25_md_expansion(self, *, retrieval_results: dict, user_question: str, dois: list[str]) -> dict:
        return dict(self.stage25_payload)

    def _extract_dois_from_results(self, retrieval_results: dict) -> list[str]:
        return list(self.doi_payload)

    def stage3_load_pdf_chunks(self, dois, max_chunks_per_doi=3, should_cancel=None):
        return {key: list(value) for key, value in self.stage3_payload.items()}

    def stage4_synthesis_with_pdf_chunks(self, user_question, deep_answer, pdf_chunks, retrieval_results=None, should_cancel=None):
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
    assert result.final_answer == "final"
    assert result.metadata.doi_count == 1
    assert result.metadata.chunk_count == 1
    assert result.metadata.source_count == 1


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
        "📄 阶段三：加载 1 个文献的原文（提取 top 8 个最相关chunk）...",
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


def test_orchestrator_run_reuses_cached_stage25_and_stage3_results():
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


def test_orchestrator_stream_reuses_cached_stage25_and_stage3_results():
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
