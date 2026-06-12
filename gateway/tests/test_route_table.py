from app.main import app
from app.services.route_table import PUBLIC_ROUTE_PATTERNS, QA_ROUTE_PATTERNS


def test_route_table_patterns_are_registered():
    registered = {route.path for route in app.routes}
    for path in PUBLIC_ROUTE_PATTERNS + QA_ROUTE_PATTERNS:
        assert path in registered


def test_public_and_qa_route_tables_do_not_overlap():
    assert set(PUBLIC_ROUTE_PATTERNS).isdisjoint(set(QA_ROUTE_PATTERNS))


def test_route_table_patterns_include_department_routes():
    expected = {
        "/api/auth/departments/tree",
        "/api/v1/auth/departments/tree",
        "/api/auth/department",
        "/api/v1/auth/department",
        "/api/admin/departments/tree",
        "/api/admin/departments/primary",
        "/api/admin/departments/primary/{primary_id}",
        "/api/admin/departments/primary/{primary_id}/status",
        "/api/admin/departments/primary/{primary_id}/direct-users",
        "/api/admin/departments/secondary",
        "/api/admin/departments/secondary/{secondary_id}",
        "/api/admin/departments/secondary/{secondary_id}/status",
        "/api/admin/departments/secondary/{secondary_id}/direct-users",
        "/api/admin/departments/secondary/{secondary_id}/users",
        "/api/admin/departments/secondary/{secondary_id}/legacy-users",
        "/api/admin/departments/tertiary",
        "/api/admin/departments/tertiary/{tertiary_id}",
        "/api/admin/departments/tertiary/{tertiary_id}/status",
        "/api/admin/departments/tertiary/{tertiary_id}/users",
        "/api/admin/departments/batch-delete",
        "/api/admin/departments/batch-status",
        "/api/admin/departments/{level}/{department_id}/force-delete",
        "/api/admin/departments/batch-force-delete",
        "/api/admin/departments/batch-import",
        "/api/admin/departments/import-template",
    }

    assert expected.issubset(set(PUBLIC_ROUTE_PATTERNS))


def test_auth_register_routes_keep_api_and_v1_parity():
    expected = {
        "/api/auth/register",
        "/api/v1/auth/register",
    }

    assert expected.issubset(set(PUBLIC_ROUTE_PATTERNS))


def test_auth_department_routes_keep_api_and_v1_parity_even_when_rejected():
    expected = {
        "/api/auth/departments/tree",
        "/api/v1/auth/departments/tree",
        "/api/auth/department",
        "/api/v1/auth/department",
    }

    assert expected.issubset(set(PUBLIC_ROUTE_PATTERNS))


def test_route_table_patterns_include_username_routes():
    expected = {
        "/api/auth/username",
        "/api/v1/auth/username",
        "/api/admin/users/{user_id}/username",
    }

    assert expected.issubset(set(PUBLIC_ROUTE_PATTERNS))


def test_route_table_patterns_include_personnel_routes():
    expected = {
        "/api/auth/personnel-binding",
        "/api/v1/auth/personnel-binding",
        "/api/admin/personnel",
        "/api/admin/personnel/{personnel_id}",
        "/api/admin/personnel/{personnel_id}/status",
        "/api/admin/personnel/{personnel_id}/bindings",
        "/api/admin/personnel/batch-delete",
        "/api/admin/personnel/batch-status",
        "/api/admin/personnel/batch-department",
        "/api/admin/personnel/{personnel_id}/force-delete",
        "/api/admin/personnel/batch-force-delete",
        "/api/admin/personnel/batch-import",
        "/api/admin/personnel/import-template",
        "/api/admin/users/{user_id}/personnel-binding",
    }

    assert expected.issubset(set(PUBLIC_ROUTE_PATTERNS))


def test_route_table_patterns_include_admin_model_status_route():
    assert "/api/admin/model-status" in set(PUBLIC_ROUTE_PATTERNS)
    assert "/api/admin/model-status/test" in set(PUBLIC_ROUTE_PATTERNS)
