from __future__ import annotations

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
        should_cancel: Any | None = None,
    ) -> Any:
        return runtime.stage4_synthesis_with_pdf_chunks(
            user_question=user_question,
            deep_answer=deep_answer,
            pdf_chunks=pdf_chunks,
            retrieval_results=retrieval_results,
            should_cancel=should_cancel,
        )
