from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.modules.graph_kb.models import GraphRagPayload


class GenerationRuntime(Protocol):
    def stage1_pre_answer_and_planning(
        self,
        user_question: str,
        conversation_context: dict[str, Any] | None = None,
        graph_context: str | None = None,
    ) -> dict[str, Any]:
        ...

    def stage2_targeted_retrieval(
        self,
        retrieval_claims: list[dict[str, Any]],
        n_results_per_claim: int = 10,
        user_question: str | None = None,
        should_cancel: Any | None = None,
        active_stream_count: int | None = None,
        graph_evidence: GraphRagPayload | None = None,
    ) -> dict[str, Any]:
        ...

    def stage25_md_expansion(
        self,
        *,
        retrieval_results: dict[str, Any],
        user_question: str,
        dois: list[str],
    ) -> dict[str, Any]:
        ...

    def _extract_dois_from_results(self, retrieval_results: dict[str, Any]) -> list[str]:
        ...

    def stage3_load_pdf_chunks(
        self,
        dois: list[str],
        max_chunks_per_doi: int = 3,
        should_cancel: Any | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        ...

    def stage4_synthesis_with_pdf_chunks(
        self,
        user_question: str,
        deep_answer: str,
        pdf_chunks: dict[str, list[dict[str, Any]]],
        retrieval_results: dict[str, Any] | None = None,
        should_cancel: Any | None = None,
        conversation_context: dict[str, Any] | None = None,
        graph_fact_block: str = "",
    ) -> Any:
        ...


@dataclass(frozen=True)
class QaKbPipelineMode:
    mode: str
    use_generation_driven: bool


@dataclass(frozen=True)
class QaKbRequest:
    question: str
    request_use_generation_driven: bool = False
    route_hint: str = "kb_qa"
    n_results_per_claim: int = 10
    active_stream_count: int | None = None
    trace_id: str = ""
    recent_turns_for_llm: list[dict[str, Any]] = field(default_factory=list)
    summary_for_llm: dict[str, Any] = field(default_factory=dict)
    conversation_state: dict[str, Any] = field(default_factory=dict)
    source_selection: dict[str, Any] = field(default_factory=dict)
    graph_evidence: GraphRagPayload | None = None


@dataclass
class QaKbExecutionMetadata:
    route: str = "kb_qa"
    pipeline_mode: str = "new"
    query_mode: str = ""
    use_generation_driven: bool = True
    doi_source: str = "none"
    doi_count: int = 0
    chunk_count: int = 0
    source_count: int = 0
    stage3_pdf_skipped: bool = False
    stage3_pdf_skip_reason: str = ""
    stage_timings_ms: dict[str, float] = field(default_factory=dict)


@dataclass
class QaKbExecutionResult:
    success: bool
    final_answer: str
    metadata: QaKbExecutionMetadata
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class QaKbLegacyDependencies:
    agent: Any
    performance_mode: str = "speed"
    sleep_fn: Any | None = None
    clean_answer_for_frontend: Any | None = None
    filter_literature_markers_for_streaming: Any | None = None
    collect_answer_references: Any | None = None
    validate_and_fix_doi: Any | None = None
    verify_doi_in_database: Any | None = None
    verify_context: Any | None = None
    log_qa_interaction: Any | None = None
