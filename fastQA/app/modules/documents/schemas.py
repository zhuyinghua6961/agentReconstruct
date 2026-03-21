from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ReferencePreviewRequest(BaseModel):
    doi: str | list[str] | None = None
    dois_text: str = ""
    doi_list: list[str] = Field(default_factory=list)
    max_items: int | None = None

    def resolved_doi_list(self) -> list[str]:
        values = list(self.doi_list)
        raw = self.doi
        if isinstance(raw, str) and raw.strip():
            values.append(raw.strip())
        elif isinstance(raw, list):
            values.extend(str(item or "").strip() for item in raw if str(item or "").strip())
        return values


class TranslateRequest(BaseModel):
    texts: list[Any] = Field(default_factory=list)
