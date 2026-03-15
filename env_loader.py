from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

try:
    from dotenv import dotenv_values, load_dotenv
except Exception:  # pragma: no cover
    dotenv_values = None
    load_dotenv = None


WORKSPACE_DIR = Path(__file__).resolve().parent
LEGACY_ENV_FILE = WORKSPACE_DIR / "config.env"
SHARED_ENV_FILE = WORKSPACE_DIR / "config.shared.env"
SECRET_ENV_FILE = WORKSPACE_DIR / "config.secret.env"
SECRET_ENV_TEMPLATE_FILE = WORKSPACE_DIR / "config.secret.env.example"
DOTENV_FILE = WORKSPACE_DIR / ".env"
ENV_FILE_CANDIDATES = (
    LEGACY_ENV_FILE,
    SHARED_ENV_FILE,
    SECRET_ENV_FILE,
    DOTENV_FILE,
)


def iter_workspace_env_files() -> tuple[Path, ...]:
    return ENV_FILE_CANDIDATES


def _collect_values(paths: Iterable[Path]) -> dict[str, str]:
    merged: dict[str, str] = {}
    if dotenv_values is None:
        return merged
    for path in paths:
        if not path.exists():
            continue
        payload = dotenv_values(path)
        for key, value in payload.items():
            if not key or value is None:
                continue
            merged[str(key)] = str(value)
    return merged


def load_workspace_env(*, override_existing: bool = False) -> tuple[Path, ...]:
    paths = tuple(path for path in ENV_FILE_CANDIDATES if path.exists())
    if not paths:
        return ()

    if dotenv_values is not None:
        merged = _collect_values(paths)
        for key, value in merged.items():
            if override_existing or key not in os.environ:
                os.environ[key] = value
        return paths

    if load_dotenv is not None:
        for path in paths:
            load_dotenv(path, override=override_existing)
    return paths
