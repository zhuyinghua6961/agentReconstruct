from __future__ import annotations

from pathlib import Path
from typing import Any

from server.patent.file_models import PatentExecutionFile, PatentFileContract


_ROUTE_TO_ALLOWED_SCOPES = {
    "pdf_qa": {"pdf"},
    "tabular_qa": {"table"},
    "hybrid_qa": {"pdf+kb", "table+kb", "pdf+table", "pdf+table+kb"},
}

_PDF_TYPES = {"pdf"}
_TABLE_TYPES = {"csv", "excel", "table", "xls", "xlsx", "xlsm"}


def build_patent_file_contract(
    *,
    question: str = "",
    route: str,
    source_scope: str,
    selected_file_ids: list[int],
    primary_file_id: int | None,
    execution_files: list[dict[str, Any]],
    file_selection: dict[str, Any] | None,
    kb_enabled: bool,
    allow_kb_verification: bool,
) -> PatentFileContract:
    normalized_route = str(route or "").strip()
    allowed_scopes = _ROUTE_TO_ALLOWED_SCOPES.get(normalized_route)
    if allowed_scopes is None:
        raise ValueError(f"unsupported file route: {route}")

    normalized_scope = str(source_scope or "").strip()
    if normalized_scope not in allowed_scopes:
        raise ValueError(f"source_scope is not valid for {normalized_route}")

    normalized_selected = [_require_strict_int(file_id, field="selected_file_ids") for file_id in selected_file_ids or []]
    if not normalized_selected:
        raise ValueError("selected_file_ids must not be empty")
    selected_id_set = set(normalized_selected)

    execution_items = [item for item in execution_files or [] if isinstance(item, dict)]
    normalized_execution_ids = {
        _require_strict_int(item.get("file_id"), field="execution_files.file_id")
        for item in execution_items
        if item.get("file_id") is not None
    }
    if not execution_items:
        raise ValueError("execution_files must not be empty")

    if any(file_id not in normalized_execution_ids for file_id in normalized_selected):
        raise ValueError("selected_file_ids must be present in execution_files")

    normalized_files = [
        _normalize_execution_file(item)
        for item in execution_items
        if _require_strict_int(item.get("file_id"), field="execution_files.file_id") in selected_id_set
    ]

    normalized_primary_file_id = None if primary_file_id is None else _require_strict_int(primary_file_id, field="primary_file_id")
    if normalized_primary_file_id is not None and normalized_primary_file_id not in normalized_selected:
        raise ValueError("primary_file_id must belong to selected_file_ids")

    selected_files = [item for item in normalized_files if item.file_id in normalized_selected]
    families = {item.family for item in selected_files}
    scope_tokens = set(normalized_scope.split("+"))
    expected_families = {token for token in scope_tokens if token in {"pdf", "table"}}
    if "pdf" in scope_tokens and "pdf" not in families:
        raise ValueError("source_scope requires pdf files")
    if "table" in scope_tokens and "table" not in families:
        raise ValueError("source_scope requires table files")
    if families != expected_families:
        raise ValueError("selected files must match source_scope exactly")

    includes_kb = "kb" in scope_tokens
    if bool(kb_enabled) != includes_kb:
        raise ValueError("kb_enabled must match source_scope")
    if bool(allow_kb_verification) != includes_kb:
        raise ValueError("allow_kb_verification must match source_scope")
    if bool(allow_kb_verification) and not includes_kb:
        raise ValueError("allow_kb_verification requires a kb-enabled source_scope")

    return PatentFileContract(
        question=str(question or "").strip(),
        route=normalized_route,
        source_scope=normalized_scope,
        selected_file_ids=normalized_selected,
        primary_file_id=normalized_primary_file_id,
        execution_files=normalized_files,
        file_selection=dict(file_selection or {}),
        kb_enabled=bool(kb_enabled),
        allow_kb_verification=bool(allow_kb_verification),
    )


def _normalize_execution_file(value: dict[str, Any]) -> PatentExecutionFile:
    if not isinstance(value, dict):
        raise ValueError("execution_files items must be objects")

    file_id = _require_strict_int(value.get("file_id"), field="execution_files.file_id")
    file_type = str(value.get("file_type") or "").strip().lower()
    if file_type in _PDF_TYPES:
        family = "pdf"
    elif file_type in _TABLE_TYPES:
        family = "table"
    else:
        raise ValueError(f"unsupported file_type: {file_type}")

    payload = dict(value)
    _validate_table_payload(file_type=file_type, payload=payload)
    file_name = str(payload.get("file_name") or "")
    return PatentExecutionFile(
        file_id=file_id,
        file_type=file_type,
        file_name=file_name,
        payload=payload,
        family=family,
    )


def _require_strict_int(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be integers")
    return int(value)


def _validate_table_payload(*, file_type: str, payload: dict[str, Any]) -> None:
    if file_type not in {"excel", "table"}:
        return
    local_path = str(payload.get("local_path") or "").strip()
    file_name = str(payload.get("file_name") or "").strip()
    suffix = Path(local_path).suffix.lower() or Path(file_name).suffix.lower()
    if suffix and suffix not in {".csv", ".xls", ".xlsx", ".xlsm"}:
        raise ValueError(f"unsupported spreadsheet extension: {suffix}")
