from __future__ import annotations

import inspect
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
        graph_evidence=None,
    ) -> dict[str, Any]:
        stage2_fn = runtime.stage2_targeted_retrieval
        try:
            signature = inspect.signature(stage2_fn)
        except (TypeError, ValueError):
            signature = None

        kwargs = {
            "retrieval_claims": retrieval_claims,
            "n_results_per_claim": n_results_per_claim,
            "user_question": user_question,
            "should_cancel": should_cancel,
            "active_stream_count": active_stream_count,
        }
        if signature is not None:
            parameters = signature.parameters
            supports_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
            if "graph_evidence" in parameters or supports_kwargs:
                kwargs["graph_evidence"] = graph_evidence
        return stage2_fn(**kwargs)


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
