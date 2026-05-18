"""Paper PDF storage helpers with MinIO-first lookup."""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from server.storage.object_reader import ObjectReader, ObjectReaderUnavailableError

_DOWNLOAD_LOCKS: dict[str, threading.Lock] = {}
_DOWNLOAD_LOCKS_GUARD = threading.Lock()


def normalize_doi(value: str) -> str:
    text = str(value or "").strip()
    filename_like_source = False
    previous = None
    while previous != text:
        previous = text
        text = unquote(text).strip()
    text = text.replace("\\", "/")
    text = re.sub(r"^doi\s*[:=]\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^[(/\\s]+|[)\],;:.\\s]+$", "", text)
    if "papers/" in text:
        text = text.split("papers/", 1)[-1]
        filename_like_source = text.lower().endswith(".pdf")
    elif (
        text.lower().endswith(".pdf")
        and (
            os.path.isabs(text)
            or text.startswith("./")
            or text.startswith("../")
            or bool(re.match(r"^[A-Za-z]:[\\/]", text))
        )
    ):
        text = Path(text).name or text
        filename_like_source = True
    if text.lower().endswith(".pdf"):
        text = text[:-4]
    if "_" in text and "/" not in text and text.startswith("10.") and not filename_like_source:
        text = text.replace("_", "/", 1)
    return text.strip()


def build_paper_filename(doi: str) -> str:
    # Compatibility note: retained for the retired document/PDF HTTP flow.
    # thinking ask still uses normalize_doi(), but does not depend on local PDF materialization.
    normalized = normalize_doi(doi)
    if not normalized:
        return ""
    return normalized.replace("/", "_").replace("\\", "_") + ".pdf"


def build_paper_object_name(doi: str) -> str:
    # Compatibility note: retained for legacy paper file lookup/materialization paths.
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


def _original_minio_only() -> bool:
    return str(os.getenv("HIGHTHINKING_ORIGINAL_MINIO_ONLY", "true")).strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _get_local_path_lock(local_path: Path) -> threading.Lock:
    key = str(local_path.resolve())
    with _DOWNLOAD_LOCKS_GUARD:
        lock = _DOWNLOAD_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _DOWNLOAD_LOCKS[key] = lock
        return lock


def paper_pdf_exists(*, doi: str, papers_dir: Path, logger: Any | None = None) -> bool:
    # Compatibility note: retained for the retired document/PDF HTTP flow and
    # should not be treated as part of the active thinking ask path.
    normalized = normalize_doi(doi)
    if not normalized:
        return False
    local_path = papers_dir / build_paper_filename(normalized)
    minio_ctx = _build_minio_client_from_env()
    if minio_ctx is None:
        if _original_minio_only():
            return False
        return local_path.exists()

    client, bucket, s3_error_cls = minio_ctx
    object_name = build_paper_object_name(normalized)
    try:
        client.stat_object(bucket, object_name)
        return True
    except s3_error_cls as exc:
        if _is_not_found_s3_error(exc):
            if _original_minio_only():
                return False
            return local_path.exists()
        if logger is not None:
            logger.warning("MinIO stat_object failed for %s: %s", object_name, exc)
        if _original_minio_only():
            return False
        return local_path.exists()
    except Exception as exc:
        if logger is not None:
            logger.warning("MinIO stat_object failed for %s: %s", object_name, exc)
        if _original_minio_only():
            return False
        return local_path.exists()


def ensure_local_paper_pdf(*, doi: str, papers_dir: Path, logger: Any | None = None) -> Path | None:
    # Compatibility note: retained for legacy paper local-materialization flows and
    # should not be treated as part of the active thinking ask path.
    """
    Resolve a readable local paper path.

    Strategy:
    1. Prefer local cache if already present.
    2. Download from MinIO object `papers/<doi_filename>.pdf` when available.
    3. Fall back to local file only.
    """
    papers_dir.mkdir(parents=True, exist_ok=True)
    normalized = normalize_doi(doi)
    if not normalized:
        return None
    local_path = papers_dir / build_paper_filename(normalized)
    lock = _get_local_path_lock(local_path)

    if _original_minio_only():
        minio_ctx = _build_minio_client_from_env()
        if minio_ctx is None:
            return None
        client, bucket, _s3_error_cls = minio_ctx
        object_name = build_paper_object_name(normalized)
        storage_ref = f"minio://{bucket}/{object_name}"
        reader = ObjectReader(client=client, runtime_root=papers_dir / "object-cache")
        try:
            return reader.materialize_temp(storage_ref, suffix=".pdf")
        except ObjectReaderUnavailableError as exc:
            if logger is not None:
                logger.warning("MinIO paper materialization failed for %s: %s", object_name, exc)
            return None

    with lock:
        if local_path.exists():
            return local_path

        minio_ctx = _build_minio_client_from_env()
        if minio_ctx is not None:
            client, bucket, s3_error_cls = minio_ctx
            object_name = build_paper_object_name(normalized)
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
