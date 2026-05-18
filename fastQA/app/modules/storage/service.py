from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, unquote

from app.core.config import get_settings


_PAPER_DOWNLOAD_LOCKS: dict[str, threading.Lock] = {}
_PAPER_DOWNLOAD_LOCKS_GUARD = threading.Lock()
_LOGGER = logging.getLogger("fastqa.storage.service")


def _record_original_metric(name: str, **labels: Any) -> None:
    try:
        from app.modules.qa_cache.metrics import increment_cache_metric

        increment_cache_metric("qa_original", name)
    except Exception:
        pass
    _LOGGER.info("qa_original_metric name=%s labels=%s", name, labels)


class StorageService:
    @staticmethod
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

    @classmethod
    def build_paper_filename(cls, doi: str) -> str:
        normalized = cls.normalize_doi(doi)
        if not normalized:
            return ""
        return normalized.replace("/", "_").replace("\\", "_") + ".pdf"

    @classmethod
    def build_paper_object_name(cls, doi: str) -> str:
        return f"papers/{cls.build_paper_filename(doi)}"

    @staticmethod
    def _legacy_filename(doi: str) -> str:
        normalized = StorageService.normalize_doi(doi)
        if not normalized:
            return ""
        return normalized.replace("/", "_", 1).replace("\\", "_", 1) + ".pdf"

    @classmethod
    def _candidate_paths(cls, base_dir: Path, doi: str) -> Iterable[Path]:
        normalized = cls.normalize_doi(doi)
        if not normalized:
            return []

        seen: set[str] = set()
        names = [
            f"{normalized}.pdf",
            cls.build_paper_filename(normalized),
            cls._legacy_filename(normalized),
        ]
        for name in names:
            if not name:
                continue
            candidate = (base_dir / name).expanduser().resolve()
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            yield candidate

        prefix = normalized.split("/", 1)[0] if "/" in normalized else normalized
        suffix = normalized.rsplit("/", 1)[-1]
        patterns = [
            f"{cls.build_paper_filename(normalized)[:-4]}*.pdf",
            f"{cls._legacy_filename(normalized)[:-4]}*.pdf",
            f"{prefix}_{suffix}*.pdf",
        ]
        for pattern in patterns:
            for match in sorted(base_dir.glob(pattern)):
                resolved = match.expanduser().resolve()
                key = str(resolved)
                if key in seen:
                    continue
                seen.add(key)
                yield resolved

    @staticmethod
    def _build_minio_client():
        settings = get_settings()
        endpoint = str(settings.minio_endpoint or "").strip()
        access_key = str(settings.minio_access_key or "").strip()
        secret_key = str(settings.minio_secret_key or "").strip()
        bucket = str(settings.minio_bucket or "agentcode").strip() or "agentcode"
        secure = bool(settings.minio_secure)
        if not endpoint or not access_key or not secret_key:
            return None
        try:
            from minio import Minio  # type: ignore
            from minio.error import S3Error  # type: ignore

            return Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure), bucket, S3Error
        except Exception:
            return None

    @staticmethod
    def _is_not_found_s3_error(exc: Exception) -> bool:
        code = str(getattr(exc, "code", "") or "")
        return code in {"NoSuchKey", "NoSuchBucket", "NoSuchObject"}

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = str(os.getenv(name, "") or "").strip().lower()
        if raw in {"1", "true", "yes", "on"}:
            return True
        if raw in {"0", "false", "no", "off"}:
            return False
        return bool(default)

    @classmethod
    def _strict_original_minio_only(cls) -> bool:
        return cls._env_bool("QA_ORIGINAL_MINIO_ONLY", True)

    @staticmethod
    def _paper_lock_key(local_path: Path) -> str:
        return str(local_path.resolve())

    @classmethod
    def _get_paper_download_lock(cls, local_path: Path) -> threading.Lock:
        key = cls._paper_lock_key(local_path)
        with _PAPER_DOWNLOAD_LOCKS_GUARD:
            lock = _PAPER_DOWNLOAD_LOCKS.get(key)
            if lock is None:
                lock = threading.Lock()
                _PAPER_DOWNLOAD_LOCKS[key] = lock
            return lock

    def build_pdf_url(self, doi: str) -> str:
        normalized = self.normalize_doi(doi)
        if not normalized:
            return "/api/v1/view_pdf/"
        encoded_path = "/".join(quote(part, safe="") for part in normalized.split("/"))
        return f"/api/v1/view_pdf/{encoded_path}"

    def build_pdf_links(self, references: list[str] | tuple[str, ...]) -> list[dict[str, str]]:
        links: list[dict[str, str]] = []
        for item in references:
            doi = self.normalize_doi(str(item or "").strip())
            if not doi:
                continue
            links.append({"doi": doi, "pdf_url": self.build_pdf_url(doi)})
        return links

    def _resolve_local_existing_pdf(self, *, doi: str, papers_dir: str | Path) -> Path | None:
        base_dir = Path(papers_dir).expanduser().resolve()
        raw_value = str(doi or "").strip()
        if raw_value:
            candidate_path = Path(unquote(raw_value)).expanduser()
            if candidate_path.is_absolute() and candidate_path.exists() and candidate_path.is_file():
                return candidate_path.resolve()
        normalized = self.normalize_doi(doi)
        if not normalized:
            return None
        for candidate in self._candidate_paths(base_dir, normalized):
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def paper_exists(
        self,
        *,
        doi: str,
        papers_dir: str | Path,
        project_root: str | None = None,
        logger: Any | None = None,
    ) -> bool:
        _ = project_root
        normalized = self.normalize_doi(doi)
        if not normalized:
            return False
        if not self._strict_original_minio_only():
            existing = self._resolve_local_existing_pdf(doi=doi, papers_dir=papers_dir)
            if existing is not None:
                _record_original_metric(
                    "qa_original_local_fallback_attempt_total",
                    service="fastQA",
                    source_family="paper_pdf",
                    result="legacy_local_exists",
                )
                return True
        minio_ctx = self._build_minio_client()
        if minio_ctx is None:
            return False
        client, bucket, s3_error_cls = minio_ctx
        object_name = self.build_paper_object_name(normalized)
        try:
            client.stat_object(bucket, object_name)
            return True
        except s3_error_cls as exc:
            if not self._is_not_found_s3_error(exc) and logger is not None:
                logger.warning("MinIO stat_object failed for %s: %s", object_name, exc)
        except Exception as exc:
            if logger is not None:
                logger.warning("MinIO stat_object failed for %s: %s", object_name, exc)
        return False

    def ensure_local_paper_pdf(
        self,
        *,
        doi: str,
        papers_dir: str | Path,
        project_root: str | None = None,
        logger: Any | None = None,
    ) -> Path | None:
        _ = project_root
        papers_path = Path(papers_dir).expanduser().resolve()
        papers_path.mkdir(parents=True, exist_ok=True)
        normalized = self.normalize_doi(doi)
        if not normalized:
            return None

        if not self._strict_original_minio_only():
            existing = self._resolve_local_existing_pdf(doi=normalized, papers_dir=papers_path)
            if existing is not None and existing.exists():
                _record_original_metric(
                    "qa_original_local_fallback_attempt_total",
                    service="fastQA",
                    source_family="paper_pdf",
                    result="legacy_local_path",
                )
                return existing

        minio_ctx = self._build_minio_client()
        if minio_ctx is None:
            return None

        client, bucket, s3_error_cls = minio_ctx
        object_name = self.build_paper_object_name(normalized)
        local_path = (papers_path / self.build_paper_filename(normalized)).resolve()
        lock = self._get_paper_download_lock(local_path)

        with lock:
            if not self._strict_original_minio_only() and local_path.exists() and local_path.is_file():
                _record_original_metric(
                    "qa_original_local_fallback_attempt_total",
                    service="fastQA",
                    source_family="paper_pdf",
                    result="legacy_local_path",
                )
                return local_path
            tmp_fd, tmp_path_text = tempfile.mkstemp(
                prefix=f"{local_path.stem}.",
                suffix=f"{local_path.suffix}.tmp",
                dir=str(local_path.parent),
            )
            os.close(tmp_fd)
            tmp_path = Path(tmp_path_text)
            try:
                client.stat_object(bucket, object_name)
                client.fget_object(bucket, object_name, str(tmp_path))
                if tmp_path.exists() and tmp_path.is_file():
                    os.replace(tmp_path, local_path)
                    return local_path
            except s3_error_cls as exc:
                if not self._is_not_found_s3_error(exc) and logger is not None:
                    logger.warning("MinIO download failed for %s: %s", object_name, exc)
            except Exception as exc:
                if logger is not None:
                    logger.warning("MinIO download failed for %s: %s", object_name, exc)
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
        return None


storage_service = StorageService()
