"""Shared normalization helpers for conversation file metadata."""

from __future__ import annotations

from typing import Any

from app.models.files import ConversationFileRow


def normalize_conversation_file_rows(rows: list[dict[str, Any]] | None) -> list[ConversationFileRow]:
    result: list[ConversationFileRow] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        file_id = _to_int(row.get("file_id") or row.get("id"))
        if file_id <= 0:
            continue
        result.append(
            ConversationFileRow(
                file_id=file_id,
                file_type=str(row.get("file_type") or "").strip().lower(),
                file_name=str(row.get("file_name") or "").strip(),
                file_status=str(row.get("file_status") or "active").strip().lower(),
                parse_status=str(row.get("parse_status") or "").strip().lower(),
                index_status=str(row.get("index_status") or "").strip().lower(),
                processing_stage=str(row.get("processing_stage") or "").strip().lower(),
                local_path=str(row.get("local_path") or "").strip(),
                storage_ref=str(row.get("storage_ref") or "").strip(),
                file_meta=row.get("file_meta") if isinstance(row.get("file_meta"), dict) else {},
                file_no=_to_int(row.get("file_no")),
                display_no=_to_int(row.get("display_no")),
            )
        )
    result.sort(key=lambda item: (item.display_no or 10**9, item.file_no or 10**9, item.file_id))
    return result


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
