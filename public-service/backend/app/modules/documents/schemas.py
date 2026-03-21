from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class TranslateRequest(BaseModel):
    texts: list[Any] = Field(default_factory=list)


class ReferencePreviewRequest(BaseModel):
    dois_text: str = Field(default="")
    doi_list: list[str] = Field(default_factory=list)
    max_items: int | None = Field(default=None)

    @model_validator(mode="before")
    @classmethod
    def _normalize_compat_fields(cls, data: Any):
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        if normalized.get("doi_list"):
            return normalized

        compat_value = normalized.get("doi")
        if compat_value is None:
            compat_value = normalized.get("dois")

        if isinstance(compat_value, str):
            cleaned = compat_value.strip()
            normalized["doi_list"] = [cleaned] if cleaned else []
            return normalized

        if isinstance(compat_value, list):
            normalized["doi_list"] = [str(item).strip() for item in compat_value if str(item or "").strip()]
            return normalized

        return normalized
