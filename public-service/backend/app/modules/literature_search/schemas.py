from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LiteratureSearchRequest(BaseModel):
    query: str = Field(default="")
    query_type: Literal["auto", "doi", "title"] = Field(default="auto")
    match_mode: Literal["semantic", "fuzzy", "exact"] = Field(default="semantic")
    sources: Literal["fastqa", "fastqa_md", "highthinking", "both"] = Field(default="both")
    limit: int = Field(default=20, ge=1, le=50)
