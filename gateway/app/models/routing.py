"""Routing decision models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ModeName = Literal["fast", "thinking", "patent"]
RouteName = Literal["kb_qa", "pdf_qa", "tabular_qa", "hybrid_qa"]
TurnMode = Literal["kb_only", "file_only", "mixed"]


@dataclass(frozen=True)
class FileContextDecision:
    route: RouteName = "kb_qa"
    turn_mode: TurnMode = "kb_only"
    allow_kb_verification: bool = False
    needs_clarification: bool = False
    clarification_message: str = ""
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
