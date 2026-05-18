from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ObjectReaderError(RuntimeError):
    pass


class ObjectReaderProtocolError(ObjectReaderError):
    pass


class ObjectReaderUnavailableError(ObjectReaderError):
    pass


@dataclass(frozen=True)
class ObjectStat:
    bucket: str
    object_name: str
    etag: str = ""
    size: int = 0
    sha256: str = ""
    content_type: str = ""
    last_modified: Any | None = None


_CACHE_LOCKS: dict[str, threading.Lock] = {}
_CACHE_LOCKS_GUARD = threading.Lock()
_SERVICE_LABEL = "highThinkingQA"
_LOGGER = logging.getLogger("highthinking.storage.object_reader")


def parse_minio_storage_ref(storage_ref: str | None) -> tuple[str, str]:
    raw = str(storage_ref or "").strip()
    if not raw:
        raise ObjectReaderProtocolError("storage_ref_missing")
    if not raw.startswith("minio://"):
        raise ObjectReaderProtocolError("storage_ref_not_minio")
    value = raw[len("minio://") :]
    if "/" not in value:
        raise ObjectReaderProtocolError("storage_ref_invalid")
    bucket, object_name = value.split("/", 1)
    bucket = bucket.strip()
    object_name = object_name.strip()
    if not bucket or not object_name:
        raise ObjectReaderProtocolError("storage_ref_invalid")
    return bucket, object_name


def _default_runtime_root() -> Path:
    configured = str(os.getenv("HIGHTHINKING_OBJECT_CACHE_DIR") or os.getenv("HIGHTHINKING_SERVICE_RUNTIME_ROOT") or "").strip()
    base = Path(configured).expanduser() if configured else Path(tempfile.gettempdir()) / "highthinking-object-cache"
    target = base / "object-cache" if base.name != "object-cache" else base
    target.mkdir(parents=True, exist_ok=True)
    return target.resolve()


def _build_minio_client_from_env() -> Any | None:
    endpoint = os.getenv("MINIO_ENDPOINT", "").strip()
    access_key = os.getenv("MINIO_ACCESS_KEY", "").strip()
    secret_key = os.getenv("MINIO_SECRET_KEY", "").strip()
    secure = os.getenv("MINIO_SECURE", "0").strip().lower() in {"1", "true", "yes", "on"}
    if not endpoint or not access_key or not secret_key:
        return None
    try:
        from minio import Minio  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise ObjectReaderUnavailableError("minio_dependency_unavailable") from exc
    return Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)


