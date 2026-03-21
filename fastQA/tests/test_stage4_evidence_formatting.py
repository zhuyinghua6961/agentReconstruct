from __future__ import annotations

from types import SimpleNamespace

from app.modules.generation_pipeline.reference_alignment import format_pdf_chunks_evidence as format_reference_evidence
from app.modules.generation_pipeline.synthesis_streaming import format_pdf_chunks_evidence as format_streaming_evidence


class _Logger(SimpleNamespace):
    def __init__(self):
        super().__init__(debug=lambda *args, **kwargs: None)


def test_stage4_evidence_formatting_matches_legacy_limits(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_EVIDENCE_CHUNKS_PER_DOI", "2")
    monkeypatch.setenv("QA_STAGE4_EVIDENCE_CHUNK_MAX_CHARS", "250")

    long_text = "Ti 掺杂 LFP 循环性能 提升 " + ("A" * 320)
    pdf_chunks = {
        f"10.1/{index}": [
            {"text": long_text + f" chunk1-{index}", "page": 1},
            {"text": f"Ti 掺杂 LFP 循环性能 第二片段 {index}", "page": 2},
            {"text": f"Ti 掺杂 LFP 循环性能 第三片段 {index}", "page": 3},
        ]
        for index in range(1, 12)
    }

    logger = _Logger()
    result = format_reference_evidence(
        pdf_chunks=pdf_chunks,
        user_question="Ti 掺杂 LFP 的循环性能如何？",
        logger=logger,
    )

    assert "### 文献10:" in result
    assert "### 文献11:" not in result
    assert "第三片段 1" not in result
    assert ("A" * 260) not in result
    assert "..." in result


def test_stage4_streaming_default_formatter_delegates_to_reference_alignment(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_EVIDENCE_CHUNKS_PER_DOI", "1")
    monkeypatch.setenv("QA_STAGE4_EVIDENCE_CHUNK_MAX_CHARS", "250")

    pdf_chunks = {
        "10.1/a": [
            {"text": "Ti 掺杂 LFP 循环性能 提升 " + ("B" * 280), "page": 1},
            {"text": "不会出现的第二段", "page": 2},
        ]
    }

    reference_result = format_reference_evidence(
        pdf_chunks=pdf_chunks,
        user_question="Ti 掺杂 LFP 的循环性能如何？",
        logger=_Logger(),
    )
    streaming_result = format_streaming_evidence(
        pdf_chunks=pdf_chunks,
        user_question="Ti 掺杂 LFP 的循环性能如何？",
    )

    assert streaming_result == reference_result
