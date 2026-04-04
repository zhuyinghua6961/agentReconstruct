from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


PatentFileRouteName = Literal["pdf_qa", "tabular_qa", "hybrid_qa"]
PatentFileSourceScope = Literal["pdf", "table", "pdf+kb", "table+kb", "pdf+table", "pdf+table+kb"]
PatentFileHandler = Literal["pdf", "tabular", "hybrid"]
PatentFileFamily = Literal["pdf", "table"]


@dataclass(frozen=True)
class PatentExecutionFile:
    file_id: int
    file_type: str
    file_name: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    family: PatentFileFamily = "pdf"

    def as_payload(self) -> dict[str, Any]:
        payload = {
            "file_id": self.file_id,
            "file_type": self.file_type,
        }
        if self.file_name:
            payload["file_name"] = self.file_name
        return payload


@dataclass(frozen=True)
class PatentFileContract:
    route: PatentFileRouteName
    source_scope: PatentFileSourceScope
    selected_file_ids: list[int]
    primary_file_id: int | None
    execution_files: list[PatentExecutionFile]
    file_selection: dict[str, Any]
    kb_enabled: bool
    allow_kb_verification: bool
    question: str = ""

    @property
    def includes_kb(self) -> bool:
        return "kb" in self.source_scope.split("+")

    @property
    def selected_execution_files(self) -> list[PatentExecutionFile]:
        selected_ids = set(self.selected_file_ids)
        selected = [item for item in self.execution_files if item.file_id in selected_ids]
        if self.primary_file_id is None:
            return selected
        primary = [item for item in selected if item.file_id == self.primary_file_id]
        others = [item for item in selected if item.file_id != self.primary_file_id]
        return [*primary, *others]


@dataclass(frozen=True)
class PatentFileRoutePlan:
    route: PatentFileRouteName
    source_scope: PatentFileSourceScope
    handler: PatentFileHandler
    file_families: tuple[PatentFileFamily, ...]
    include_kb: bool
