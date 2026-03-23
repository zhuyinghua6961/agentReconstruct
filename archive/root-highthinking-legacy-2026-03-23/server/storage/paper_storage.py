"""Paper PDF storage helpers with MinIO-first lookup."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

_DOWNLOAD_LOCKS: dict[str, threading.Lock] = {}
_DOWNLOAD_LOCKS_GUARD = threading.Lock()


def build_paper_filename(doi: str) -> str:
    return str(doi or "").replace("/", "_") + ".pdf"


def build_paper_object_name(doi: str) -> str:
    return f"papers/{build_paper_filename(doi)}"


def _is_not_found_s3_error(exc: Exception) -> bool:
    code = str(getattr(exc, "code", "") or "")
    return code in {"NoSuchKey", "NoSuchBucket", "NoSuchObject"}


def _build_minio_client_from_env():
    endpoint = os.getenv("MINIO_ENDPOINT", "").strip()
    access_key = os.getenv("MINIO_ACCESS_KEY", "").strip()
    secret_key = os.getenv("MINIO_SECRET_KEY", "").strip()
    bucket = os.getenv("MINIO_BUCKET", "").strip() or "agentcode"
    secure = os.getenv("MINIO_SECURE", "0").strip() == "1"

    if not endpoint or not access_key or not secret_key:
        return None

    try:
        from minio import Minio  # type: ignore
        from minio.error import S3Error  # type: ignore

        return Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure), bucket, S3Error
    except Exception:
        return None


def _get_local_path_lock(local_path: Path) -> threading.Lock:
    key = str(local_path.resolve())
    with _DOWNLOAD_LOCKS_GUARD:
        lock = _DOWNLOAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _DOWNLOAD_LOCKS[key] = lock
        return lock


def paper_pdf_exists(*, doi: str, papers_dir: Path, logger: Any | None = None) -> bool:
    local_path = papers_dir / build_paper_filename(doi)
    minio_ctx = _build_minio_client_from_env()
    if minio_ctx is None:
        return local_path.exists()

    client, bucket, s3_error_cls = minio_ctx
    object_name = build_paper_object_name(doi)
    try:
        client.stat_object(bucket, object_name)
        return True
    except s3_error_cls as exc:
        if _is_not_found_s3_error(exc):
            return local_path.exists()
        if logger is not None:
            logger.warning("MinIO stat_object failed for %s: %s", object_name, exc)
        return local_path.exists()
    except Exception as exc:
        if logger is not None:
            logger.warning("MinIO stat_object failed for %s: %s", object_name, exc)
        return local_path.exists()


def ensure_local_paper_pdf(*, doi: str, papers_dir: Path, logger: Any | None = None) -> Path | None:
    """
    Resolve a readable local paper path.

    Strategy:
    1. Prefer local cache if already present.
    2. Download from MinIO object `papers/<doi_filename>.pdf` when available.
    3. Fall back to local file only.
    """
    papers_dir.mkdir(parents=True, exist_ok=True)
    local_path = papers_dir / build_paper_filename(doi)
    lock = _get_local_path_lock(local_path)

    with lock:
        if local_path.exists():
            return local_path

        minio_ctx = _build_minio_client_from_env()
        if minio_ctx is not None:
            client, bucket, s3_error_cls = minio_ctx
            object_name = build_paper_object_name(doi)
            try:
                client.stat_object(bucket, object_name)
                client.fget_object(bucket, object_name, str(local_path))
                return local_path
            except s3_error_cls as exc:
                if not _is_not_found_s3_error(exc) and logger is not None:
                    logger.warning("MinIO download failed for %s: %s", object_name, exc)
            except Exception as exc:
                if logger is not None:
                    logger.warning("MinIO download failed for %s: %s", object_name, exc)

        if local_path.exists():
            return local_path
        return None
