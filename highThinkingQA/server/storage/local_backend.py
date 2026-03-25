"""Local storage backend (fallback)."""

# Compatibility note: retained for legacy conversation-json/object-storage support
# and older file-flow helpers. Current highThinkingQA runtime uses public-service
# for conversation persistence, and file HTTP surfaces are no longer served here.


from __future__ import annotations

import shutil
from pathlib import Path

from server.storage.base import StorageBackend


class LocalStorageBackend(StorageBackend):
    """Local no-op backend returning local:// references."""

    def __init__(self, *, root_dir: str):
        self.root_dir = Path(root_dir).resolve()

    def upload_file(self, *, local_path: str, object_name: str, content_type: str | None = None) -> str:
        _ = object_name, content_type
        src = Path(local_path)
        if not src.is_absolute():
            src = (self.root_dir / src).resolve()
        return f"local://{src}"

    def download_file(self, *, object_name: str, local_path: str) -> bool:
        src = Path(object_name)
        if not src.is_absolute():
            src = (self.root_dir / src).resolve()
        if not src.exists() or not src.is_file():
            return False
        dst = Path(local_path)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        return True

    def get_file_url(self, *, object_name: str, expires_seconds: int = 3600) -> str:
        _ = expires_seconds
        src = Path(object_name)
        if not src.is_absolute():
            src = (self.root_dir / src).resolve()
        return f"file://{src}"
