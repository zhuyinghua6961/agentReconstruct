from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PatentGraphKbDecision:
    decision: str
    reason: str
    standalone: bool
    signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class PatentGraphKbQueryPlan:
    template_id: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PatentGraphKbExecutionResult:
    handled: bool
    answer: str = ""
    references: tuple[str, ...] = ()
    reference_objects: tuple[dict[str, Any], ...] = ()
    query_mode: str = "patent_graph_kb"
    template_id: str = ""
    result_count: int = 0
    latency_ms: float = 0.0
    fallback_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
