#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Translation cache for public-service documents."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.modules.documents.translation_redis_cache import (
    cache_chunk_translation,
    get_cached_chunk_translation,
    get_translation_redis_service,
    hash_translation_text,
)

try:
    import fcntl

    _HAS_FCNTL = True
except Exception:
    fcntl = None  # type: ignore
    _HAS_FCNTL = False


class TranslationCache:
    def __init__(self, cache_dir: str | None = None):
        if cache_dir is None:
            cache_dir = str(get_settings().translation_cache_dir)

        cache_dir = str(Path(cache_dir).expanduser().resolve())

        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.cache_file = os.path.join(cache_dir, "translations.json")
        self.lock_file = os.path.join(cache_dir, ".translations.lock")

        self._lock = threading.RLock()
        self._max_entries = max(1, int(os.getenv("TRANSLATION_CACHE_MAX_ENTRIES", "10000") or "10000"))
        self._remote_sync_interval_seconds = max(
            0,
            int(os.getenv("TRANSLATION_CACHE_REMOTE_SYNC_INTERVAL_SECONDS", "5") or "5"),
        )
        self._last_remote_sync_at = 0.0

        self._object_name = (
            os.getenv("TRANSLATION_CACHE_OBJECT_NAME", "translation_cache/translations.json").strip()
            or "translation_cache/translations.json"
        )
        self._minio_ctx = self._build_minio_context()

        self.cache: dict[str, dict[str, Any]] = {}
        self._load_cache()

    def _build_minio_context(self):
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

            client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
            return client, bucket, S3Error
        except Exception as exc:
            print(f"⚠️  MinIO translation cache disabled: {exc}")
            return None

    @contextmanager
    def _exclusive_file_lock(self):
        if not _HAS_FCNTL:
            yield
            return
        fd = open(self.lock_file, "a+", encoding="utf-8")
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX)  # type: ignore[arg-type]
            yield
        finally:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)  # type: ignore[arg-type]
            finally:
                fd.close()

    @staticmethod
    def _is_not_found_error(exc: Exception) -> bool:
        return str(getattr(exc, "code", "") or "") in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}

    def _normalize_cache_payload(self, payload: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(payload, dict):
            return {}
        normalized: dict[str, dict[str, Any]] = {}
        now = time.time()
        for key, value in payload.items():
            if not isinstance(key, str):
                continue
            if isinstance(value, str):
                normalized[key] = {"translation": value, "updated_at": now}
                continue
            if isinstance(value, dict):
                translation = value.get("translation")
                if isinstance(translation, str):
                    updated_at_raw = value.get("updated_at")
                    try:
                        updated_at = float(updated_at_raw) if updated_at_raw is not None else now
                    except Exception:
                        updated_at = now
                    normalized[key] = {"translation": translation, "updated_at": updated_at}
        return normalized

    def _read_cache_file(self) -> dict[str, dict[str, Any]]:
        if not os.path.exists(self.cache_file):
            return {}
        try:
            with open(self.cache_file, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            return self._normalize_cache_payload(payload)
        except Exception as exc:
            print(f"⚠️  读取缓存失败，回退空缓存: {exc}")
            return {}

    def _read_remote_cache(self) -> dict[str, dict[str, Any]]:
        if self._minio_ctx is None:
            return {}
        client, bucket, s3_error_cls = self._minio_ctx
        try:
            response = client.get_object(bucket, self._object_name)
            try:
                payload_bytes = response.read()
            finally:
                try:
                    response.close()
                except Exception:
                    pass
                try:
                    response.release_conn()
                except Exception:
                    pass
            payload = json.loads(payload_bytes.decode("utf-8")) if payload_bytes else {}
            return self._normalize_cache_payload(payload)
        except s3_error_cls as exc:
            if self._is_not_found_error(exc):
                return {}
            print(f"⚠️  读取MinIO翻译缓存失败: {exc}")
            return {}
        except Exception as exc:
            print(f"⚠️  读取MinIO翻译缓存失败: {exc}")
            return {}

    def _upload_cache_to_remote(self) -> None:
        if self._minio_ctx is None:
            return
        client, bucket, _ = self._minio_ctx
        try:
            client.fput_object(
                bucket,
                self._object_name,
                self.cache_file,
                content_type="application/json",
            )
        except Exception as exc:
            print(f"⚠️  上传MinIO翻译缓存失败: {exc}")

    @staticmethod
    def _merge_cache_dicts(*cache_dicts: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for cache_dict in cache_dicts:
            for key, value in cache_dict.items():
                if not isinstance(value, dict):
                    continue
                current = merged.get(key)
                if current is None:
                    merged[key] = value
                    continue
                current_ts = float(current.get("updated_at") or 0.0)
                new_ts = float(value.get("updated_at") or 0.0)
                if new_ts >= current_ts:
                    merged[key] = value
        return merged

    def _prune_cache(self, cache_dict: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        if len(cache_dict) <= self._max_entries:
            return cache_dict
        items = sorted(
            cache_dict.items(),
            key=lambda item: float(item[1].get("updated_at") or 0.0),
        )
        kept = items[-self._max_entries :]
        return {k: v for k, v in kept}

    def _write_local_snapshot(self, cache_dict: dict[str, dict[str, Any]]) -> None:
        tmp_file = f"{self.cache_file}.tmp"
        payload = {
            key: {
                "translation": str(value.get("translation") or ""),
                "updated_at": float(value.get("updated_at") or time.time()),
            }
            for key, value in cache_dict.items()
        }
        try:
            with open(tmp_file, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_file, self.cache_file)
        finally:
            if os.path.exists(tmp_file):
                try:
                    os.remove(tmp_file)
                except Exception:
                    pass

    def _load_cache(self):
        with self._lock:
            with self._exclusive_file_lock():
                local_cache = self._read_cache_file()
                remote_cache = self._read_remote_cache()
                merged = self._prune_cache(self._merge_cache_dicts(local_cache, remote_cache))
                self.cache = merged
                if merged != local_cache:
                    self._write_local_snapshot(merged)
                if self._minio_ctx is not None and merged and not remote_cache:
                    self._upload_cache_to_remote()
                self._last_remote_sync_at = time.time()

    def _refresh_from_remote_if_due(self, *, force: bool = False) -> None:
        if self._minio_ctx is None:
            return
        now = time.time()
        if not force and self._remote_sync_interval_seconds > 0:
            if now - self._last_remote_sync_at < self._remote_sync_interval_seconds:
                return

        with self._lock:
            with self._exclusive_file_lock():
                remote_cache = self._read_remote_cache()
                if remote_cache:
                    merged = self._prune_cache(self._merge_cache_dicts(self.cache, remote_cache))
                    if merged != self.cache:
                        self.cache = merged
                        self._write_local_snapshot(merged)
                self._last_remote_sync_at = now

    def _save_cache(self):
        with self._lock:
            with self._exclusive_file_lock():
                local_cache = self._read_cache_file()
                remote_cache = self._read_remote_cache()
                merged = self._prune_cache(self._merge_cache_dicts(local_cache, remote_cache, self.cache))
                self.cache = merged
                self._write_local_snapshot(merged)
                self._upload_cache_to_remote()
                self._last_remote_sync_at = time.time()

    def _hash_text(self, text: str, *, profile: str = "snippet") -> str:
        return hash_translation_text(text, profile=profile)

    def get(self, text: str, *, profile: str = "snippet") -> str | None:
        redis_service = get_translation_redis_service()
        redis_cached = get_cached_chunk_translation(
            redis_service=redis_service,
            text=text,
            profile=profile,
        )
        if redis_cached:
            text_hash = self._hash_text(text, profile=profile)
            self.cache[text_hash] = {
                "translation": redis_cached,
                "updated_at": time.time(),
            }
            return redis_cached

        self._refresh_from_remote_if_due()
        text_hash = self._hash_text(text, profile=profile)
        entry = self.cache.get(text_hash)
        if entry:
            entry["updated_at"] = time.time()
            translation = str(entry.get("translation") or "")
            if translation:
                cache_chunk_translation(
                    redis_service=redis_service,
                    text=text,
                    translation=translation,
                    profile=profile,
                )
            return translation
        return None

    def set(self, text: str, translation: str, *, profile: str = "snippet"):
        redis_service = get_translation_redis_service()
        cache_chunk_translation(
            redis_service=redis_service,
            text=text,
            translation=translation,
            profile=profile,
        )
        text_hash = self._hash_text(text, profile=profile)
        self.cache[text_hash] = {
            "translation": translation,
            "updated_at": time.time(),
        }
        self.cache = self._prune_cache(self.cache)
        self._save_cache()
