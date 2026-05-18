from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.main import app
from app.modules.documents.api import check_pdf, reference_preview_get, view_pdf
from app.modules.documents.service import documents_service
from app.modules.storage.paper_storage import find_local_paper_pdf


class _FakeRequest:
    def __init__(self, papers_dir: Path | None = None):
        settings = SimpleNamespace(papers_dir=papers_dir) if papers_dir is not None else SimpleNamespace(papers_dir=None)
        self.app = SimpleNamespace(state=SimpleNamespace(settings=settings, generation_runtime=None, logger=None), logger=None)
        self.method = "GET"


def test_view_pdf_returns_inline_response(monkeypatch, tmp_path):
    pdf_path = tmp_path / "10.1_test.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test\n")
    monkeypatch.setattr("app.modules.documents.service.storage_service.ensure_local_paper_pdf", lambda **_kwargs: pdf_path)
    request = _FakeRequest(tmp_path)
    request.method = "HEAD"
    response = view_pdf("10.1/test", request)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"].startswith("inline;")

    request.method = "GET"
    get_response = view_pdf("10.1/test", request)
    assert get_response.status_code == 200
    assert get_response.headers["content-disposition"].startswith("inline;")


def test_check_pdf_returns_exists_payload(monkeypatch, tmp_path):
    pdf_path = tmp_path / "10.1_demo.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test\n")
    monkeypatch.setattr("app.modules.documents.service.storage_service.paper_exists", lambda **_kwargs: True)
    response = check_pdf("10.1/demo", _FakeRequest(tmp_path))
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["exists"] is True
    assert payload["filename"] == "10.1_demo.pdf"


def test_view_pdf_handles_encoded_pdf_suffix_and_parentheses(monkeypatch, tmp_path):
    pdf_path = tmp_path / "10.2_demo.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test\n")
    monkeypatch.setattr("app.modules.documents.service.storage_service.ensure_local_paper_pdf", lambda **_kwargs: pdf_path)
    response = view_pdf("(10.2%2Fdemo.pdf)", _FakeRequest(tmp_path))

    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("inline;")


def test_documents_service_view_pdf_path_uses_storage_service_normalize_doi(monkeypatch, tmp_path):
    calls = []
    pdf_path = tmp_path / "demo.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test\n")

    def fake_normalize(value):
        calls.append(value)
        return "10.2/demo"

    def fake_ensure_local_paper_pdf(**kwargs):
        assert kwargs["doi"] == "10.2/demo"
        return pdf_path

    monkeypatch.setattr("app.modules.documents.service.storage_service.normalize_doi", fake_normalize)
    monkeypatch.setattr("app.modules.documents.service.storage_service.ensure_local_paper_pdf", fake_ensure_local_paper_pdf)

    payload, status_code, resolved = documents_service.view_pdf_path("(10.2%2Fdemo.pdf)", logger=None, papers_dir=tmp_path)

    assert calls == ["(10.2%2Fdemo.pdf)"]
    assert payload == {}
    assert status_code == 200
    assert resolved == pdf_path


def test_reference_preview_reports_requested_count_and_truncation(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.modules.documents.service.build_reference_preview_batch",
        lambda **_kwargs: [{"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1/a"}],
    )

    payload, status_code = documents_service.reference_preview(
        dois_text="10.1/a,10.2/b",
        doi_list=["10.2/b", "10.3/c"],
        max_items=1,
        agent=None,
        logger=None,
        papers_dir=tmp_path,
    )

    assert status_code == 200
    assert payload["count"] == 1
    assert payload["requested_count"] == 3
    assert payload["max_items"] == 1
    assert payload["truncated"] is True


def test_reference_preview_returns_pdf_url(monkeypatch):
    monkeypatch.setattr(
        documents_service,
        "reference_preview",
        lambda **_kwargs: (
            {"success": True, "items": [{"doi": "10.1/a", "pdf_exists": True, "pdf_url": "/api/v1/view_pdf/10.1/a"}], "count": 1},
            200,
        ),
    )

    response = reference_preview_get(_FakeRequest(), dois=["10.1/a"], doi=[], dois_text="", max_items=None)
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["items"][0]["pdf_url"] == "/api/v1/view_pdf/10.1/a"


def test_find_local_paper_pdf_accepts_multiple_doi_variants(tmp_path):
    pdf_path = tmp_path / "10.1000_abc_def.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test\n")

    assert find_local_paper_pdf(doi="10.1000/abc/def", papers_dir=tmp_path) == pdf_path.resolve()
    assert find_local_paper_pdf(doi="10.1000_abc_def", papers_dir=tmp_path) == pdf_path.resolve()
    assert find_local_paper_pdf(doi="10.1000%2Fabc%2Fdef.pdf", papers_dir=tmp_path) == pdf_path.resolve()


def test_main_app_registers_documents_routes():
    route_paths = {getattr(route, "path", "") for route in app.routes}

    assert "/api/view_pdf/{doi:path}" in route_paths
    assert "/api/v1/view_pdf/{doi:path}" in route_paths
    assert "/api/check_pdf/{doi:path}" in route_paths
    assert "/api/v1/reference_preview" in route_paths
