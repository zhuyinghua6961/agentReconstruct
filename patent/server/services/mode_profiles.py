from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PatentModeProfile:
    requested_mode: str = "patent"
    actual_mode: str = "patent"
    route: str = "kb_qa"
    query_mode: str = "patent"
    turn_mode: str = "kb_only"
    chunk_size: int = 4000


_DEFAULT_PROFILE = PatentModeProfile()



def get_patent_mode_profile() -> PatentModeProfile:
    return _DEFAULT_PROFILE
