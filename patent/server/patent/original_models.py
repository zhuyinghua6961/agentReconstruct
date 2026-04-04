from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


OriginalSection = Literal["abstract", "claim", "description", "figure", "fulltext"]
OriginalFormat = Literal["html", "json", "text", "redirect"]


@dataclass(frozen=True)
class OriginalRequest:
    canonical_patent_id: str
    section: OriginalSection
    claim_number: int | None
    paragraph_id: str | None
    response_format: OriginalFormat
    anchor: str


@dataclass(frozen=True)
class OriginalViewResult:
    kind: Literal["content", "redirect"]
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    payload: dict[str, object] | str | None = None
    redirect_url: str | None = None

