from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings
from app.integrations.storage.base import StorageBackend
from app.integrations.storage.local import LocalStorageBackend
from app.integrations.storage.minio import MinIOStorageBackend


_backend_instance: StorageBackend | None = None


def get_storage_backend(*, project_root: str | None = None, force_new: bool = False) -> StorageBackend:
    global _backend_instance
    if _backend_instance is not None and not force_new:
        return _backend_instance

    root = Path(project_root or get_settings().local_storage_root).resolve()
    local = LocalStorageBackend(root_dir=str(root))
    settings = get_settings()
    if not (settings.minio_endpoint and settings.minio_access_key and settings.minio_secret_key):
        if not force_new:
            _backend_instance = local
        return local

    try:
        backend: StorageBackend = MinIOStorageBackend(
            endpoint=settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            bucket=settings.minio_bucket,
            secure=settings.minio_secure,
            region=settings.minio_region,
        )
    except Exception:
        backend = local

    if not force_new:
        _backend_instance = backend
    return backend
