from __future__ import annotations

from datetime import timedelta
from typing import Any, Iterator

from app.integrations.storage.base import StorageBackend


class MinIOStorageBackend(StorageBackend):
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str = "agentcode",
        secure: bool = False,
        region: str | None = None,
    ) -> None:
        try:
            from minio import Minio  # type: ignore
            from minio.error import S3Error  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("minio dependency not installed") from exc

        if not endpoint or not access_key or not secret_key:
            raise RuntimeError("MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY are required")

        self._client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
        self._bucket = bucket
        self._region = region
        self._s3_error_cls = S3Error
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            if not self._client.bucket_exists(self._bucket):
                if self._region:
                    self._client.make_bucket(self._bucket, location=self._region)
                else:
                    self._client.make_bucket(self._bucket)
        except self._s3_error_cls:
            raise

    def object_exists(self, *, object_name: str, bucket: str | None = None) -> bool:
        target_bucket = str(bucket or "").strip() or self._bucket
        try:
            self._client.stat_object(target_bucket, object_name)
            return True
        except Exception:
            return False

    @staticmethod
    def _is_not_found_error(exc: Exception) -> bool:
        return str(getattr(exc, "code", "") or "") in {"NoSuchKey", "NoSuchObject"}

    def stat_object(self, *, object_name: str, bucket: str | None = None) -> dict[str, Any] | None:
        target_bucket = str(bucket or "").strip() or self._bucket
        try:
            result = self._client.stat_object(target_bucket, object_name)
        except self._s3_error_cls as exc:
            if self._is_not_found_error(exc):
                return None
            raise
        return {
            "bucket": target_bucket,
            "object_name": object_name,
            "etag": str(getattr(result, "etag", "") or ""),
            "size": int(getattr(result, "size", 0) or 0),
            "content_type": str(getattr(result, "content_type", "") or ""),
            "last_modified": getattr(result, "last_modified", None),
        }

    def read_object_bytes(self, *, object_name: str, bucket: str | None = None) -> bytes | None:
        target_bucket = str(bucket or "").strip() or self._bucket
        try:
            response = self._client.get_object(target_bucket, object_name)
        except self._s3_error_cls as exc:
            if self._is_not_found_error(exc):
                return None
            raise
        try:
            return response.read()
        finally:
            try:
                response.close()
            except Exception:
                pass
            try:
                response.release_conn()
            except Exception:
                pass

    def iter_object_bytes(self, *, object_name: str, bucket: str | None = None, chunk_size: int = 65536) -> Iterator[bytes]:
        target_bucket = str(bucket or "").strip() or self._bucket
        try:
            response = self._client.get_object(target_bucket, object_name)
        except self._s3_error_cls as exc:
            if self._is_not_found_error(exc):
                return
            raise
        try:
            while True:
                chunk = response.read(max(1, int(chunk_size)))
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                response.close()
            except Exception:
                pass
            try:
                response.release_conn()
            except Exception:
                pass

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

    def delete_object(self, *, object_name: str, bucket: str | None = None) -> bool:
        target_bucket = str(bucket or "").strip() or self._bucket
        try:
            self._client.remove_object(target_bucket, object_name)
            return True
        except Exception:
            return False
