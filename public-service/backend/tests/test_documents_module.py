from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import threading
import time

from fastapi.testclient import TestClient

from app.core import runtime as runtime_module
from app.core.deps import AuthContext
from app.main import app
from app.modules.auth import service as auth_service_module
from app.modules.auth.deps import get_optional_auth_context, require_auth_context
from app.modules.documents import reference_preview as reference_preview_module
from app.modules.documents.reference_preview import build_reference_preview_batch, query_graph_reference_metadata
from app.modules.documents.service import documents_service
from app.modules.quota import service as quota_service_module
from app.modules.retrieval.models import ChromaBootstrapResult, RetrievalBindings, RetrievalRuntimeConfig


def test_document_routes_registered():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/api/view_pdf/{doi:path}" in paths
    assert "/api/translate" in paths
    assert "/api/translate_document" in paths
    assert "/api/reference_preview" in paths
    assert "/api/patent/original/{canonical_patent_id}" in paths


def test_view_pdf_route_serves_file(monkeypatch, tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    with TestClient(app) as client:
        client.app.dependency_overrides[require_auth_context] = lambda: AuthContext(
            user_id=7,
            role="user",
            username="alice",
        )
        monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
        monkeypatch.setattr(quota_service_module.quota_service, "check_quota", lambda **kwargs: {"success": True, "allowed": True})
        monkeypatch.setattr(quota_service_module.quota_service, "increment_quota", lambda **kwargs: {"success": True})
        monkeypatch.setattr(documents_service, "view_pdf_path", lambda doi, logger: ({}, 200, pdf_path))

        response = client.get("/api/view_pdf/10.1000/test")
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")


def test_documents_service_view_pdf_path_normalizes_polluted_doi(monkeypatch, tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    captured: dict[str, str] = {}

    def _fake_ensure_local_pdf(*, doi: str, logger):
        captured["doi"] = doi
        return pdf_path

    monkeypatch.setattr(documents_service, "_ensure_local_pdf", _fake_ensure_local_pdf)

    payload, status_code, resolved = documents_service.view_pdf_path("doi:10.1007_s11581-021-04073-2).", logger=None)

    assert status_code == 200
    assert resolved == pdf_path
    assert captured["doi"] == "10.1007/s11581-021-04073-2"
    assert payload["doi"] == "10.1007/s11581-021-04073-2"


def test_documents_service_view_pdf_path_normalizes_equals_prefixed_doi(monkeypatch, tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    captured: dict[str, str] = {}

    def _fake_ensure_local_pdf(*, doi: str, logger):
        captured["doi"] = doi
        return pdf_path

    monkeypatch.setattr(documents_service, "_ensure_local_pdf", _fake_ensure_local_pdf)

    payload, status_code, resolved = documents_service.view_pdf_path("doi=10.1016/j.psep.2024.10.111", logger=None)

    assert status_code == 200
    assert resolved == pdf_path
    assert captured["doi"] == "10.1016/j.psep.2024.10.111"
    assert payload["doi"] == "10.1016/j.psep.2024.10.111"


def test_view_pdf_route_accepts_query_token_auth(monkeypatch, tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    with TestClient(app) as client:
        monkeypatch.setattr(auth_service_module.auth_service, "decode_token", lambda token: {"user_id": 7, "role": "user"} if token == "token-1" else None)
        monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "status": "active", "role": "user", "user_type": 3, "username": "alice"})
        monkeypatch.setattr(quota_service_module.quota_service, "check_quota", lambda **kwargs: {"success": True, "allowed": True})
        monkeypatch.setattr(quota_service_module.quota_service, "increment_quota", lambda **kwargs: {"success": True})
        monkeypatch.setattr(documents_service, "view_pdf_path", lambda doi, logger: ({}, 200, pdf_path))

        response = client.get("/api/v1/view_pdf/10.1000/test?token=token-1")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"].startswith("inline;")


def test_view_pdf_head_route_accepts_query_token_auth(monkeypatch, tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    with TestClient(app) as client:
        monkeypatch.setattr(auth_service_module.auth_service, "decode_token", lambda token: {"user_id": 7, "role": "user"} if token == "token-1" else None)
        monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "status": "active", "role": "user", "user_type": 3, "username": "alice"})
        monkeypatch.setattr(quota_service_module.quota_service, "check_quota", lambda **kwargs: {"success": True, "allowed": True})
        monkeypatch.setattr(quota_service_module.quota_service, "increment_quota", lambda **kwargs: {"success": True})
        monkeypatch.setattr(documents_service, "view_pdf_path", lambda doi, logger: ({}, 200, pdf_path))

        response = client.head("/api/v1/view_pdf/10.1000/test?token=token-1")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"].startswith("inline;")


def test_patent_original_route_uses_file_view_quota_and_json_payload(monkeypatch):
    calls: list[tuple[str, str]] = []

    with TestClient(app) as client:
        client.app.dependency_overrides[require_auth_context] = lambda: AuthContext(
            user_id=7,
            role="user",
            username="alice",
        )
        monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "check_quota",
            lambda **kwargs: calls.append(("check", kwargs["quota_type"])) or {"success": True, "allowed": True},
        )
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "increment_quota",
            lambda **kwargs: calls.append(("increment", kwargs["quota_type"])) or {"success": True},
        )
        monkeypatch.setattr(
            documents_service,
            "patent_original_view",
            lambda **kwargs: {
                "status_code": 200,
                "headers": {"etag": '"patent-original:version-1"', "cache-control": "public, max-age=300"},
                "media_type": "application/json",
                "body": {
                    "success": True,
                    "canonical_patent_id": "CN123456789A",
                    "section": "claim",
                    "section_label": "权利要求1",
                    "content_format": "json",
                    "content": {"claim_number": 1},
                },
            },
        )

        response = client.get("/api/v1/patent/original/CN123456789A", params={"section": "claim", "claim_number": "1"})
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["section_label"] == "权利要求1"
    assert response.headers["etag"] == '"patent-original:version-1"'
    assert calls == [("check", "file_view"), ("increment", "file_view")]


