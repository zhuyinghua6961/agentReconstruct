from app.main import app


def test_public_route_surface_contains_key_modules():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    expected = {
        "/api/auth/login",
        "/api/conversations",
        "/internal/conversations/{conversation_id}/messages/user",
        "/internal/conversations/{conversation_id}/context-snapshot",
        "/internal/conversations/{conversation_id}/messages/assistant-async",
        "/api/upload_pdf",
        "/api/reference_preview",
        "/api/patent/original/{canonical_patent_id}",
        "/api/quota/my",
        "/api/admin/users",
        "/api/kb_info",
    }
    assert expected.issubset(paths)


def test_internal_quota_grant_routes_are_not_exposed_on_public_api_surface():
    paths = {route.path for route in app.routes if hasattr(route, "path")}

    assert "/internal/quota/grants/precheck" in paths
    assert "/internal/quota/grants/{grant_id}/finalize" in paths
    assert "/api/quota/grants/precheck" not in paths
    assert "/api/v1/quota/grants/precheck" not in paths
    assert "/api/quota/grants/{grant_id}/finalize" not in paths
    assert "/api/v1/quota/grants/{grant_id}/finalize" not in paths
