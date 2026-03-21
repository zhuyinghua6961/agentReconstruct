from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.config import SERVICE_ASSET_ROOT

DEFAULT_PROMPT_FILES: tuple[str, ...] = (
    "system_prompt.txt",
    "system_prompt_v2.txt",
    "system_prompt_old.txt",
    "synthesis_prompt.txt",
    "synthesis_prompt_v3.txt",
    "semantic_synthesis_prompt.txt",
    "semantic_synthesis_prompt_v2.txt",
    "broad_question_synthesis_prompt.txt",
    "hybrid_synthesis_prompt.txt",
)

DEFAULT_PROMPT_ROOT = Path(SERVICE_ASSET_ROOT) / "prompts"
LEGACY_PROMPT_ROOT = Path(__file__).resolve().parents[2] / "prompts"


def resolve_prompt_root() -> Path:
    if DEFAULT_PROMPT_ROOT.exists():
        return DEFAULT_PROMPT_ROOT.resolve()
    return LEGACY_PROMPT_ROOT.resolve()


def resolve_prompt_path(filename: str) -> Path:
    clean_name = Path(str(filename or "")).name
    candidates = [resolve_prompt_root() / clean_name]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    raise FileNotFoundError(f"prompt file not found: {clean_name}")


def load_prompt_text(filename: str, logger: Any | None = None) -> str:
    path = resolve_prompt_path(filename)
    with path.open("r", encoding="utf-8") as fh:
        prompt = fh.read()
    if logger is not None:
        logger.info("提示词文件 %s 加载成功: %s", path.name, path)
    return prompt
