"""Phase 1 qa_kb models and streaming helpers for fastQA."""

from app.modules.qa_kb.models import (
    GenerationRuntime,
    QaKbExecutionMetadata,
    QaKbExecutionResult,
    QaKbLegacyDependencies,
    QaKbPipelineMode,
    QaKbRequest,
)
from app.modules.qa_kb.streaming import iter_result_events, iter_text_chunks

__all__ = [
    "GenerationRuntime",
    "QaKbExecutionMetadata",
    "QaKbExecutionResult",
    "QaKbLegacyDependencies",
    "QaKbPipelineMode",
    "QaKbRequest",
    "iter_result_events",
    "iter_text_chunks",
]
from app.modules.qa_kb.service import qa_kb_service

__all__ = ["qa_kb_service"]
