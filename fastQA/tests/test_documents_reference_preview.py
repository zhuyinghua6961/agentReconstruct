from __future__ import annotations

import json
from types import SimpleNamespace

from app.modules.documents.api import reference_preview_get, reference_preview_post
from app.modules.documents.schemas import ReferencePreviewRequest


class _FakeRequest:
    def __init__(self):
        self.app = SimpleNamespace(state=SimpleNamespace(generation_runtime=None, logger=None), logger=None)
        self.method = "GET"


def test_reference_preview_post_accepts_frontend_doi_field(monkeypatch):
    monkeypatch.setattr(
        "app.modules.documents.api.documents_service.reference_preview",
        lambda **kwargs: (
            {
                "items": [{"doi": "10.1/demo", "title": "demo"}],
                "count": 1,
                "requested_count": len(kwargs.get("doi_list") or []),
                "max_items": kwargs.get("max_items"),
                "truncated": False,
            },
            200,
        ),
    )

    response = reference_preview_post(
        ReferencePreviewRequest(doi=["10.1/demo", "10.2/demo"], max_items=5),
        _FakeRequest(),
    )
    payload = json.loads(response.body)

    assert response.status_code == 200
    assert payload["count"] == 1
    assert payload["requested_count"] == 2


def test_reference_preview_get_accepts_doi_query_alias(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_reference_preview(**kwargs):
        captured.update(kwargs)
        return {"items": [], "count": 0, "requested_count": 2, "max_items": 4, "truncated": False}, 200

    monkeypatch.setattr("app.modules.documents.api.documents_service.reference_preview", _fake_reference_preview)

    response = reference_preview_get(
        _FakeRequest(),
        dois=[],
        doi=["10.1/demo", "10.2/demo"],
        dois_text="",
        max_items=4,
    )

    assert response.status_code == 200
    assert captured["doi_list"] == ["10.1/demo", "10.2/demo"]
    assert captured["max_items"] == 4
