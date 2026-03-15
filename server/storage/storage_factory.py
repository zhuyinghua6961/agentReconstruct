"""Storage backend factory with local fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from server.storage.base import StorageBackend
from server.storage.local_backend import LocalStorageBackend
from server.storage.minio_backend import MinIOStorageBackend

_backend_instance: Optional[StorageBackend] = None


def get_storage_backend(*, project_root: str | None = None) -> StorageBackend:
    """Get process singleton backend. MinIO preferred, local fallback."""
    global _backend_instance
    if _backend_instance is not None:
        return _backend_instance

    root = Path(project_root or Path(__file__).resolve().parents[2])
    local = LocalStorageBackend(root_dir=str(root))

    try:
        _backend_instance = MinIOStorageBackend()
    except Exception:
        _backend_instance = local

    return _backend_instance
