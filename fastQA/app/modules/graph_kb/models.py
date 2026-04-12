from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GraphKbDecision:
    decision: str
    reason: str
    standalone: bool
    signals: tuple[str, ...] = ()


@dataclass(frozen=True)
class GraphKbQueryPlan:
    template_id: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphKbExecutionResult:
    handled: bool
    answer: str = ""
    references: tuple[str, ...] = ()
    query_mode: str = "graph_kb"
    template_id: str = ""
    result_count: int = 0
    latency_ms: float = 0.0
    fallback_reason: str = ""

