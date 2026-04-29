from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

try:
    from dotenv import dotenv_values, load_dotenv
except Exception:  # pragma: no cover
    dotenv_values = None
    load_dotenv = None


SERVICE_NAME = "public-service"
WORKSPACE_DIR = Path(__file__).resolve().parents[4]
SERVICE_DIR = WORKSPACE_DIR / "public-service"
DEFAULT_ENV_FILENAMES = ("config.shared.env", "config.secret.env", ".env", "config.env")
SHARED_CONFIG_FILENAMES = (
    "infrastructure.shared.env",
    "model-endpoints.shared.env",
    "infrastructure.secret.env",
    "model-endpoints.secret.env",
    "graph.shared.env",
    "graph.secret.env",
)
_EXPLICIT_ENV_KEYS = ("PUBLIC_SERVICE_ENV_FILE", "PUBLIC_SERVICE_ENV_FILES")
_LEGACY_ENV_FILES = (
    SERVICE_DIR / "config.shared.env",
    SERVICE_DIR / "config.secret.env",
    SERVICE_DIR / ".env",
)


def _resolve_path(raw: str, *, base: Path) -> Path:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (base / path).resolve()


def resolve_resource_root() -> Path | None:
    raw = str(os.getenv("RESOURCE_ROOT", "") or "").strip()
    if raw:
        return _resolve_path(raw, base=WORKSPACE_DIR)
    candidate = (WORKSPACE_DIR / "resource").resolve()
    if candidate.exists() and candidate.is_dir():
        return candidate
    return None


def _iter_resource_shared_env_files() -> tuple[Path, ...]:
    resource_root = resolve_resource_root()
    if resource_root is None:
        return ()
    shared_root = resource_root / "config" / "shared"
    return tuple((shared_root / filename).resolve() for filename in SHARED_CONFIG_FILENAMES)


def _iter_resource_service_env_files() -> tuple[Path, ...]:
    resource_root = resolve_resource_root()
    if resource_root is None:
        return ()
    service_root = resource_root / "config" / "services" / SERVICE_NAME
    return tuple((service_root / filename).resolve() for filename in DEFAULT_ENV_FILENAMES)


def _iter_explicit_env_values() -> Iterable[str]:
    for key in _EXPLICIT_ENV_KEYS:
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


def iter_env_files() -> tuple[Path, ...]:
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

    resource_root = resolve_resource_root()
    if resource_root is not None:
        for candidate in (*_LEGACY_ENV_FILES, *_iter_resource_shared_env_files(), *_iter_resource_service_env_files()):
            if candidate in seen:
                continue
            seen.add(candidate)
            values.append(candidate)
        return tuple(values)

    should_load_dotenv = str(os.getenv("PUBLIC_SERVICE_LOAD_DOTENV", "0") or "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not should_load_dotenv:
        return ()

    for filename in (".env",):
        candidate = (Path.cwd() / filename).resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        values.append(candidate)
    return tuple(values)


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


def load_env(*, override_existing: bool = False) -> tuple[Path, ...]:
    paths = tuple(path for path in iter_env_files() if path.exists())
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
