"""Upload helper to mirror local files to object storage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from server.storage.storage_factory import get_storage_backend


def mirror_file_to_object_storage(
    *,
    local_path: str,
    object_name: str,
    content_type: str | None,
    project_root: str,
    logger: Any,
) -> str | None:
    """Mirror a local file to configured object storage."""
    path = Path(local_path)
    if not path.exists() or not path.is_file():
        return None

    try:
        backend = get_storage_backend(project_root=project_root)
        return backend.upload_file(
            local_path=str(path),
            object_name=object_name,
            content_type=content_type,
        )
    except Exception as exc:  # pragma: no cover - runtime env specific
        logger.warning("object storage mirror skipped: %s", exc)
        return None
