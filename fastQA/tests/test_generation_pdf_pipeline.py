from __future__ import annotations

import logging

import app.modules.generation_pipeline.pdf_pipeline as pdf_pipeline_module
from app.modules.generation_pipeline.pdf_pipeline import find_pdf_path, stage3_load_pdf_chunks


def test_find_pdf_path_prefers_storage_resolution(monkeypatch, tmp_path):
    resolved = tmp_path / "resolved.pdf"
    resolved.write_text("pdf", encoding="utf-8")

    monkeypatch.setattr(
        "app.modules.generation_pipeline.pdf_pipeline.storage_service.ensure_local_paper_pdf",
        lambda **kwargs: resolved,
    )

    assert not hasattr(pdf_pipeline_module, "ensure_local_paper_pdf")

    found = find_pdf_path(doi="10.1/x", papers_dir=tmp_path, logger=logging.getLogger("test.pdf"))

    assert found == str(resolved)


def test_find_pdf_path_supports_exact_and_underscore_names(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_ORIGINAL_MINIO_ONLY", "false")
    exact = tmp_path / "10.1" / "x.pdf"
    exact.parent.mkdir(parents=True)
    exact.write_text("pdf", encoding="utf-8")
    underscore = tmp_path / "10.2_y.pdf"
    underscore.write_text("pdf", encoding="utf-8")

    found_exact = find_pdf_path(doi="10.1/x", papers_dir=tmp_path, logger=logging.getLogger("test.pdf"))
    found_underscore = find_pdf_path(doi="10.2/y", papers_dir=tmp_path, logger=logging.getLogger("test.pdf"))

    assert found_exact == str(exact)
    assert found_underscore == str(underscore)


def test_find_pdf_path_strict_mode_does_not_fallback_to_local_pdf(monkeypatch, tmp_path):
    local_pdf = tmp_path / "10.1_x.pdf"
    local_pdf.write_text("pdf", encoding="utf-8")
    monkeypatch.setenv("QA_ORIGINAL_MINIO_ONLY", "true")
    monkeypatch.setattr(
        "app.modules.generation_pipeline.pdf_pipeline.storage_service.ensure_local_paper_pdf",
        lambda **kwargs: None,
    )

    found = find_pdf_path(doi="10.1/x", papers_dir=tmp_path, logger=logging.getLogger("test.pdf"))

    assert found is None


def test_stage3_load_pdf_chunks_uses_finder_and_extractor(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_ORIGINAL_MINIO_ONLY", "false")
    pdf_path = tmp_path / "10.1_a.pdf"
    pdf_path.write_text("pdf", encoding="utf-8")

    calls: list[tuple[str, int]] = []

    def _extract(**kwargs):
        calls.append((kwargs["doi"], kwargs["max_chunks"]))
        return [{"doi": kwargs["doi"], "text": "evidence"}]

    result = stage3_load_pdf_chunks(
        dois=["10.1/a", "10.2/b"],
        papers_dir=tmp_path,
        max_chunks_per_doi=2,
        logger=logging.getLogger("test.pdf"),
        extract_chunks_fn=_extract,
    )

    assert result == {"10.1/a": [{"doi": "10.1/a", "text": "evidence"}]}
    assert calls == [("10.1/a", 2)]


def test_stage3_load_pdf_chunks_returns_partial_result_on_cancel(monkeypatch, tmp_path):
    monkeypatch.setenv("QA_ORIGINAL_MINIO_ONLY", "false")
    pdf_path = tmp_path / "10.1_a.pdf"
    pdf_path.write_text("pdf", encoding="utf-8")

    state = {"count": 0}

    def _cancel():
        state["count"] += 1
        return state["count"] >= 3

    result = stage3_load_pdf_chunks(
        dois=["10.1/a", "10.2/b"],
        papers_dir=tmp_path,
        max_chunks_per_doi=2,
        logger=logging.getLogger("test.pdf"),
        should_cancel=_cancel,
        extract_chunks_fn=lambda **kwargs: [{"doi": kwargs["doi"], "text": "chunk"}],
    )

    assert result == {"10.1/a": [{"doi": "10.1/a", "text": "chunk"}]}


def test_stage3_load_pdf_chunks_logs_diagnostic_source_and_chunk_details(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("QA_STAGE3_DIAGNOSTIC_LOG", "1")
    monkeypatch.setenv("QA_STAGE3_LOG_SOURCE_DETAILS", "1")
    monkeypatch.setenv("QA_STAGE3_LOG_CHUNK_DETAILS", "1")
    monkeypatch.setenv("QA_STAGE3_LOG_CHUNK_MAX", "2")
    monkeypatch.setenv("QA_STAGE3_LOG_TEXT_MAX_CHARS", "80")

    pdf_path = tmp_path / "10.1_a.pdf"
    pdf_path.write_text("pdf", encoding="utf-8")
    logger = logging.getLogger("test.fastqa.stage3")

    def _find(**kwargs):
        if kwargs["doi"] == "10.1/a":
            return str(pdf_path)
        return None

    def _extract(**kwargs):
        return [
            {
                "doi": kwargs["doi"],
                "page": 2,
                "chunk_id": "c1",
                "chunk_type": "paragraph",
                "text": "第一段证据：LMFP/LFP/三元复配改善低SOC功率。",
            },
            {
                "doi": kwargs["doi"],
                "page": 3,
                "chunk_id": "c2",
                "chunk_type": "paragraph",
                "text": "第二段证据：高SOC快充窗口更稳定。",
            },
        ]

    with caplog.at_level(logging.INFO, logger="test.fastqa.stage3"):
        result = stage3_load_pdf_chunks(
            dois=["10.1/a", "10.2/b"],
            papers_dir=tmp_path,
            max_chunks_per_doi=2,
            logger=logger,
            find_pdf_path_fn=_find,
            extract_chunks_fn=_extract,
        )

    assert result["10.1/a"][0]["text"].startswith("第一段证据")
    messages = [record.message for record in caplog.records if record.name == "test.fastqa.stage3"]
    assert any("Stage3 diagnostic start" in message and "doi_count=2" in message for message in messages)
    assert any(
        "Stage3 source diagnostic" in message
        and "doi=10.1/a" in message
        and "pdf_found=True" in message
        and "chunk_count=2" in message
        for message in messages
    )
    assert any(
        "Stage3 source diagnostic" in message
        and "doi=10.2/b" in message
        and "pdf_found=False" in message
        for message in messages
    )
    assert any(
        "Stage3 chunk diagnostic" in message
        and "doi=10.1/a" in message
        and "page=2" in message
        and "第一段证据" in message
        for message in messages
    )
    assert any(
        "Stage3 diagnostic completed" in message
        and "requested=2" in message
        and "successful=1" in message
        and "missing_pdf=1" in message
        and "total_chunks=2" in message
        for message in messages
    )
