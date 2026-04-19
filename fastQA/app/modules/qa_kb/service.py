from __future__ import annotations

import logging
import os
from typing import Any, Callable, Iterator

from app.integrations.redis import RedisService
from app.modules.qa_kb.md_expansion import evaluate_stage3_pdf_skip, merge_pdf_chunks_with_md
from app.modules.qa_kb.models import GenerationRuntime, QaKbExecutionResult, QaKbPipelineMode, QaKbRequest
from app.modules.qa_kb.orchestrators.generation import GenerationPipelineOrchestrator
from app.modules.qa_kb.streaming import iter_result_events
from app.services.conversation_context_builder import normalize_conversation_context
from app.services.stream_contract import normalize_stream_event


class QaKbService:
    def __init__(self) -> None:
        self._logger = logging.getLogger(__name__)
        self._generation_orchestrator = GenerationPipelineOrchestrator(
            evaluate_stage3_pdf_skip_fn=evaluate_stage3_pdf_skip,
            merge_pdf_chunks_with_md_fn=merge_pdf_chunks_with_md,
        )

    def iter_phase1_placeholder_events(self, *, request: QaKbRequest) -> Iterator[dict[str, Any]]:
        yield {
            "type": "metadata",
            "query_mode": request.route_hint,
            "requested_mode": "fast",
            "actual_mode": "fast",
            "trace_id": request.trace_id,
        }
        yield normalize_stream_event(
            {
                "type": "thinking",
                "message": "阶段1：请求已接入 fastQA 流式骨架",
                "trace_id": request.trace_id,
            }
        )
        yield {
            "type": "error",
            "code": "FASTQA_NOT_READY",
            "error": "fastQA 暂未接入真实执行闭包",
            "message": "fastQA execution closure has not been extracted yet",
            "trace_id": request.trace_id,
        }
        yield {
            "type": "done",
            "references": [],
            "route": request.route_hint,
            "used_files": [],
            "timings": {},
            "trace_id": request.trace_id,
            "file_selection": {},
        }

    def resolve_pipeline_mode(
        self,
        *,
        request_use_generation_driven: bool,
        env_get: Callable[[str, str], str] | None = None,
        logger: Any | None = None,
    ) -> QaKbPipelineMode:
        lookup = env_get or os.getenv
        log = logger or self._logger
        raw_mode = str(lookup("QA_QUERY_PIPELINE_MODE", "new") or "new").strip().lower()
        aliases = {
            "new": "new",
            "generation": "new",
            "generation_driven": "new",
            "legacy": "legacy",
            "old": "legacy",
            "semantic": "legacy",
            "request": "request",
            "client": "request",
        }
        mode = aliases.get(raw_mode)
        if mode is None:
            log.warning("Unknown QA_QUERY_PIPELINE_MODE=%r, falling back to new", raw_mode)
            mode = "new"
        if mode == "request":
            return QaKbPipelineMode(mode=mode, use_generation_driven=bool(request_use_generation_driven))
        return QaKbPipelineMode(mode=mode, use_generation_driven=(mode == "new"))

    def run_generation_pipeline(
        self,
        *,
        question: str,
        generation_runtime: GenerationRuntime,
        redis_service: RedisService | None = None,
        n_results_per_claim: int = 10,
        should_cancel: Callable[[], bool] | None = None,
        active_stream_count: int | None = None,
        logger: Any | None = None,
        conversation_context: dict[str, Any] | None = None,
        graph_evidence=None,
    ) -> QaKbExecutionResult:
        return self._generation_orchestrator.run(
            question=question,
            runtime=generation_runtime,
            redis_service=redis_service,
            n_results_per_claim=n_results_per_claim,
            should_cancel=should_cancel,
            active_stream_count=active_stream_count,
            logger=logger or self._logger,
            conversation_context=conversation_context,
            graph_evidence=graph_evidence,
        )

    def iter_generation_answer_events(
        self,
        *,
        question: str,
        generation_runtime: GenerationRuntime,
        redis_service: RedisService | None = None,
        sse_event: Callable[[dict[str, Any]], Any],
        n_results_per_claim: int = 10,
        should_cancel: Callable[[], bool] | None = None,
        active_stream_count: int | None = None,
        logger: Any | None = None,
        chunk_size: int = 120,
        conversation_context: dict[str, Any] | None = None,
        graph_evidence=None,
    ) -> Iterator[Any]:
        yield from self._generation_orchestrator.stream(
            question=question,
            runtime=generation_runtime,
            redis_service=redis_service,
            n_results_per_claim=n_results_per_claim,
            should_cancel=should_cancel,
            active_stream_count=active_stream_count,
            logger=logger or self._logger,
            sse_event=sse_event,
            chunk_size=chunk_size,
            conversation_context=conversation_context,
            graph_evidence=graph_evidence,
        )

    def iter_answer_events(
        self,
        *,
        request: QaKbRequest,
        sse_event: Callable[[dict[str, Any]], Any],
        generation_runtime: GenerationRuntime,
        redis_service: RedisService | None = None,
        should_cancel: Callable[[], bool] | None = None,
        env_get: Callable[[str, str], str] | None = None,
        logger: Any | None = None,
        chunk_size: int = 120,
        conversation_context: dict[str, Any] | None = None,
    ) -> Iterator[Any]:
        log = logger or self._logger
        resolved_mode = self.resolve_pipeline_mode(
            request_use_generation_driven=request.request_use_generation_driven,
            env_get=env_get,
            logger=log,
        )

        def _emit(payload: dict[str, Any]) -> Any:
            return sse_event(normalize_stream_event(dict(payload or {})))

        conversation_context = normalize_conversation_context(
            recent_turns_for_llm=request.recent_turns_for_llm,
            summary_for_llm=request.summary_for_llm,
            conversation_state=request.conversation_state,
            source_selection=request.source_selection,
        )

        if not resolved_mode.use_generation_driven:
            yield _emit(
                {
                    "type": "error",
                    "code": "FASTQA_PIPELINE_MODE_UNSUPPORTED",
                    "error": f"fastQA does not support pipeline_mode={resolved_mode.mode}",
                    "message": "fastQA 当前仅支持 generation-driven 普通知识库问答",
                    "trace_id": request.trace_id,
                }
            )
            return

        yield from self.iter_generation_answer_events(
            question=request.question,
            generation_runtime=generation_runtime,
            redis_service=redis_service,
            sse_event=_emit,
            n_results_per_claim=request.n_results_per_claim,
            should_cancel=should_cancel,
            active_stream_count=request.active_stream_count,
            logger=log,
            chunk_size=chunk_size,
            conversation_context=conversation_context,
            graph_evidence=request.graph_evidence,
        )

    def iter_result_events(
        self,
        *,
        result: QaKbExecutionResult,
        sse_event: Callable[[dict[str, Any]], Any],
        chunk_size: int = 120,
    ) -> Iterator[Any]:
        yield from iter_result_events(result=result, sse_event=sse_event, chunk_size=chunk_size)


qa_kb_service = QaKbService()
