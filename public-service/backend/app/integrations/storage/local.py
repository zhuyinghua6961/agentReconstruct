from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path
from typing import Any, Iterator

from app.integrations.storage.base import StorageBackend


class LocalStorageBackend(StorageBackend):
    def __init__(self, *, root_dir: str):
        self.root_dir = Path(root_dir)

    def stat_object(self, *, object_name: str, bucket: str | None = None) -> dict[str, Any] | None:
        _ = bucket
        path = Path(object_name)
        if not path.is_absolute():
            path = (self.root_dir / path).resolve()
        if not path.exists() or not path.is_file():
            return None
        return {
            "object_name": object_name,
            "etag": "",
            "size": int(path.stat().st_size),
            "content_type": mimetypes.guess_type(str(path.name))[0] or "application/octet-stream",
            "last_modified": path.stat().st_mtime,
        }

    def read_object_bytes(self, *, object_name: str, bucket: str | None = None) -> bytes | None:
        _ = bucket
        path = Path(object_name)
        if not path.is_absolute():
            path = (self.root_dir / path).resolve()
        if not path.exists() or not path.is_file():
            return None
        return path.read_bytes()

    def iter_object_bytes(self, *, object_name: str, bucket: str | None = None, chunk_size: int = 65536) -> Iterator[bytes]:
        _ = bucket
        path = Path(object_name)
        if not path.is_absolute():
            path = (self.root_dir / path).resolve()
        if not path.exists() or not path.is_file():
            return
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(max(1, int(chunk_size)))
                if not chunk:
                    break
                yield chunk

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
