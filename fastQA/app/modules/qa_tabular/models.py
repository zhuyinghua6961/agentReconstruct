from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TabularQaRequest:
    question: str
    file_items: list[dict[str, Any]] = field(default_factory=list)
    route_hint: str = "tabular_qa"
    allow_kb_verification: bool = False