def test_patent_original_head_route_accepts_query_token_auth(monkeypatch):
    with TestClient(app) as client:
        monkeypatch.setattr(auth_service_module.auth_service, "decode_token", lambda token: {"user_id": 7, "role": "user"} if token == "token-1" else None)
        monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "status": "active", "role": "user", "user_type": 3, "username": "alice"})
        monkeypatch.setattr(quota_service_module.quota_service, "check_quota", lambda **kwargs: {"success": True, "allowed": True})
        monkeypatch.setattr(quota_service_module.quota_service, "increment_quota", lambda **kwargs: {"success": True})
        monkeypatch.setattr(
            documents_service,
            "patent_original_view",
            lambda **kwargs: {
                "status_code": 200,
                "headers": {"etag": '"patent-original:version-2"', "cache-control": "public, max-age=300"},
                "media_type": "application/json",
                "body": {"success": True},
            },
        )

        response = client.head("/api/v1/patent/original/CN123456789A?section=claim&claim_number=1&token=token-1")

    assert response.status_code == 200
    assert response.text == ""
    assert response.headers["etag"] == '"patent-original:version-2"'


def test_patent_original_fulltext_route_streams_pdf_body(monkeypatch):
    with TestClient(app) as client:
        client.app.dependency_overrides[require_auth_context] = lambda: AuthContext(
            user_id=7,
            role="user",
            username="alice",
        )
        monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
        monkeypatch.setattr(quota_service_module.quota_service, "check_quota", lambda **kwargs: {"success": True, "allowed": True})
        monkeypatch.setattr(quota_service_module.quota_service, "increment_quota", lambda **kwargs: {"success": True})
        monkeypatch.setattr(
            documents_service,
            "patent_original_view",
            lambda **kwargs: {
                "status_code": 200,
                "headers": {"etag": '"patent-original:version-2"', "cache-control": "public, max-age=300"},
                "media_type": "application/pdf",
                "body_iter": iter([b"%PDF-1.4\n", b"tail"]),
            },
        )

        response = client.get("/api/v1/patent/original/CN123456789A", params={"section": "fulltext"})
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["etag"] == '"patent-original:version-2"'
    assert response.content == b"%PDF-1.4\ntail"


def test_patent_original_route_rejects_invalid_anchor_combinations(monkeypatch):
    with TestClient(app) as client:
        client.app.dependency_overrides[require_auth_context] = lambda: AuthContext(
            user_id=7,
            role="user",
            username="alice",
        )
        monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
        monkeypatch.setattr(quota_service_module.quota_service, "check_quota", lambda **kwargs: {"success": True, "allowed": True})

        response = client.get("/api/v1/patent/original/CN123456789A", params={"section": "abstract", "claim_number": "1"})
        client.app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_REQUEST"


