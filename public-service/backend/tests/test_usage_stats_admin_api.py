from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.responses import Response
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.auth.deps import require_admin_context


def test_usage_stats_route_requires_admin():
    app = create_app()
    with TestClient(app) as client:
        response = client.get(
            "/api/admin/usage-stats",
            params={"from": "2026-01-01", "to": "2026-01-07"},
        )
    assert response.status_code in {401, 403}


def test_usage_stats_route_returns_rows_for_admin(monkeypatch):
    app = create_app()
    today = date.today()
    fake_payload = {
        "success": True,
        "data": [
            {
                "id": 7,
                "username": "alice",
                "personnel_display": "未绑定",
                "department_display": "未填写",
                "ask_query_count": 2,
                "file_qa_count": 1,
                "literature_search_count": 3,
                "patent_search_count": 4,
                "active_seconds": 120,
                "last_active_at": f"{today.isoformat()}T10:00:00",
            }
        ],
        "pagination": {"page": 1, "page_size": 20, "total": 1},
    }

    with patch(
        "app.modules.usage_stats.admin_api.usage_stats_service_module.usage_stats_service.list_usage_stats",
        return_value=fake_payload,
    ):
        with TestClient(app) as client:
            client.app.dependency_overrides[require_admin_context] = lambda: SimpleNamespace(
                user_id=1,
                role="admin",
                username="root",
            )
            response = client.get(
                "/api/admin/usage-stats",
                params={
                    "from": (today - timedelta(days=6)).isoformat(),
                    "to": today.isoformat(),
                },
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"][0]["literature_search_count"] == 3
    assert payload["data"][0]["patent_search_count"] == 4


def test_usage_stats_export_route_returns_file_for_admin(monkeypatch):
    app = create_app()
    today = date.today()

    with patch(
        "app.modules.usage_stats.admin_api.usage_stats_service_module.usage_stats_service.export_usage_stats",
        return_value=Response(
            content="账号,绑定人员\nalice,未绑定",
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="usage_stats.csv"'},
        ),
    ):
        with TestClient(app) as client:
            client.app.dependency_overrides[require_admin_context] = lambda: SimpleNamespace(
                user_id=1,
                role="admin",
                username="root",
            )
            response = client.get(
                "/api/admin/usage-stats/export",
                params={
                    "from": (today - timedelta(days=6)).isoformat(),
                    "to": today.isoformat(),
                    "format": "csv",
                    "sort_by": "ask_total",
                    "sort_order": "desc",
                },
            )

    assert response.status_code == 200
    assert "text/csv" in response.headers.get("content-type", "")
