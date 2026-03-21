from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from app.modules.documents.api import check_pdf, reference_preview_get, view_pdf


class _FakeRequest:
    def __init__(self, app):
        self.app = app
        self.method = "GET"


def test_view_pdf_returns_inline_file(monkeypatch):
    with TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "10.1_a.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%mock\n")
        fake_app = SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(papers_dir=Path(tmpdir))), logger=None)
        monkeypatch.setattr(
            "app.modules.documents.api.documents_service.view_pdf_path",
            lambda **_kwargs: ({"success": True}, 200, pdf_path),
        )
        response = view_pdf("10.1/a", _FakeRequest(fake_app))

    assert response.media_type == "application/pdf"
    assert "inline;" in response.headers["content-disposition"].lower()


def test_check_pdf_route_returns_exists_payload(monkeypatch):
    fake_app = SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(papers_dir=Path("/tmp"))), logger=None)
    monkeypatch.setattr(
        "app.modules.documents.api.documents_service.check_pdf",
        lambda **_kwargs: ({"success": True, "exists": True, "doi": "10.1/a", "filename": "10.1_a.pdf"}, 200),
    )
    response = check_pdf("10.1/a", _FakeRequest(fake_app))

    assert response.status_code == 200
    assert b'"exists":true' in response.body


def test_reference_preview_route_returns_service_payload(monkeypatch):
    fake_app = SimpleNamespace(state=SimpleNamespace(generation_runtime=None), logger=None)
    monkeypatch.setattr(
        "app.modules.documents.api.documents_service.reference_preview",
        lambda **_kwargs: ({"success": True, "items": [{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1/a"}]}, 200),
    )
    response = reference_preview_get(_FakeRequest(fake_app), dois=["10.1/a"], dois_text="", max_items=10)

    assert response.status_code == 200
    assert b'"pdf_url":"/api/v1/view_pdf/10.1/a"' in response.body
