"""Storage backend contract."""

# Compatibility note: retained for legacy conversation-json/object-storage support
# and older file-flow helpers. Current highThinkingQA runtime uses public-service
# for conversation persistence, and file HTTP surfaces are no longer served here.


from __future__ import annotations

from abc import ABC, abstractmethod


class StorageBackend(ABC):
    """Object storage backend interface."""

    @abstractmethod
    def upload_file(self, *, local_path: str, object_name: str, content_type: str | None = None) -> str:
        """Upload local file and return storage reference."""

    @abstractmethod
    def download_file(self, *, object_name: str, local_path: str) -> bool:
        """Download object to local path."""

    @abstractmethod
    def get_file_url(self, *, object_name: str, expires_seconds: int = 3600) -> str:
        """Build a downloadable URL for object."""