def test_translate_and_reference_preview_routes(monkeypatch):
    with TestClient(app) as client:
        client.app.dependency_overrides[require_auth_context] = lambda: AuthContext(
            user_id=7,
            role="user",
            username="alice",
        )
        monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
        monkeypatch.setattr(quota_service_module.quota_service, "check_quota", lambda **kwargs: {"success": True, "allowed": True})
        monkeypatch.setattr(quota_service_module.quota_service, "increment_quota", lambda **kwargs: {"success": True})
        monkeypatch.setattr(
            documents_service,
            "translate",
            lambda **kwargs: ({"success": True, "translations": ["你好"]}, 200),
        )
        monkeypatch.setattr(
            documents_service,
            "reference_preview",
            lambda **kwargs: ({"items": [{"doi": "10.1000/test", "pdf_exists": False}], "count": 1}, 200),
        )

        translate_resp = client.post("/api/translate", json={"texts": ["hello"]})
        preview_resp = client.get("/api/reference_preview?dois=10.1000/test")
        client.app.dependency_overrides.clear()

    assert translate_resp.status_code == 200
    assert translate_resp.json()["translations"] == ["你好"]
    assert preview_resp.status_code == 200
    assert preview_resp.json()["count"] == 1


def test_translate_route_uses_doc_assist_quota(monkeypatch):
    calls: list[tuple[str, str]] = []

    with TestClient(app) as client:
        client.app.dependency_overrides[require_auth_context] = lambda: AuthContext(
            user_id=7,
            role="user",
            username="alice",
        )
        monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "check_quota",
            lambda **kwargs: calls.append(("check", kwargs["quota_type"])) or {"success": True, "allowed": True},
        )
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "increment_quota",
            lambda **kwargs: calls.append(("increment", kwargs["quota_type"])) or {"success": True},
        )
        monkeypatch.setattr(
            documents_service,
            "translate",
            lambda **kwargs: ({"success": True, "translations": ["你好"]}, 200),
        )

        response = client.post("/api/v1/translate", json={"texts": ["hello"]})
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert calls == [("check", "doc_assist"), ("increment", "doc_assist")]


def test_translate_route_preserves_success_when_quota_finalize_fails(monkeypatch):
    with TestClient(app) as client:
        client.app.dependency_overrides[require_auth_context] = lambda: AuthContext(
            user_id=7,
            role="user",
            username="alice",
        )
        monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "check_quota",
            lambda **kwargs: {"success": True, "allowed": True},
        )
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "increment_quota",
            lambda **kwargs: {"success": False, "error": "redis_down"},
        )
        monkeypatch.setattr(
            documents_service,
            "translate",
            lambda **kwargs: ({"success": True, "translations": ["你好"]}, 200),
        )

        response = client.post("/api/v1/translate", json={"texts": ["hello"]})
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["quota_counted"] is False
    assert payload["quota_warning"] == "redis_down"


def test_translate_document_route_uses_doc_assist_quota(monkeypatch):
    calls: list[tuple[str, str]] = []

    with TestClient(app) as client:
        client.app.dependency_overrides[require_auth_context] = lambda: AuthContext(
            user_id=7,
            role="user",
            username="alice",
        )
        monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "check_quota",
            lambda **kwargs: calls.append(("check", kwargs["quota_type"])) or {"success": True, "allowed": True},
        )
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "increment_quota",
            lambda **kwargs: calls.append(("increment", kwargs["quota_type"])) or {"success": True},
        )
        monkeypatch.setattr(
            documents_service,
            "translate_document",
            lambda **kwargs: (
                {
                    "success": True,
                    "document_type": kwargs["document_type"],
                    "document_id": kwargs["document_id"],
                    "translated_text": "译文正文",
                },
                200,
            ),
        )

        response = client.post(
            "/api/v1/translate_document",
            json={"document_type": "doi", "document_id": "10.1000/test"},
        )
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["translated_text"] == "译文正文"
    assert calls == [("check", "doc_assist"), ("increment", "doc_assist")]


