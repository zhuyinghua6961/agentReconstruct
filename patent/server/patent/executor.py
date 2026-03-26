from __future__ import annotations

from typing import Any

from server.patent.pipeline import build_stub_patent_result
from server.schemas.request_models import PatentAskRequest
from server.services.mode_profiles import PatentModeProfile, get_patent_mode_profile


class PatentExecutor:
    def __init__(self, *, mode_profile: PatentModeProfile | None = None) -> None:
        self._mode_profile = mode_profile or get_patent_mode_profile()

    def execute(self, *, request: PatentAskRequest, context: dict[str, Any] | None = None) -> dict[str, Any]:
        return build_stub_patent_result(
            request=request,
            context=context,
            profile=self._mode_profile,
        )
