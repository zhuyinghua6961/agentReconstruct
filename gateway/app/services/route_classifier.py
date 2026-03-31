from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ClassifierDecision:
    route: str
    turn_mode: str
    source_scope: str
    confidence: float
    reason_codes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ClassifierThresholdPolicy:
    high_confidence: float = 0.8
    medium_confidence: float = 0.6

    def should_apply(self, *, decision: ClassifierDecision, conflicts_with_rule: bool) -> bool:
        confidence = float(decision.confidence)
        if confidence >= self.high_confidence:
            return True
        if confidence < self.medium_confidence:
            return False
        if conflicts_with_rule:
            return False
        return decision.route == "kb_qa"


class RouteClassifier(Protocol):
    def classify(self, **kwargs: Any) -> ClassifierDecision | None:
        ...


class NoopRouteClassifier:
    def classify(self, **kwargs: Any) -> ClassifierDecision | None:
        return None
