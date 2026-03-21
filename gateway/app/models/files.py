"""Conversation file metadata models used by the gateway."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ConversationFileRow:
    file_id: int
    file_type: str
    file_name: str = ""
    file_status: str = "active"
    parse_status: str = ""
    index_status: str = ""
    processing_stage: str = ""
    local_path: str = ""
    storage_ref: str = ""
    file_meta: dict[str, Any] = field(default_factory=dict)
    file_no: int = 0
    display_no: int = 0

    @property
    def is_deleted(self) -> bool:
        return str(self.file_status or "active").strip().lower() == "deleted"

    @property
    def is_table(self) -> bool:
        return str(self.file_type or "").strip().lower() in {"excel", "csv", "table"}

    @property
    def is_pdf(self) -> bool:
        return str(self.file_type or "").strip().lower() == "pdf"

    @property
    def is_ready(self) -> bool:
        parse_status = str(self.parse_status or "").strip().lower()
        index_status = str(self.index_status or "").strip().lower()
        stage = str(self.processing_stage or "").strip().lower()
        if self.is_deleted:
            return False
        if parse_status == "failed" or index_status == "failed" or stage == "failed":
            return False
        if index_status == "ready" or stage == "ready":
            return True
        if not parse_status and not index_status and not stage:
            return True
        return parse_status in {"parsed", "ready"}
