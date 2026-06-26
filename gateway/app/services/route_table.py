"""Gateway route ownership table."""

from __future__ import annotations


def _paths(path: str, *, include_v1: bool = True) -> tuple[str, ...]:
    paths = [path]
    if include_v1:
        paths.append(path.replace("/api/", "/api/v1/", 1))
    return tuple(paths)


def _mode_paths(suffix: str, *, include_v1: bool = True) -> tuple[str, ...]:
    paths = [f"/api/{mode}/{suffix}" for mode in ("fast", "thinking", "patent")]
    if include_v1:
        paths.extend(f"/api/v1/{mode}/{suffix}" for mode in ("fast", "thinking", "patent"))
    return tuple(paths)


_PUBLIC_ROUTE_GROUPS = (
    _paths("/api/auth/login"),
    _paths("/api/auth/register"),
    _paths("/api/auth/me"),
    _paths("/api/auth/departments/tree"),
    _paths("/api/auth/department"),
    _paths("/api/auth/username"),
    _paths("/api/auth/personnel-binding"),
    _paths("/api/auth/password"),
    _paths("/api/auth/forgot-password/initiate"),
    _paths("/api/auth/forgot-password/verify"),
    _paths("/api/auth/security-questions"),
    _paths("/api/conversations"),
    _paths("/api/conversations/{conversation_id}"),
    _paths("/api/conversations/{conversation_id}/title"),
    _paths("/api/conversations/{conversation_id}/messages"),
    _paths("/api/conversations/{conversation_id}/files"),
    _paths("/api/conversations/{conversation_id}/files/{file_id}"),
    _paths("/api/conversations/{conversation_id}/files/{file_id}/download"),
    _paths("/api/upload_pdf"),
    _paths("/api/upload_excel"),
    _paths("/api/clear_pdf"),
    _paths("/api/translate"),
    _paths("/api/translate_document"),
    _paths("/api/kb_info"),
    _paths("/api/refresh_kb"),
    _paths("/api/clear_cache"),
    _paths("/api/background_status"),
    _paths("/api/health"),
    _paths("/api/literature_content"),
    _paths("/api/literature_search"),
    _paths("/api/reference_preview"),
    _paths("/api/patent/original/{canonical_patent_id}"),
    _paths("/api/summarize_pdf/{doi:path}"),
    _paths("/api/extract_pdf_text/{doi:path}"),
    _paths("/api/check_pdf/{doi:path}"),
    _paths("/api/view_pdf/{doi:path}"),
    _paths("/api/quota/my"),
    _paths("/api/quota/configs"),
    _paths("/api/quota/configs/{quota_type:path}"),
    _paths("/api/quota/users/{user_id}"),
    _paths("/api/quota/reset/{user_id}/{quota_type:path}"),
    _paths("/api/admin/users", include_v1=False),
    _paths("/api/admin/model-status", include_v1=False),
    _paths("/api/admin/model-status/test", include_v1=False),
    _paths("/api/admin/users/{user_id}", include_v1=False),
    _paths("/api/admin/users/{user_id}/username", include_v1=False),
    _paths("/api/admin/users/{user_id}/personnel-binding", include_v1=False),
    _paths("/api/admin/users/{user_id}/password", include_v1=False),
    _paths("/api/admin/users/{user_id}/status", include_v1=False),
    _paths("/api/admin/users/{user_id}/type", include_v1=False),
    _paths("/api/admin/users/batch-delete", include_v1=False),
    _paths("/api/admin/users/batch-type", include_v1=False),
    _paths("/api/admin/users/batch-import", include_v1=False),
    _paths("/api/admin/users/import-template", include_v1=False),
    _paths("/api/admin/personnel", include_v1=False),
    _paths("/api/admin/personnel/{personnel_id}", include_v1=False),
    _paths("/api/admin/personnel/{personnel_id}/status", include_v1=False),
    _paths("/api/admin/personnel/{personnel_id}/bindings", include_v1=False),
    _paths("/api/admin/personnel/batch-delete", include_v1=False),
    _paths("/api/admin/personnel/batch-status", include_v1=False),
    _paths("/api/admin/personnel/batch-department", include_v1=False),
    _paths("/api/admin/personnel/{personnel_id}/force-delete", include_v1=False),
    _paths("/api/admin/personnel/batch-force-delete", include_v1=False),
    _paths("/api/admin/personnel/batch-import", include_v1=False),
    _paths("/api/admin/personnel/import-template", include_v1=False),
    _paths("/api/admin/departments/tree", include_v1=False),
    _paths("/api/admin/departments/primary", include_v1=False),
    _paths("/api/admin/departments/primary/{primary_id}", include_v1=False),
    _paths("/api/admin/departments/primary/{primary_id}/status", include_v1=False),
    _paths("/api/admin/departments/primary/{primary_id}/direct-users", include_v1=False),
    _paths("/api/admin/departments/secondary", include_v1=False),
    _paths("/api/admin/departments/secondary/{secondary_id}", include_v1=False),
    _paths("/api/admin/departments/secondary/{secondary_id}/status", include_v1=False),
    _paths("/api/admin/departments/secondary/{secondary_id}/direct-users", include_v1=False),
    _paths("/api/admin/departments/secondary/{secondary_id}/users", include_v1=False),
    _paths("/api/admin/departments/secondary/{secondary_id}/legacy-users", include_v1=False),
    _paths("/api/admin/departments/tertiary", include_v1=False),
    _paths("/api/admin/departments/tertiary/{tertiary_id}", include_v1=False),
    _paths("/api/admin/departments/tertiary/{tertiary_id}/status", include_v1=False),
    _paths("/api/admin/departments/tertiary/{tertiary_id}/users", include_v1=False),
    _paths("/api/admin/departments/batch-delete", include_v1=False),
    _paths("/api/admin/departments/batch-status", include_v1=False),
    _paths("/api/admin/departments/{level}/{department_id}/force-delete", include_v1=False),
    _paths("/api/admin/departments/batch-force-delete", include_v1=False),
    _paths("/api/admin/departments/batch-import", include_v1=False),
    _paths("/api/admin/departments/import-template", include_v1=False),
    _paths("/api/activity/heartbeat"),
    _paths("/api/admin/usage-stats", include_v1=False),
    _paths("/api/admin/usage-stats/export", include_v1=False),
)

PUBLIC_ROUTE_PATTERNS = tuple(path for group in _PUBLIC_ROUTE_GROUPS for path in group)

_QA_ROUTE_GROUPS = (
    _mode_paths("ask"),
    _mode_paths("ask_stream"),
    _paths("/api/patent_search"),
)

QA_ROUTE_PATTERNS = tuple(path for group in _QA_ROUTE_GROUPS for path in group)
