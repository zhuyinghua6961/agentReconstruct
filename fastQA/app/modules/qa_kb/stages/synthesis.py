from __future__ import annotations

import inspect
from typing import Any

from app.modules.qa_kb.models import GenerationRuntime


class Stage4Synthesizer:
    def stream(
        self,
        *,
        runtime: GenerationRuntime,
        user_question: str,
        deep_answer: str,
        pdf_chunks: dict[str, list[dict[str, Any]]],
        retrieval_results: dict[str, Any] | None = None,
        answer_plan: dict[str, Any] | None = None,
        should_cancel: Any | None = None,
        conversation_context: dict[str, Any] | None = None,
        graph_evidence=None,
    ) -> Any:
        stage4_fn = runtime.stage4_synthesis_with_pdf_chunks
        try:
            signature = inspect.signature(stage4_fn)
        except (TypeError, ValueError):
            signature = None

        kwargs = {
            "user_question": user_question,
            "deep_answer": deep_answer,
            "pdf_chunks": pdf_chunks,
            "retrieval_results": retrieval_results,
            "should_cancel": should_cancel,
            "conversation_context": conversation_context,
        }
        if signature is not None:
            parameters = signature.parameters
            supports_kwargs = any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
            if "answer_plan" in parameters or supports_kwargs:
                kwargs["answer_plan"] = answer_plan
            if "graph_fact_block" in parameters or supports_kwargs:
                kwargs["graph_fact_block"] = (
                    getattr(graph_evidence, "stage4_fact_block", "") if graph_evidence is not None else ""
                )
        return stage4_fn(**kwargs)
