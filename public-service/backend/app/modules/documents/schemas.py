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


class PatentOriginalFigureObjectSet(BaseModel):
    primary_object: str | None = Field(default=None)
    ordered_objects: list[str] = Field(default_factory=list)


class PatentOriginalObjects(BaseModel):
    structured: dict[str, str] = Field(default_factory=dict)
    figures: dict[str, PatentOriginalFigureObjectSet] = Field(default_factory=dict)
    fulltext_pdf: str | None = Field(default=None)


class PatentOriginalManifest(BaseModel):
    canonical_patent_id: str
    title: str
    provider: str
    original_version: str
    country: str
    kind_code: str
    publication_number: str
    application_number: str
    objects: PatentOriginalObjects
    availability: dict[str, bool]


class PatentOriginalResolvedSection(BaseModel):
    canonical_patent_id: str
    section: str
    original_version: str
    section_label: str | None = Field(default=None)
    content: Any | None = Field(default=None)
    anchor_hit: bool = Field(default=False)
    claim_number: int | None = Field(default=None)
    paragraph_id: str | None = Field(default=None)
    figure_source: str | None = Field(default=None)
    served_object_key: str | None = Field(default=None)
    object_key: str | None = Field(default=None)
    media_type: str | None = Field(default=None)
