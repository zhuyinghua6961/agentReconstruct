from app.main import app


def test_public_route_surface_contains_key_modules():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    expected = {
        "/api/auth/login",
        "/api/conversations",
        "/api/upload_pdf",
        "/api/reference_preview",
        "/api/quota/my",
        "/api/admin/users",
        "/api/kb_info",
    }
    assert expected.issubset(paths)
