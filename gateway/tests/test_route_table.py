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
        "/api/admin/departments/secondary",
        "/api/admin/departments/secondary/{secondary_id}",
        "/api/admin/departments/secondary/{secondary_id}/status",
        "/api/admin/departments/batch-import",
        "/api/admin/departments/import-template",
    }

    assert expected.issubset(set(PUBLIC_ROUTE_PATTERNS))
