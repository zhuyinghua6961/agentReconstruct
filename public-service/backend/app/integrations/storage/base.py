from __future__ import annotations

from abc import ABC, abstractmethod


class StorageBackend(ABC):
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
