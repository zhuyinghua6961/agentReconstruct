from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.errors import codes
from server_fastapi.app import create_app


def _enable_original_route_compatibility(app) -> None:
    app.state.original_route_compatibility_enabled = True


class _FakeOriginalService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def get_original_view(
        self,
        *,
        canonical_patent_id: str,
        section: str,
        claim_number: int | None,
        paragraph_id: str | None,
        response_format: str,
        head_only: bool,
        trace_id: str,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "canonical_patent_id": canonical_patent_id,
                "section": section,
                "claim_number": claim_number,
                "paragraph_id": paragraph_id,
                "response_format": response_format,
                "head_only": head_only,
                "trace_id": trace_id,
            }
        )
        return {
            "kind": "content",
            "status_code": 200,
            "headers": {"Cache-Control": "public, max-age=60", "ETag": '"patent-original-v1"'},
            "payload": {
                "success": True,
                "canonical_patent_id": canonical_patent_id,
                "title": "A patent title",
                "provider": "patent_source_x",
                "section": section,
                "section_label": "Claim 1",
                "content_format": "html" if response_format == "html" else response_format,
                "content": "<div>claim text</div>",
                "trace_id": trace_id,
            },
        }


class _RedirectingOriginalService(_FakeOriginalService):
    def get_original_view(
        self,
        *,
        canonical_patent_id: str,
        section: str,
        claim_number: int | None,
        paragraph_id: str | None,
        response_format: str,
        head_only: bool,
        trace_id: str,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "canonical_patent_id": canonical_patent_id,
                "section": section,
                "claim_number": claim_number,
                "paragraph_id": paragraph_id,
                "response_format": response_format,
                "head_only": head_only,
                "trace_id": trace_id,
            }
        )
        return {
            "kind": "redirect",
            "status_code": 302,
            "headers": {"Cache-Control": "public, max-age=300", "ETag": '"patent-original-redirect-v1"'},
            "redirect_url": f"https://provider.example/patent/{canonical_patent_id}",
        }


def test_original_routes_are_registered_for_get_and_head():
    app = create_app()
    route_map = {(route.path, tuple(sorted(route.methods or []))) for route in app.routes}

    assert ("/api/patent/original/{canonical_patent_id}", ("GET", "HEAD")) in route_map
    assert ("/api/v1/patent/original/{canonical_patent_id}", ("GET", "HEAD")) in route_map


def test_get_original_route_returns_structured_payload_and_preserves_headers():
    app = create_app()
    _enable_original_route_compatibility(app)
    service = _FakeOriginalService()
    app.state.original_service = service

    with TestClient(app) as client:
        response = client.get(
            "/api/patent/original/CN123456789A",
            params={"section": "claim", "claim_number": "1", "format": "json"},
            headers={"X-Trace-ID": "req_original_1"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == codes.SERVICE_NOT_READY
    assert service.calls == []


def test_default_app_original_route_is_disabled_until_compatibility_is_enabled():
    app = create_app()

    with TestClient(app) as client:
        response = client.get(
            "/api/patent/original/CN123456789A",
            params={"section": "claim", "claim_number": "1", "format": "json"},
            headers={"X-Trace-ID": "req_original_default"},
        )

    assert response.status_code == 503
    payload = response.json()
    assert payload["success"] is False
    assert payload["code"] == codes.SERVICE_NOT_READY
    assert "gateway/public original route" in payload["message"]


def test_original_route_stays_disabled_even_when_compatibility_flag_is_enabled():
    app = create_app()
    _enable_original_route_compatibility(app)

    with TestClient(app) as client:
        response = client.get(
            "/api/patent/original/CN123456789A",
            params={"section": "claim", "claim_number": "1", "format": "json"},
            headers={"X-Trace-ID": "req_original_default"},
        )

    assert response.status_code == 503
    payload = response.json()
    assert payload["success"] is False
    assert payload["code"] == codes.SERVICE_NOT_READY
    assert "gateway/public original route" in payload["message"]

def test_original_route_does_not_serve_html_even_when_compatibility_flag_is_enabled():
    app = create_app()
    _enable_original_route_compatibility(app)

    with TestClient(app) as client:
        response = client.get(
            "/api/patent/original/CN123456789A",
            params={"section": "claim", "claim_number": "1"},
            headers={"X-Trace-ID": "req_original_html"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == codes.SERVICE_NOT_READY

def test_original_route_does_not_serve_user_controlled_html_when_compatibility_flag_is_enabled():
    app = create_app()
    _enable_original_route_compatibility(app)

    with TestClient(app) as client:
        response = client.get(
            "/api/patent/original/CN123456789A",
            params={"section": "description", "paragraph_id": "</div><script>alert(1)</script><div>"},
            headers={"X-Trace-ID": "req_original_escape"},
        )

    assert response.status_code == 503
    assert response.json()["code"] == codes.SERVICE_NOT_READY


def test_head_original_route_stays_disabled_even_when_compatibility_flag_is_enabled():
    app = create_app()
    _enable_original_route_compatibility(app)
    service = _RedirectingOriginalService()
    app.state.original_service = service

    with TestClient(app) as client:
        response = client.head(
            "/api/v1/patent/original/CN123456789A",
            params={"section": "fulltext", "format": "redirect"},
            headers={"X-Trace-ID": "req_original_2"},
            follow_redirects=False,
        )

    assert response.status_code == 503
    assert response.text == ""
    assert service.calls == []


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"section": "bogus", "format": "json"}, "section must be one of"),
        ({"section": "claim", "format": "json"}, "claim_number is required"),
        ({"section": "claim", "claim_number": "x", "format": "json"}, "claim_number must be an integer"),
        ({"section": "fulltext", "format": "bogus"}, "format must be one of"),
        ({"section": "fulltext", "claim_number": "1", "format": "json"}, "claim_number is only allowed"),
        ({"section": "claim", "paragraph_id": "p-1", "format": "json"}, "paragraph_id is only allowed"),
        ({"section": "abstract", "paragraph_id": "p-1", "format": "json"}, "paragraph_id is only allowed"),
    ],
)
def test_original_route_maps_semantic_query_validation_errors_to_invalid_request(params, message):
    app = create_app()
    _enable_original_route_compatibility(app)
    app.state.original_service = _FakeOriginalService()

    with TestClient(app) as client:
        response = client.get("/api/patent/original/CN123456789A", params=params)

    assert response.status_code == 503
    payload = response.json()
    assert payload["success"] is False
    assert payload["code"] == codes.SERVICE_NOT_READY
    assert "gateway/public original route" in payload["message"]
