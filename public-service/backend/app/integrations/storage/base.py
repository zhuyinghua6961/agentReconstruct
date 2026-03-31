from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class StorageBackend(ABC):
    @abstractmethod
    def stat_object(self, *, object_name: str, bucket: str | None = None) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def read_object_bytes(self, *, object_name: str, bucket: str | None = None) -> bytes | None:
        raise NotImplementedError

    @abstractmethod
    def object_exists(self, *, object_name: str, bucket: str | None = None) -> bool:
        raise NotImplementedError

    @abstractmethod
    def upload_file(self, *, local_path: str, object_name: str, content_type: str | None = None) -> str:
        raise NotImplementedError

    @abstractmethod
    def download_file(self, *, object_name: str, local_path: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_file_url(self, *, object_name: str, expires_seconds: int = 3600) -> str:
        raise NotImplementedError

    @abstractmethod
    def delete_object(self, *, object_name: str, bucket: str | None = None) -> bool:
        raise NotImplementedError
