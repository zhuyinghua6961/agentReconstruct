from __future__ import annotations

import shutil
from pathlib import Path

from app.integrations.storage.base import StorageBackend


class LocalStorageBackend(StorageBackend):
    def __init__(self, *, root_dir: str):
        self.root_dir = Path(root_dir)

    def object_exists(self, *, object_name: str, bucket: str | None = None) -> bool:
        _ = bucket
        path = Path(object_name)
        if not path.is_absolute():
            path = (self.root_dir / path).resolve()
        return path.exists() and path.is_file()

    def upload_file(self, *, local_path: str, object_name: str, content_type: str | None = None) -> str:
        _ = object_name
        _ = content_type
        path = Path(local_path)
        if not path.is_absolute():
            path = (self.root_dir / path).resolve()
        return f"local://{path}"

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

    def delete_object(self, *, object_name: str, bucket: str | None = None) -> bool:
        _ = bucket
        src = Path(object_name)
        if not src.is_absolute():
            src = (self.root_dir / src).resolve()
        try:
            if src.exists() and src.is_file():
                src.unlink()
            return True
        except Exception:
            return False
