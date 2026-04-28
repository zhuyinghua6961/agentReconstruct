from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

try:
    from dotenv import dotenv_values, load_dotenv
except Exception:  # pragma: no cover
    dotenv_values = None
    load_dotenv = None


SERVICE_CODE = "FASTQA"
SERVICE_NAME = "fastQA"
WORKSPACE_DIR = Path(__file__).resolve().parents[3]
DEFAULT_ENV_FILENAMES = ("config.env", "config.shared.env", "config.secret.env", ".env")
SHARED_CONFIG_FILENAMES = (
    "infrastructure.shared.env",
    "model-endpoints.shared.env",
    "infrastructure.secret.env",
)
LEGACY_ENV_FILE = WORKSPACE_DIR / "config.env"
SHARED_ENV_FILE = WORKSPACE_DIR / "config.shared.env"
SECRET_ENV_FILE = WORKSPACE_DIR / "config.secret.env"
SECRET_ENV_TEMPLATE_FILE = WORKSPACE_DIR / "config.secret.env.example"
DOTENV_FILE = WORKSPACE_DIR / ".env"
ENV_FILE_CANDIDATES = (LEGACY_ENV_FILE, SHARED_ENV_FILE, SECRET_ENV_FILE, DOTENV_FILE)
EXPLICIT_ENV_KEYS = (
    f"{SERVICE_CODE}_ENV_FILE",
    f"{SERVICE_CODE}_ENV_FILES",
    "SERVICE_ENV_FILE",
    "SERVICE_ENV_FILES",
)
CONFIG_ROOT_KEYS = (
    f"{SERVICE_CODE}_SERVICE_CONFIG_ROOT",
    "SERVICE_CONFIG_ROOT",
)


def _read_env(key: str) -> str | None:
    raw = str(os.getenv(key, "") or "").strip()
    return raw or None


def _resolve_path(raw: str, *, base: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (base / path).resolve()
    else:
        path = path.resolve()
    return path


def resolve_resource_root() -> Path | None:
    raw = _read_env("RESOURCE_ROOT")
    if raw is not None:
        return _resolve_path(raw, base=WORKSPACE_DIR)
    auto_candidate = (WORKSPACE_DIR / "resource").resolve()
    if auto_candidate.exists() and auto_candidate.is_dir():
        return auto_candidate
    return None


def resolve_service_root(kind: str) -> Path:
    normalized = str(kind or "").strip().upper()
    if normalized not in {"CONFIG", "STATE", "RUNTIME", "ASSET"}:
        raise ValueError(f"unsupported service root kind: {kind}")

    resource_root = resolve_resource_root()
    explicit = _read_env(f"{SERVICE_CODE}_SERVICE_{normalized}_ROOT") or _read_env(f"SERVICE_{normalized}_ROOT")
    if explicit is not None:
        return _resolve_path(explicit, base=resource_root or WORKSPACE_DIR)

    if resource_root is not None:
        if normalized == "CONFIG":
            return (resource_root / "config" / "services" / SERVICE_NAME).resolve()
        if normalized == "STATE":
            return (resource_root / "state" / "dev" / SERVICE_NAME).resolve()
        if normalized == "RUNTIME":
            return (resource_root / "runtime" / "dev" / SERVICE_NAME).resolve()
        return (resource_root / "assets").resolve()

    if normalized == "RUNTIME":
        return (WORKSPACE_DIR / ".runtime").resolve()
    return WORKSPACE_DIR.resolve()


def _iter_explicit_env_values() -> Iterable[str]:
    for key in EXPLICIT_ENV_KEYS:
        raw = str(os.getenv(key, "") or "").strip()
        if not raw:
            continue
        if key.endswith("FILES"):
            normalized = raw.replace(",", os.pathsep)
            for item in normalized.split(os.pathsep):
                value = item.strip()
                if value:
                    yield value
            continue
        yield raw


def _resolve_config_root() -> Path | None:
    for key in CONFIG_ROOT_KEYS:
        raw = str(os.getenv(key, "") or "").strip()
        if not raw:
            continue
        return _resolve_path(raw, base=resolve_resource_root() or WORKSPACE_DIR)

    resource_root = resolve_resource_root()
    if resource_root is not None:
        return (resource_root / "config" / "services" / SERVICE_NAME).resolve()
    return None


def _iter_resource_shared_env_files() -> tuple[Path, ...]:
    resource_root = resolve_resource_root()
    if resource_root is None:
        return ()
    shared_root = resource_root / "config" / "shared"
    return tuple((shared_root / filename).resolve() for filename in SHARED_CONFIG_FILENAMES)


def iter_workspace_env_files() -> tuple[Path, ...]:
    values: list[Path] = []
    seen: set[Path] = set()

    for raw in _iter_explicit_env_values():
        candidate = _resolve_path(raw, base=Path.cwd())
        if candidate in seen:
            continue
        seen.add(candidate)
        values.append(candidate)
    if values:
        return tuple(values)

    config_root = _resolve_config_root()
    if config_root is not None:
        candidates = tuple((config_root / filename).resolve() for filename in DEFAULT_ENV_FILENAMES)
        if any(path.exists() for path in candidates):
            merged: list[Path] = []
            for path in (*_iter_resource_shared_env_files(), *candidates, *ENV_FILE_CANDIDATES):
                if path in seen:
                    continue
                seen.add(path)
                merged.append(path)
            return tuple(merged)

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
    paths = tuple(path for path in iter_workspace_env_files() if path.exists())
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
