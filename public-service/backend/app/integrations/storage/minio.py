from __future__ import annotations

from datetime import timedelta

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
