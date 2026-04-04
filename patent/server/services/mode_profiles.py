from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class PatentModeProfile:
    requested_mode: str = "patent"
    actual_mode: str = "patent"
    route: str = "kb_qa"
    query_mode: str = "patent_kb_qa"
    turn_mode: str = "kb_only"
    chunk_size: int = 4000


_MODE_PROFILES: dict[str, PatentModeProfile] = {
    "kb_qa": PatentModeProfile(
        route="kb_qa",
        query_mode="patent_kb_qa",
        turn_mode="kb_only",
    ),
    "pdf_qa": PatentModeProfile(
        route="pdf_qa",
        query_mode="patent_pdf_qa",
        turn_mode="file_only",
    ),
    "tabular_qa": PatentModeProfile(
        route="tabular_qa",
        query_mode="patent_tabular_qa",
        turn_mode="file_only",
    ),
    "hybrid_qa": PatentModeProfile(
        route="hybrid_qa",
        query_mode="patent_hybrid_qa",
        turn_mode="mixed",
    ),
}

_DEFAULT_PROFILE = _MODE_PROFILES["kb_qa"]



def get_patent_mode_profile(route: str = "kb_qa") -> PatentModeProfile:
    return _MODE_PROFILES.get(str(route or "").strip(), _DEFAULT_PROFILE)
