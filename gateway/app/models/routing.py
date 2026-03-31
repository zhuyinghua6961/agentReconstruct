"""Routing decision models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ModeName = Literal["fast", "thinking", "patent"]
RouteName = Literal["kb_qa", "pdf_qa", "tabular_qa", "hybrid_qa"]
TurnMode = Literal["kb_only", "file_only", "mixed"]
SourceScope = Literal["kb", "pdf", "table", "pdf+kb", "table+kb", "pdf+table", "pdf+table+kb"]


@dataclass(frozen=True)
class FileContextDecision:
    route: RouteName = "kb_qa"
    turn_mode: TurnMode = "kb_only"
    allow_kb_verification: bool = False
    needs_clarification: bool = False
    clarification_message: str = ""
    status_code: str = ""
    status_error: str = ""
    status_message: str = ""
    status_retriable: bool = False
    status_detail: dict[str, Any] = field(default_factory=dict)
    clarify_candidates: list[dict[str, Any]] = field(default_factory=list)
    classifier_used: bool = False
    classifier_confidence: float = 0.0
    classifier_reason_codes: list[str] = field(default_factory=list)
    selected_file_ids: list[int] = field(default_factory=list)
    used_files: list[dict[str, Any]] = field(default_factory=list)
    execution_files: list[dict[str, Any]] = field(default_factory=list)
    strategy: str = "none"


@dataclass(frozen=True)
class RouteDecision:
    requested_mode: ModeName
    actual_mode: ModeName
    route: RouteName
    turn_mode: TurnMode
    allow_kb_verification: bool
    needs_clarification: bool
    clarification_message: str
    status_code: str = ""
    status_error: str = ""
    status_message: str = ""
    status_retriable: bool = False
    status_detail: dict[str, Any] = field(default_factory=dict)
    clarify_candidates: list[dict[str, Any]] = field(default_factory=list)
    source_scope: SourceScope | None = None
    kb_enabled: bool = False
    selected_file_ids: list[int] = field(default_factory=list)
    execution_files: list[dict[str, Any]] = field(default_factory=list)
    strategy: str = "none"
    primary_file_id: int | None = None
    file_selection: dict[str, Any] = field(default_factory=dict)
    route_reasons: list[str] = field(default_factory=list)
    route_confidence: float = 1.0
    classifier_used: bool = False
