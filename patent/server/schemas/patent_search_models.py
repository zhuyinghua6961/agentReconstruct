from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PatentSearchRequest(BaseModel):
    query: str = Field(default="")
    query_type: Literal["auto", "patent_id", "topic"] = Field(default="auto")
    sources: Literal["abstract", "chunk", "both"] = Field(default="both")
    limit: int = Field(default=20, ge=1, le=50)
