from __future__ import annotations

from typing import Any

from app.modules.qa_kb.models import GenerationRuntime


class Stage2Retriever:
    def run(
        self,
        *,
        runtime: GenerationRuntime,
        retrieval_claims: list[dict[str, Any]],
        n_results_per_claim: int,
        user_question: str,
        should_cancel: Any | None = None,
        active_stream_count: int | None = None,
    ) -> dict[str, Any]:
        return runtime.stage2_targeted_retrieval(
            retrieval_claims=retrieval_claims,
            n_results_per_claim=n_results_per_claim,
            user_question=user_question,
            should_cancel=should_cancel,
            active_stream_count=active_stream_count,
        )


class Stage25MdExpansion:
    def run(
        self,
        *,
        runtime: GenerationRuntime,
        retrieval_results: dict[str, Any],
        user_question: str,
        dois: list[str],
    ) -> dict[str, Any]:
        return runtime.stage25_md_expansion(
            retrieval_results=retrieval_results,
            user_question=user_question,
            dois=dois,
        )