def test_translate_document_route_supports_sse_streaming(monkeypatch):
    calls: list[tuple[str, str]] = []

    with TestClient(app) as client:
        client.app.dependency_overrides[require_auth_context] = lambda: AuthContext(
            user_id=7,
            role="user",
            username="alice",
        )
        monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "check_quota",
            lambda **kwargs: calls.append(("check", kwargs["quota_type"])) or {"success": True, "allowed": True},
        )
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "increment_quota",
            lambda **kwargs: calls.append(("increment", kwargs["quota_type"])) or {"success": True},
        )
        monkeypatch.setattr(
            documents_service,
            "stream_translate_document",
            lambda **kwargs: {
                "status_code": 200,
                "headers": {"cache-control": "no-cache"},
                "media_type": "text/event-stream",
                "body_iter": iter(
                    [
                        b'data: {"type":"start","segment_count":2}\n\n',
                        b'data: {"type":"segment","index":0,"translation":"\xe7\xac\xac\xe4\xb8\x80\xe6\xae\xb5"}\n\n',
                        b'data: {"type":"done","translated_text":"\xe7\xac\xac\xe4\xb8\x80\xe6\xae\xb5","cache_status":"miss"}\n\n',
                    ]
                ),
            },
        )

        response = client.post(
            "/api/v1/translate_document",
            headers={"Accept": "text/event-stream"},
            json={"document_type": "doi", "document_id": "10.1000/test"},
        )
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert b'"type":"start"' in response.content
    assert b'"type":"done"' in response.content
    assert calls == [("check", "doc_assist"), ("increment", "doc_assist")]


def test_reference_preview_authenticated_uses_doc_assist_quota(monkeypatch):
    calls: list[tuple[str, str]] = []

    with TestClient(app) as client:
        client.app.dependency_overrides[get_optional_auth_context] = lambda: AuthContext(
            user_id=7,
            role="user",
            username="alice",
        )
        monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "check_quota",
            lambda **kwargs: calls.append(("check", kwargs["quota_type"])) or {"success": True, "allowed": True},
        )
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "increment_quota",
            lambda **kwargs: calls.append(("increment", kwargs["quota_type"])) or {"success": True},
        )
        monkeypatch.setattr(
            documents_service,
            "reference_preview",
            lambda **kwargs: ({"items": [{"doi": "10.1000/test"}], "count": 1}, 200),
        )

        response = client.get("/api/v1/reference_preview", params=[("dois", "10.1000/test")])
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert calls == [("check", "doc_assist"), ("increment", "doc_assist")]


def test_reference_preview_anonymous_compatibility_does_not_consume_quota(monkeypatch):
    quota_calls = {"check": 0, "increment": 0}

    with TestClient(app) as client:
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "check_quota",
            lambda **kwargs: quota_calls.__setitem__("check", quota_calls["check"] + 1) or {"success": True, "allowed": True},
        )
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "increment_quota",
            lambda **kwargs: quota_calls.__setitem__("increment", quota_calls["increment"] + 1) or {"success": True},
        )
        monkeypatch.setattr(
            documents_service,
            "reference_preview",
            lambda **kwargs: ({"items": [{"doi": "10.1000/test"}], "count": 1}, 200),
        )

        response = client.get("/api/v1/reference_preview", params=[("dois", "10.1000/test")])

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert quota_calls == {"check": 0, "increment": 0}


def test_extract_pdf_text_authenticated_missing_doc_assist_config_fails_closed(monkeypatch):
    with TestClient(app) as client:
        client.app.dependency_overrides[get_optional_auth_context] = lambda: AuthContext(
            user_id=7,
            role="user",
            username="alice",
        )
        monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "check_quota",
            lambda **kwargs: {"success": True, "allowed": True, "config_missing": True, "config_active": False},
        )

        response = client.get("/api/v1/extract_pdf_text/10.1000/test")
        client.app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json()["code"] == "QUOTA_CONFIG_MISSING"


def test_literature_content_graph_query_prefers_exact_doi_match():
    captured: dict[str, object] = {}

    class _FakeGraph:
        def run(self, query, **kwargs):
            captured["query"] = query
            captured["kwargs"] = kwargs
            return SimpleNamespace(data=lambda: [{"n": {"title": "Exact", "authors": "A", "journal": "J", "publication_date": "2025", "abstract": "Abs"}}])

    payload, status_code = documents_service.literature_content(
        doi="10.1000/test",
        agent=SimpleNamespace(graph=_FakeGraph(), semantic_expert=None),
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None, error=lambda *args, **kwargs: None),
    )

    assert status_code == 200
    assert payload["title"] == "Exact"
    assert captured["kwargs"] == {"doi": "10.1000/test"}
    assert "n.doi = $doi" in str(captured["query"])
    assert "ORDER BY match_rank ASC" in str(captured["query"])