def _metadata_sha256(stat_result: Any) -> str:
    metadata = getattr(stat_result, "metadata", None)
    if isinstance(metadata, dict):
        for key in ("sha256", "x-amz-meta-sha256", "X-Amz-Meta-Sha256"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value
    return ""


def _lock_for(path: Path) -> threading.Lock:
    key = str(path)
    with _CACHE_LOCKS_GUARD:
        lock = _CACHE_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _CACHE_LOCKS[key] = lock
        return lock


def _record_metric(metrics: Any | None, name: str, **labels: Any) -> None:
    if metrics is None:
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


def _source_family_for_object(object_name: str) -> str:
    lower = str(object_name or "").lower()
    if lower.startswith("patent/originals/"):
        if lower.endswith("/structured/tables.json") or "/structured/tables" in lower:
            return "patent_table"
        if "/fulltext/" in lower or lower.endswith(".pdf"):
            return "patent_fulltext"
        return "patent_structured"
    if lower.endswith(".pdf"):
        return "upload_pdf" if lower.startswith("uploads/") else "paper_pdf"
    if lower.endswith((".csv", ".xls", ".xlsx")):
        return "upload_table"
    return "upload_object"


def _source_family_for_ref(storage_ref: str) -> str:
    try:
        _bucket, object_name = parse_minio_storage_ref(storage_ref)
        return _source_family_for_object(object_name)
    except ObjectReaderProtocolError:
        return "unknown"


class ObjectReader:
    def __init__(self, *, client: Any | None = None, runtime_root: str | Path | None = None, metrics: Any | None = None) -> None:
        self._client = client
        self._runtime_root = Path(runtime_root).expanduser().resolve() if runtime_root is not None else _default_runtime_root()
        self._runtime_root.mkdir(parents=True, exist_ok=True)
        self._metrics = metrics

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = _build_minio_client_from_env()
        if self._client is None:
            raise ObjectReaderUnavailableError("minio_client_unavailable")
        return self._client

    def stat(self, storage_ref: str) -> ObjectStat:
        bucket, object_name = parse_minio_storage_ref(storage_ref)
        try:
            client = self._get_client()
        except ObjectReaderUnavailableError:
            _record_metric(
                self._metrics,
                "qa_original_minio_read_failed_total",
                service=_SERVICE_LABEL,
                source_family=_source_family_for_object(object_name),
                result="failure",
                reason="minio_client_unavailable",
            )
            raise
        try:
            result = client.stat_object(bucket, object_name)
        except Exception as exc:
            _record_metric(
                self._metrics,
                "qa_original_minio_read_failed_total",
                service=_SERVICE_LABEL,
                source_family=_source_family_for_object(object_name),
                result="failure",
                reason="object_stat_failed",
            )
            raise ObjectReaderUnavailableError(f"object_stat_failed:{object_name}") from exc
        return ObjectStat(
            bucket=bucket,
            object_name=object_name,
            etag=str(getattr(result, "etag", "") or ""),
            size=int(getattr(result, "size", 0) or 0),
            sha256=_metadata_sha256(result),
            content_type=str(getattr(result, "content_type", "") or ""),
            last_modified=getattr(result, "last_modified", None),
        )

    def read_bytes(self, storage_ref: str) -> bytes:
        try:
            bucket, object_name = parse_minio_storage_ref(storage_ref)
        except ObjectReaderProtocolError as exc:
            _record_metric(
                self._metrics,
                "qa_original_minio_read_failed_total",
                service=_SERVICE_LABEL,
                source_family="unknown",
                result="failure",
                reason=str(exc) or "storage_ref_invalid",
            )
            raise
        source_family = _source_family_for_object(object_name)
        try:
            client = self._get_client()
        except ObjectReaderUnavailableError:
            _record_metric(
                self._metrics,
                "qa_original_minio_read_failed_total",
                service=_SERVICE_LABEL,
                source_family=source_family,
                result="failure",
                reason="minio_client_unavailable",
            )
            raise
        response = None
        try:
            response = client.get_object(bucket, object_name)
            payload = bytes(response.read())
        except Exception as exc:
            _record_metric(
                self._metrics,
                "qa_original_minio_read_failed_total",
                service=_SERVICE_LABEL,
                source_family=source_family,
                result="failure",
                reason="object_read_failed",
            )
            raise ObjectReaderUnavailableError(f"object_read_failed:{object_name}") from exc
        finally:
            if response is not None:
                for method_name in ("close", "release_conn"):
                    method = getattr(response, method_name, None)
                    if callable(method):
                        try:
                            method()
                        except Exception:
                            pass
        _record_metric(
            self._metrics,
            "qa_original_minio_read_total",
            service=_SERVICE_LABEL,
            source_family=source_family,
            result="success",
        )
        return payload

    def read_json(self, storage_ref: str) -> Any:
        return json.loads(self.read_bytes(storage_ref).decode("utf-8"))

    def _cache_path(self, *, storage_ref: str, suffix: str) -> Path:
        stat = self.stat(storage_ref)
        digest_parts = [stat.bucket, stat.object_name, str(stat.size)]
        if stat.sha256:
            digest_parts.extend([stat.etag, stat.sha256])
        elif stat.etag:
            digest_parts.append(stat.etag)
        else:
            digest_parts.append(hashlib.sha256(self.read_bytes(storage_ref)).hexdigest())
        digest = hashlib.sha1("|".join(digest_parts).encode("utf-8")).hexdigest()
        normalized_suffix = suffix if str(suffix or "").startswith(".") else f".{suffix}" if suffix else ".bin"
        return (self._runtime_root / f"{digest}{normalized_suffix}").resolve()

    def materialize_temp(self, storage_ref: str, *, suffix: str) -> Path:
        target = self._cache_path(storage_ref=storage_ref, suffix=suffix)
        lock = _lock_for(target)
        with lock:
            if target.exists() and target.is_file():
                _record_metric(
                    self._metrics,
                    "qa_original_scratch_materialize_total",
                    service=_SERVICE_LABEL,
                    source_family=_source_family_for_ref(storage_ref),
                    result="cache_hit",
                )
                return target
            payload = self.read_bytes(storage_ref)
            tmp_path = target.with_suffix(target.suffix + ".tmp")
            tmp_path.write_bytes(payload)
            os.replace(tmp_path, target)
        _record_metric(
            self._metrics,
            "qa_original_scratch_materialize_total",
            service=_SERVICE_LABEL,
            source_family=_source_family_for_ref(storage_ref),
            result="success",
        )
        return target


__all__ = [
    "ObjectReader",
    "ObjectReaderError",
    "ObjectReaderProtocolError",
    "ObjectReaderUnavailableError",
    "ObjectStat",
    "parse_minio_storage_ref",
]
