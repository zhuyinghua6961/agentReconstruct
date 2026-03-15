"""MinIO storage backend."""

from __future__ import annotations

import os
from datetime import timedelta

from server.storage.base import StorageBackend


class MinIOStorageBackend(StorageBackend):
    """MinIO implementation for upload/download/url."""

    def __init__(self) -> None:
        try:
            from minio import Minio  # type: ignore
            from minio.error import S3Error  # type: ignore
        except Exception as exc:  # pragma: no cover - dependency guard
            raise RuntimeError("minio dependency not installed") from exc

        endpoint = str(os.getenv("MINIO_ENDPOINT", "")).strip()
        access_key = str(os.getenv("MINIO_ACCESS_KEY", "")).strip()
        secret_key = str(os.getenv("MINIO_SECRET_KEY", "")).strip()
        bucket = str(os.getenv("MINIO_BUCKET", "")).strip() or "agentcode"
        secure = str(os.getenv("MINIO_SECURE", "0")).strip() == "1"
        region = str(os.getenv("MINIO_REGION", "")).strip() or None

        if not endpoint or not access_key or not secret_key:
            raise RuntimeError("MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY are required")

        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self._bucket = bucket
        self._region = region
        self._s3_error_cls = S3Error
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        if self._client.bucket_exists(self._bucket):
            return
        if self._region:
            self._client.make_bucket(self._bucket, location=self._region)
        else:
            self._client.make_bucket(self._bucket)

    def upload_file(self, *, local_path: str, object_name: str, content_type: str | None = None) -> str:
        self._client.fput_object(self._bucket, object_name, local_path, content_type=content_type)
        return f"minio://{self._bucket}/{object_name}"

    def download_file(self, *, object_name: str, local_path: str) -> bool:
        try:
            self._client.fget_object(self._bucket, object_name, local_path)
            return True
        except Exception:
            return False

    def get_file_url(self, *, object_name: str, expires_seconds: int = 3600) -> str:
        return self._client.presigned_get_object(
            self._bucket,
            object_name,
            expires=timedelta(seconds=max(60, int(expires_seconds))),
        )
