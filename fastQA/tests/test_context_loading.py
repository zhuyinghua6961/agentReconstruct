from __future__ import annotations

import sys
from types import SimpleNamespace

import app.modules.generation_pipeline.context_loading as context_loading_module
from app.modules.generation_pipeline.context_loading import load_pdf_sentences


class _Doc:
    def __init__(self, texts):
        self._texts = texts
        self.page_count = len(texts)

    def __getitem__(self, index):
        return SimpleNamespace(get_text=lambda: self._texts[index])

    def close(self):
        return None


class _FitzModule:
    def __init__(self, texts):
        self._texts = texts

    def open(self, _path):
        return _Doc(self._texts)


class _Logger(SimpleNamespace):
    def __init__(self):
        super().__init__(info=lambda *a, **k: None, debug=lambda *a, **k: None, warning=lambda *a, **k: None)


def test_load_pdf_sentences_reads_local_pdf_and_splits_sentences(monkeypatch, tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_text("stub", encoding="utf-8")

    monkeypatch.setitem(sys.modules, "fitz", _FitzModule([
        "Short. This is a sufficiently long first sentence for testing. Another long sentence appears here!",
        "Second page keeps enough text so extraction still works and should be split correctly.",
    ]))
    monkeypatch.setattr(
        "app.modules.generation_pipeline.context_loading.storage_service.ensure_local_paper_pdf",
        lambda **kwargs: pdf_path,
    )
    monkeypatch.setattr(
        "app.modules.generation_pipeline.context_loading.get_settings",
        lambda: SimpleNamespace(papers_dir=tmp_path),
    )

    sentences = load_pdf_sentences(
        doi="10.1/test",
        max_pages=5,
        max_chars=10000,
        logger=_Logger(),
    )

    assert sentences is not None
    assert len(sentences) >= 2
    assert any("sufficiently long first sentence" in sentence for sentence in sentences)
    assert any("Second page keeps enough text" in sentence for sentence in sentences)


def test_context_loading_uses_storage_service_entrypoint(monkeypatch, tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_text("stub", encoding="utf-8")

    monkeypatch.setitem(sys.modules, "fitz", _FitzModule([
        "This is a sufficiently long first sentence for testing. Another long sentence appears here!",
    ]))
    monkeypatch.setattr(
        "app.modules.generation_pipeline.context_loading.storage_service.ensure_local_paper_pdf",
        lambda **kwargs: pdf_path,
    )

    assert not hasattr(context_loading_module, "ensure_local_paper_pdf")

    monkeypatch.setattr(
        "app.modules.generation_pipeline.context_loading.get_settings",
        lambda: SimpleNamespace(papers_dir=tmp_path),
    )

    sentences = load_pdf_sentences(doi="10.1/test", max_pages=5, max_chars=10000, logger=_Logger())

    assert sentences is not None
    assert any("sufficiently long first sentence" in sentence for sentence in sentences)
