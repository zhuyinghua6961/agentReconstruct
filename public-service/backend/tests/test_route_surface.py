from app.main import app


def test_public_route_surface_contains_key_modules():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    expected = {
        "/api/auth/login",
        "/api/auth/username",
        "/api/v1/auth/username",
        "/api/conversations",
        "/internal/conversations/{conversation_id}/messages/user",
        "/internal/conversations/{conversation_id}/context-snapshot",
        "/internal/conversations/{conversation_id}/messages/assistant-async",
        "/internal/conversations/{conversation_id}/messages/assistant-terminal-async",
        "/internal/conversations/{conversation_id}/tasks/{task_id}/create-turn",
        "/api/upload_pdf",
        "/api/reference_preview",
        "/api/patent/original/{canonical_patent_id}",
        "/api/quota/my",
        "/api/admin/users",
        "/api/admin/departments/tree",
        "/api/admin/departments/primary",
        "/api/admin/departments/secondary/{secondary_id}/users",
        "/api/admin/departments/secondary/{secondary_id}/legacy-users",
        "/api/admin/departments/tertiary",
        "/api/admin/departments/tertiary/{tertiary_id}",
        "/api/admin/departments/tertiary/{tertiary_id}/status",
        "/api/admin/departments/tertiary/{tertiary_id}/users",
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
