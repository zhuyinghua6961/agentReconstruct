from __future__ import annotations

from pathlib import Path

from server.services.documents_service import documents_service


def test_view_pdf_path_normalizes_polluted_doi(monkeypatch, tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    captured: dict[str, str] = {}

    def _fake_ensure_local_paper_pdf(*, doi: str, papers_dir: Path, logger=None):
        captured["doi"] = doi
        return pdf_path

    monkeypatch.setattr("server.services.documents_service.ensure_local_paper_pdf", _fake_ensure_local_paper_pdf)

    payload, status_code, resolved = documents_service.view_pdf_path("doi:10.1007_s11581-021-04073-2).", logger=None)

    assert status_code == 200
    assert resolved == pdf_path
    assert payload["doi"] == "10.1007/s11581-021-04073-2"
    assert captured["doi"] == "10.1007/s11581-021-04073-2"
