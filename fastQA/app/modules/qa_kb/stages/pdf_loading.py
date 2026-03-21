from __future__ import annotations

from typing import Any

from app.modules.qa_kb.models import GenerationRuntime


class Stage3PdfLoader:
    def run(
        self,
        *,
        runtime: GenerationRuntime,
        dois: list[str],
        max_chunks_per_doi: int = 3,
        should_cancel: Any | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        return runtime.stage3_load_pdf_chunks(
            dois=dois,
            max_chunks_per_doi=max_chunks_per_doi,
            should_cancel=should_cancel,
        )
