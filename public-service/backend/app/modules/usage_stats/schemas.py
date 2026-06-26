from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


class HeartbeatRequest(BaseModel):
    session_id: str = Field(default="", max_length=64)
    finalize: bool = False
    last_interaction_at: str | None = None


class InternalActivityRecordRequest(BaseModel):
    user_id: int = Field(gt=0)
    event_type: str
    trace_id: str | None = None
    conversation_id: int | None = None
    metadata: dict[str, Any] | None = None
