from __future__ import annotations

from types import SimpleNamespace

from app.modules.documents.pdf_text_extractor import exclude_references_section
from app.modules.documents.service import documents_service


def test_pack_translation_chunks_merges_small_paragraphs():
    paragraphs = [("word " * 59 + "word") for _ in range(20)]
    text = "\n\n".join(paragraphs)
    chunks = documents_service._pack_translation_chunks(text)
    assert len(chunks) < 20
    assert 2 <= len(chunks) <= 4
    assert "\n\n".join(chunks) == text


def test_build_doi_translation_segments_no_segment_cap(monkeypatch, tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    paragraphs = [f"Paragraph {index}. " + ("content " * 40) for index in range(100)]
    body = "\n\n".join(paragraphs)

    monkeypatch.setattr(documents_service, "_ensure_local_pdf", lambda **kwargs: pdf_path)
    monkeypatch.setattr(documents_service, "_extract_pdf_body", lambda **kwargs: body)

    segments, payload, status_code = documents_service._build_doi_translation_segments(
        doi="10.1000/test",
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
    )

    assert status_code == 200
    assert payload["truncated"] is False
    assert len(segments) < 100
    assert len(segments) > 5
    assert payload["segment_count"] == len(segments)


def test_pdf_text_extractor_exclude_references():
    references_page = (
        "References\n\n"
        "10.1000/ref-1 https://example.com/a 2020\n"
        "10.1000/ref-2 https://example.com/b 2021\n"
        "10.1000/ref-3 https://example.com/c 2022\n"
    )
    pages = [(1, "Introduction body"), (2, references_page)]
    logger = SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)

    trimmed = exclude_references_section(pages, logger)

    assert trimmed == [(1, "Introduction body")]


def test_natural_paragraphs_splits_on_blank_lines():
    paragraphs = documents_service._natural_paragraphs("Alpha\n\nBeta\n\nGamma")
    assert paragraphs == ["Alpha", "Beta", "Gamma"]


def test_clip_text_at_boundary_prefers_paragraph_break():
    text = "A" * 1000 + "\n\n" + "B" * 1000
    clipped = documents_service._clip_text_at_boundary(text, 1100)
    assert clipped.endswith("A")
    assert len(clipped) <= 1100
