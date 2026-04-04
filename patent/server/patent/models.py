from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


PatentProgressCallback = Any


class PatentGenerationRuntime(Protocol):
    stage25_is_noop: bool
    stage25_skip_reason: str
    stage3_force_pdf: bool

    def stage1_pre_answer_and_planning(
        self,
        user_question: str,
        conversation_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def stage2_targeted_retrieval(
        self,
        retrieval_claims: list["PatentRetrievalClaim"],
        *,
        user_question: str,
        should_cancel: Any | None = None,
        active_stream_count: int | None = None,
    ) -> dict[str, Any]:
        ...

    def stage25_patent_evidence_expansion(
        self,
        *,
        retrieval_results: dict[str, Any],
        user_question: str,
        source_ids: list[str],
    ) -> dict[str, Any]:
        ...

    def _extract_patent_ids_from_results(self, retrieval_results: dict[str, Any]) -> list[str]:
        ...

    def stage3_load_patent_evidence(
        self,
        *,
        retrieval_results: dict[str, Any],
        source_ids: list[str],
        should_cancel: Any | None = None,
    ) -> dict[str, Any]:
        ...

    def stage4_synthesis_with_patent_evidence(
        self,
        *,
        user_question: str,
        deep_answer: str,
        patent_evidence_bundle: dict[str, Any],
        retrieval_results: dict[str, Any] | None = None,
        should_cancel: Any | None = None,
        conversation_context: dict[str, Any] | None = None,
    ) -> Any:
        ...


@dataclass(frozen=True)
class PatentRetrievalPlan:
    question_type: str = ""
    analysis_axes: list[str] = field(default_factory=list)
    explicit_patent_ids: list[str] = field(default_factory=list)
    candidate_recall_queries: list[str] = field(default_factory=list)
    evidence_localization_queries: list[str] = field(default_factory=list)
    preferred_sections: list[str] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PatentRetrievalClaim:
    claim: str = ""
    keywords: list[str] = field(default_factory=list)
    preferred_sections: list[str] = field(default_factory=list)
    filters: dict[str, Any] = field(default_factory=dict)


@dataclass
class PatentQaExecutionMetadata:
    route: str = "kb_qa"
    query_mode: str = ""
    source_ids: list[str] = field(default_factory=list)
    stage_timings_ms: dict[str, float] = field(default_factory=dict)
    stage1_short_circuit: bool = False
    stage25_skipped: bool = False
    stage25_skip_reason: str = ""


@dataclass
class PatentQaExecutionResult:
    success: bool
    final_answer: str
    metadata: PatentQaExecutionMetadata = field(default_factory=PatentQaExecutionMetadata)
    raw: dict[str, Any] = field(default_factory=dict)