def test_literature_content_route_reports_retrieval_dependency_when_runtime_missing():
    with TestClient(app) as client:
        response = client.get("/api/v1/literature_content", params={"doi": "10.1000/test"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is False
    assert payload["code"] == "RETRIEVAL_RUNTIME_UNAVAILABLE"
    assert payload["dependency"]["name"] == "retrieval_runtime"
    assert payload["dependency"]["mode"] == "required"


def test_literature_content_route_contract(monkeypatch):
    monkeypatch.setattr(
        documents_service,
        "literature_content",
        lambda **kwargs: (
            {
                "doi": "10.1000/test",
                "title": "Test Paper",
                "authors": "Alice; Bob",
                "journal": "Journal X",
                "publication_date": "2025-01-01",
                "abstract": "abstract",
                "content": "<p>detail</p>",
            },
            200,
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/literature_content?doi=10.1000/test")

    assert response.status_code == 200
    payload = response.json()
    assert payload["doi"] == "10.1000/test"
    assert payload["title"] == "Test Paper"
    assert payload["authors"] == "Alice; Bob"
    assert payload["journal"] == "Journal X"
    assert payload["publication_date"] == "2025-01-01"
    assert payload["abstract"] == "abstract"
    assert payload["content"] == "<p>detail</p>"


def test_literature_content_authenticated_uses_doc_assist_quota(monkeypatch):
    calls: list[tuple[str, str]] = []

    with TestClient(app) as client:
        client.app.dependency_overrides[get_optional_auth_context] = lambda: AuthContext(
            user_id=7,
            role="user",
            username="alice",
        )
        monkeypatch.setattr(auth_service_module.auth_service, "get_user_by_id", lambda user_id: {"id": user_id, "user_type": 3})
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "check_quota",
            lambda **kwargs: calls.append(("check", kwargs["quota_type"])) or {"success": True, "allowed": True},
        )
        monkeypatch.setattr(
            quota_service_module.quota_service,
            "increment_quota",
            lambda **kwargs: calls.append(("increment", kwargs["quota_type"])) or {"success": True},
        )
        monkeypatch.setattr(
            documents_service,
            "literature_content",
            lambda **kwargs: (
                {
                    "doi": "10.1000/test",
                    "title": "Test Paper",
                    "authors": "Alice; Bob",
                    "journal": "Journal X",
                    "publication_date": "2025-01-01",
                    "abstract": "abstract",
                    "content": "<p>detail</p>",
                },
                200,
            ),
        )

        response = client.get("/api/v1/literature_content", params={"doi": "10.1000/test"})
        client.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["doi"] == "10.1000/test"
    assert calls == [("check", "doc_assist"), ("increment", "doc_assist")]


def test_reference_preview_post_accepts_frontend_doi_payload(monkeypatch):
    captured = {}

    def _fake_reference_preview(**kwargs):
        captured.update(kwargs)
        return ({"items": [{"doi": "10.1000/test", "pdf_exists": True}], "count": 1}, 200)

    with TestClient(app) as client:
        monkeypatch.setattr(documents_service, "reference_preview", _fake_reference_preview)
        response = client.post(
            "/api/v1/reference_preview",
            json={"doi": ["10.1000/test"], "max_items": 5},
        )

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert captured["doi_list"] == ["10.1000/test"]
    assert captured["dois_text"] == ""
    assert captured["max_items"] == 5


def test_reference_preview_route_reports_optional_retrieval_dependency_when_runtime_missing():
    with TestClient(app) as client:
        response = client.get("/api/v1/reference_preview", params=[("dois", "10.1000/test")])

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["dependency"]["name"] == "retrieval_runtime"
    assert payload["dependency"]["mode"] == "optional"


def test_literature_content_route_works_with_public_service_kb_runtime(monkeypatch):
    class _FakeCollection:
        def get(self, *, where):
            assert where == {"doi": "10.1000/test"}
            return {
                "ids": ["doc-1"],
                "metadatas": [{"title": "Paper", "authors": "A", "journal": "J", "date": "2024", "abstract": "Abs"}],
                "documents": ["full content"],
            }

    class _FakeVectorClient:
        def __init__(self):
            self.db_path = "/tmp/vector-db"
            self.collection_name = "lfp_papers"

    monkeypatch.setattr(
        runtime_module.retrieval_service,
        "build_bindings",
        lambda **kwargs: RetrievalBindings(
            runtime=RetrievalRuntimeConfig(
                vector_db_path=runtime_module.Path("/tmp/vector-db"),
                vector_collection_name="lfp_papers",
                neo4j_url="",
                neo4j_username="neo4j",
                neo4j_password="password",
            ),
            vector_db_client=_FakeVectorClient(),
            chroma=ChromaBootstrapResult(client=object(), collection=_FakeCollection(), available=True, error=None),
            neo4j_client=None,
        ),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/literature_content", params={"doi": "10.1000/test"})

    assert response.status_code == 200
    assert response.json()["title"] == "Paper"
    assert response.json()["content"] == "full content"


def test_reference_preview_graph_query_prefers_exact_doi_match():
    captured: dict[str, object] = {}

    class _FakeGraph:
        def run(self, query, **kwargs):
            captured["query"] = query
            captured["kwargs"] = kwargs
            return SimpleNamespace(data=lambda: [{"title": "Exact", "journal": "J", "publication_date": "2024", "match_rank": 0}])

    payload = query_graph_reference_metadata(
        agent=SimpleNamespace(graph=_FakeGraph()),
        doi="10.1000/test",
        logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
    )

    assert payload["title"] == "Exact"
    assert captured["kwargs"] == {"doi": "10.1000/test"}
    assert "n.doi = $doi" in str(captured["query"])
    assert "ORDER BY match_rank ASC" in str(captured["query"])


def test_build_reference_preview_batch_preserves_order_under_parallel_execution(monkeypatch, tmp_path):
    monkeypatch.setenv("REFERENCE_PREVIEW_MAX_WORKERS", "4")
    seen_threads: set[int] = set()
    lock = threading.Lock()

    def _graph(agent, doi, logger):
        _ = agent, logger
        with lock:
            seen_threads.add(threading.get_ident())
        time.sleep(0.03)
        return {"title": f"title:{doi}", "journal": "J", "publication_date": "2024", "source": "neo4j"}

    monkeypatch.setattr(reference_preview_module, "query_graph_reference_metadata", _graph)
    monkeypatch.setattr(reference_preview_module, "query_chroma_reference_metadata", lambda *args, **kwargs: {})
    monkeypatch.setattr(reference_preview_module.storage_service, "paper_exists", lambda **kwargs: False)

    items = build_reference_preview_batch(
        dois=["10.1000/a", "10.1000/b", "10.1000/c"],
        agent=object(),
        papers_dir=tmp_path,
        logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
    )

    assert [item["doi"] for item in items] == ["10.1000/a", "10.1000/b", "10.1000/c"]
    assert [item["title"] for item in items] == ["title:10.1000/a", "title:10.1000/b", "title:10.1000/c"]
    assert len(seen_threads) >= 2


def test_reference_preview_route_uses_public_service_kb_runtime(monkeypatch):
    class _FakeCollection:
        def get(self, *, where):
            doi = where["doi"]
            return {
                "ids": [f"doc:{doi}"],
                "metadatas": [{"title": f"title:{doi}", "journal": "J", "date": "2024"}],
                "documents": [f"content:{doi}"],
            }

    class _FakeVectorClient:
        def __init__(self):
            self.db_path = "/tmp/vector-db"
            self.collection_name = "lfp_papers"

    monkeypatch.setattr(
        runtime_module.retrieval_service,
        "build_bindings",
        lambda **kwargs: RetrievalBindings(
            runtime=RetrievalRuntimeConfig(
                vector_db_path=runtime_module.Path("/tmp/vector-db"),
                vector_collection_name="lfp_papers",
                neo4j_url="",
                neo4j_username="neo4j",
                neo4j_password="password",
            ),
            vector_db_client=_FakeVectorClient(),
            chroma=ChromaBootstrapResult(client=object(), collection=_FakeCollection(), available=True, error=None),
            neo4j_client=None,
        ),
    )
    monkeypatch.setattr(
        documents_service,
        "_resolve_papers_dir",
        lambda: runtime_module.Path("/tmp"),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/reference_preview", params=[("dois", "10.1000/test")])

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["items"][0]["title"] == "title:10.1000/test"
