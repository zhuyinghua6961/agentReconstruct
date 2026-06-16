from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterable

from app.modules.storage.object_reader import ObjectReader, ObjectReaderProtocolError, ObjectReaderUnavailableError

_DOWNLOAD_LOCKS: dict[str, threading.Lock] = {}
_DOWNLOAD_LOCKS_GUARD = threading.Lock()
_TABLE_FILE_TYPES = {"excel", "csv", "table", "xls", "xlsx"}
_LOGGER = logging.getLogger("fastqa.storage.upload_materializer")


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


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _strict_minio_only() -> bool:
    if "FASTQA_UPLOAD_MINIO_ONLY" in os.environ:
        return _env_bool("FASTQA_UPLOAD_MINIO_ONLY", True)
    return _env_bool("QA_ORIGINAL_MINIO_ONLY", True)


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


def _record_metric(metrics: Any | None, name: str, **labels: Any) -> None:
    if metrics is None:
        try:
            from app.modules.qa_cache.metrics import increment_cache_metric

            increment_cache_metric("qa_original", name)
        except Exception:
            pass
        _LOGGER.info("qa_original_metric name=%s labels=%s", name, labels)
        return
    for method_name in ("increment", "inc", "record"):
        method = getattr(metrics, method_name, None)
        if not callable(method):
            continue
        try:
            method(name, **labels)
            return
        except TypeError:
            try:
                method(name, labels)
                return
            except TypeError:
                continue
    counter = getattr(metrics, "counter", None)
    if callable(counter):
        try:
            metric = counter(name, **labels)
            inc = getattr(metric, "inc", None)
            if callable(inc):
                inc()
                return
        except TypeError:
            pass
    if callable(metrics):
        try:
            metrics(name, **labels)
        except TypeError:
            metrics(name, labels)


def _source_family_for_item(*, file_item: dict[str, Any], parsed_ref: dict[str, str | None] | None = None) -> str:
    file_name = str(file_item.get("file_name") or "").lower()
    object_name = str((parsed_ref or {}).get("object_name") or "").lower()
    target = object_name or file_name
    if target.endswith(".pdf"):
        return "upload_pdf"
    if target.endswith((".csv", ".xls", ".xlsx")):
        return "upload_table"
    return "upload_object"


def materialize_uploaded_file(
    file_item: dict[str, Any],
    *,
    logger: Any | None = None,
    cache_dir: str | Path | None = None,
    strict_minio_only: bool | None = None,
    metrics: Any | None = None,
) -> dict[str, Any]:
    prepared = dict(file_item or {})
    strict = _strict_minio_only() if strict_minio_only is None else bool(strict_minio_only)
    if strict:
        prepared["local_path"] = ""
        parsed_ref = parse_storage_ref(prepared.get("storage_ref"))
        if not parsed_ref:
            prepared["storage_error"] = "storage_ref_missing"
            _LOGGER.info(
                "upload materialize failed file_id=%s storage_ref=%s error=%s",
                prepared.get("file_id"),
                prepared.get("storage_ref"),
                prepared.get("storage_error"),
            )
            return prepared
        if parsed_ref["scheme"] != "minio":
            prepared["storage_error"] = "storage_ref_not_minio"
            _LOGGER.info(
                "upload materialize failed file_id=%s storage_ref=%s error=%s",
                prepared.get("file_id"),
                prepared.get("storage_ref"),
                prepared.get("storage_error"),
            )
            return prepared
        suffix = Path(str(prepared.get("file_name") or parsed_ref.get("object_name") or "upload.bin")).suffix or ".bin"
        try:
            reader = ObjectReader(runtime_root=cache_dir, metrics=metrics)
            materialized = reader.materialize_temp(str(prepared.get("storage_ref") or ""), suffix=suffix)
            stat = reader.stat(str(prepared.get("storage_ref") or ""))
        except ObjectReaderProtocolError as exc:
            prepared["storage_error"] = str(exc) or "storage_ref_invalid"
            _LOGGER.info(
                "upload materialize failed file_id=%s storage_ref=%s error=%s",
                prepared.get("file_id"),
                prepared.get("storage_ref"),
                prepared.get("storage_error"),
            )
            return prepared
        except ObjectReaderUnavailableError as exc:
            _warn(logger, "uploaded file MinIO materialization failed for %s: %s", prepared.get("storage_ref"), exc)
            prepared["storage_error"] = "object_unavailable"
            _LOGGER.info(
                "upload materialize failed file_id=%s storage_ref=%s error=%s",
                prepared.get("file_id"),
                prepared.get("storage_ref"),
                prepared.get("storage_error"),
            )
            return prepared
        prepared["local_path"] = str(materialized)
        prepared["storage_error"] = ""
        prepared["storage_bucket"] = stat.bucket
        prepared["storage_object_name"] = stat.object_name
        prepared["storage_etag"] = stat.etag
        prepared["storage_size"] = stat.size
        _LOGGER.info(
            "upload materialize success file_id=%s storage_ref=%s local_path=%s size=%s",
            prepared.get("file_id"),
            prepared.get("storage_ref"),
            prepared.get("local_path"),
            prepared.get("storage_size"),
        )
        return prepared

    existing_local_path = _resolve_readable_local_path(prepared.get("local_path"))
    if existing_local_path is not None:
        _record_metric(
            metrics,
            "qa_original_local_fallback_attempt_total",
            service="fastQA",
            source_family=_source_family_for_item(file_item=prepared),
            result="legacy_local_path",
        )
        prepared["local_path"] = str(existing_local_path)
        return prepared

    prepared["local_path"] = ""
    parsed_ref = parse_storage_ref(prepared.get("storage_ref"))
    if not parsed_ref:
        return prepared

    if parsed_ref["scheme"] == "local":
        resolved_local_path = _resolve_readable_local_path(parsed_ref.get("local_path"))
        if resolved_local_path is not None:
            _record_metric(
                metrics,
                "qa_original_local_fallback_attempt_total",
                service="fastQA",
                source_family=_source_family_for_item(file_item=prepared, parsed_ref=parsed_ref),
                result="legacy_local_ref",
            )
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
    metrics: Any | None = None,
) -> list[dict[str, Any]]:
    prepared_items: list[dict[str, Any]] = []
    for item in list(file_items or []):
        if not isinstance(item, dict):
            continue
        prepared = materialize_uploaded_file(item, logger=logger, cache_dir=cache_dir, metrics=metrics)
        if str(prepared.get("local_path") or "").strip():
            prepared = _clear_processing_statuses(prepared)
        prepared_items.append(prepared)
    return prepared_items


__all__ = ["materialize_uploaded_file", "materialize_uploaded_files", "parse_storage_ref"]
