from __future__ import annotations

from typing import Any, TypedDict


class NormalizedFileRow(TypedDict):
    file_id: int
    file_no: int
    display_no: int
    file_type: str
    file_name: str
    file_status: str
    local_path: str
    storage_ref: str
    parse_status: str
    index_status: str
    processing_stage: str
    last_error: str
    deleted_at: Any
    deleted_by: Any
    file_meta: dict[str, Any]


class FileSelectionCandidate(TypedDict):
    file_id: int
    file_no: int
    display_no: int
    file_type: str
    file_name: str


class UsedFilePayload(TypedDict):
    file_id: int
    file_no: int
    display_no: int
    file_type: str
    file_name: str
    selected_reason: str
    source: str
    parse_status: str
    index_status: str
    processing_stage: str
    last_error: str


class ExecutionFilePayload(UsedFilePayload):
    local_path: str
    storage_ref: str
    file_meta: dict[str, Any]


class OrdinalRefs(TypedDict):
    direct_indexes: list[int]
    front_count: int
    back_count: int
    reverse_indexes: list[int]
    ambiguous_values: list[int]
    has_ordinal: bool
    has_ambiguous: bool


class FileContextResult(TypedDict):
    strategy: str
    file_intent: bool
    needs_clarification: bool
    clarification_message: str
    clarify_candidates: list[FileSelectionCandidate]
    explicit_file_ids: list[int]
    selected_file_ids: list[int]
    used_files: list[UsedFilePayload]
    execution_files: list[ExecutionFilePayload]
    ready_file_ids: list[int]
    pending_file_ids: list[int]
    failed_file_ids: list[int]
    selected_has_pdf: bool
    selected_has_table: bool
    primary_pdf_path: str | None
    primary_table_path: str | None
    route_hint: str
    question_mode: str
    turn_mode: str
    allow_kb_verification: bool
    selection_semantic: str
