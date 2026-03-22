from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterable

_DOWNLOAD_LOCKS: dict[str, threading.Lock] = {}
_DOWNLOAD_LOCKS_GUARD = threading.Lock()
_TABLE_FILE_TYPES = {"excel", "csv", "table", "xls", "xlsx"}


def parse_storage_ref(storage_ref: str | None) -> dict[str, str | None] | None:
    if not storage_ref:
        return None
    raw = str(storage_ref).strip()
    if raw.startswith("minio://"):
        value = raw[len("minio://") :]
        if "/" not in value:
            return None
        bucket, object_name = value.split("/", 1)
        return {"scheme": "minio", "bucket": bucket, "object_name": object_name, "local_path": None}
    if raw.startswith("local://"):
        return {"scheme": "local", "bucket": None, "object_name": None, "local_path": raw[len("local://") :]}
    return None


def _resolve_readable_local_path(local_path: str | None) -> Path | None:
    raw = str(local_path or "").strip()
    if not raw:
        return None
    try:
        candidate = Path(raw).expanduser()
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    except Exception:
        return None
    return None


def _default_cache_dir() -> Path:
    configured = str(os.getenv("FASTQA_UPLOAD_CACHE_DIR", "") or "").strip()
    base_dir = Path(configured).expanduser() if configured else Path(tempfile.gettempdir()) / "fastqa-upload-cache"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir.resolve()


def _build_minio_client_from_env():
    endpoint = os.getenv("MINIO_ENDPOINT", "").strip()
    access_key = os.getenv("MINIO_ACCESS_KEY", "").strip()
    secret_key = os.getenv("MINIO_SECRET_KEY", "").strip()
    secure = os.getenv("MINIO_SECURE", "0").strip() == "1"
    if not endpoint or not access_key or not secret_key:
        return None
    try:
        from minio import Minio  # type: ignore
        from minio.error import S3Error  # type: ignore

        return Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure), S3Error
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


def _cache_path_for_file(*, file_item: dict[str, Any], parsed_ref: dict[str, str | None], cache_dir: Path) -> Path:
    file_name = str(file_item.get("file_name") or "").strip()
    object_name = str(parsed_ref.get("object_name") or "").strip()
    source_name = file_name or Path(object_name).name or "upload.bin"
    suffix = Path(source_name).suffix
    stem = Path(source_name).stem or "upload"
    safe_stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in stem).strip("_") or "upload"
    digest_source = "|".join(
        [
            str(file_item.get("file_id") or ""),
            str(file_item.get("storage_ref") or ""),
            file_name,
        ]
    )
    digest = hashlib.sha1(digest_source.encode("utf-8")).hexdigest()[:16]
    return (cache_dir / f"{safe_stem}-{digest}{suffix}").resolve()


def _warn(logger: Any, message: str, *args: Any) -> None:
    if logger is not None and hasattr(logger, "warning"):
        logger.warning(message, *args)


def materialize_uploaded_file(
    file_item: dict[str, Any],
    *,
    logger: Any | None = None,
    cache_dir: str | Path | None = None,
) -> dict[str, Any]:
    prepared = dict(file_item or {})
    existing_local_path = _resolve_readable_local_path(prepared.get("local_path"))
    if existing_local_path is not None:
        prepared["local_path"] = str(existing_local_path)
        return prepared

    prepared["local_path"] = ""
    parsed_ref = parse_storage_ref(prepared.get("storage_ref"))
    if not parsed_ref:
        return prepared

    if parsed_ref["scheme"] == "local":
        resolved_local_path = _resolve_readable_local_path(parsed_ref.get("local_path"))
        if resolved_local_path is not None:
            prepared["local_path"] = str(resolved_local_path)
        return prepared

    if parsed_ref["scheme"] != "minio":
        return prepared

    minio_ctx = _build_minio_client_from_env()
    if minio_ctx is None:
        return prepared

    client, s3_error_cls = minio_ctx
    bucket = str(parsed_ref.get("bucket") or "").strip()
    object_name = str(parsed_ref.get("object_name") or "").strip()
    if not bucket or not object_name:
        return prepared

    target_cache_dir = Path(cache_dir).expanduser().resolve() if cache_dir else _default_cache_dir()
    target_cache_dir.mkdir(parents=True, exist_ok=True)
    target_path = _cache_path_for_file(file_item=prepared, parsed_ref=parsed_ref, cache_dir=target_cache_dir)
    lock = _get_local_path_lock(target_path)

    with lock:
        if target_path.exists() and target_path.is_file():
            prepared["local_path"] = str(target_path)
            return prepared
        try:
            client.fget_object(bucket, object_name, str(target_path))
        except s3_error_cls as exc:
            _warn(logger, "uploaded file MinIO download failed for %s/%s: %s", bucket, object_name, exc)
            return prepared
        except Exception as exc:
            _warn(logger, "uploaded file MinIO download failed for %s/%s: %s", bucket, object_name, exc)
            return prepared

    if target_path.exists() and target_path.is_file():
        prepared["local_path"] = str(target_path)
    return prepared


def _clear_processing_statuses(file_item: dict[str, Any]) -> dict[str, Any]:
    prepared = dict(file_item or {})
    prepared["parse_status"] = ""
    prepared["index_status"] = ""
    prepared["processing_stage"] = ""
    return prepared


def materialize_uploaded_files(
    *,
    file_items: Iterable[dict[str, Any]],
    logger: Any | None = None,
    cache_dir: str | Path | None = None,
) -> list[dict[str, Any]]:
    prepared_items: list[dict[str, Any]] = []
    for item in list(file_items or []):
        if not isinstance(item, dict):
            continue
        prepared = materialize_uploaded_file(item, logger=logger, cache_dir=cache_dir)
        if str(prepared.get("local_path") or "").strip():
            prepared = _clear_processing_statuses(prepared)
        prepared_items.append(prepared)
    return prepared_items


__all__ = ["materialize_uploaded_file", "materialize_uploaded_files", "parse_storage_ref"]
