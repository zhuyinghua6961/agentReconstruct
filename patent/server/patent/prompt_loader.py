from __future__ import annotations

from functools import lru_cache
from pathlib import Path


_PROMPT_ROOT = Path(__file__).resolve().parent / "prompts"


@lru_cache(maxsize=None)
def load_patent_prompt_template(name: str) -> str:
    normalized = str(name or "").strip()
    if not normalized:
        raise ValueError("prompt template name is required")
    if "/" in normalized or "\\" in normalized or normalized in {".", ".."}:
        raise ValueError(f"invalid prompt template name: {normalized!r}")
    return _PROMPT_ROOT.joinpath(normalized).read_text(encoding="utf-8")
