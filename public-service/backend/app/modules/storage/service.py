from __future__ import annotations

import json
import mimetypes
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from app.integrations.storage.factory import get_storage_backend
from app.integrations.storage.minio import MinIOStorageBackend


_PAPER_DOWNLOAD_LOCKS: dict[str, threading.Lock] = {}
_PAPER_DOWNLOAD_LOCKS_GUARD = threading.Lock()


class StorageService:
    @staticmethod
    def normalize_patent_id(value: str) -> str:
        return str(value or "").strip().upper()

    @staticmethod
    def normalize_doi(value: str) -> str:
        text = str(value or "").strip()
        previous = None
        while previous != text:
            previous = text
            text = unquote(text).strip()
        text = text.replace("\\", "/")
        text = re.sub(r"^doi:\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^[(/\\s]+|[)\],;:.\\s]+$", "", text)
        if "papers/" in text:
            text = text.split("papers/", 1)[-1]
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
        if text.lower().endswith(".pdf"):
            text = text[:-4]
        if "_" in text and "/" not in text and text.startswith("10."):
            text = text.replace("_", "/", 1)
        return text.strip()

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

    @classmethod
    def build_paper_filename(cls, doi: str) -> str:
        normalized = cls.normalize_doi(doi)
        return normalized.replace("/", "_").replace("\\", "_") + ".pdf" if normalized else ""

    @classmethod
    def build_paper_object_name(cls, doi: str) -> str:
        return f"papers/{cls.build_paper_filename(doi)}"

    @classmethod
    def build_patent_original_prefix(cls, canonical_patent_id: str) -> str:
        normalized = cls.normalize_patent_id(canonical_patent_id)
        return f"patent/originals/{normalized}" if normalized else "patent/originals"

    @classmethod
    def build_patent_original_manifest_object_name(cls, canonical_patent_id: str) -> str:
        return f"{cls.build_patent_original_prefix(canonical_patent_id)}/manifest.json"

    @staticmethod
    def _resolve_backend(*, backend: Any | None, project_root: str | None):
        if backend is not None:
            return backend
        return get_storage_backend(project_root=project_root)

    @staticmethod
    def _resolve_local_backend_path(*, backend: Any, object_name: str) -> Path:
        path = Path(object_name)
        if path.is_absolute():
            return path
        root_dir = Path(str(getattr(backend, "root_dir", "") or "")).expanduser()
        if str(root_dir):
            return (root_dir / path).resolve()
        return path.resolve()

    def read_object_bytes(
        self,
        *,
        object_name: str,
        project_root: str | None = None,
        backend: Any | None = None,
    ) -> bytes | None:
        active_backend = self._resolve_backend(backend=backend, project_root=project_root)
        reader = getattr(active_backend, "read_object_bytes", None)
        if callable(reader):
            return reader(object_name=object_name)
        path = self._resolve_local_backend_path(backend=active_backend, object_name=object_name)
        if not path.exists() or not path.is_file():
            return None
        return path.read_bytes()

    def read_json_object(
        self,
        *,
        object_name: str,
        project_root: str | None = None,
        backend: Any | None = None,
    ) -> dict[str, Any] | list[Any] | None:
        payload = self.read_object_bytes(object_name=object_name, project_root=project_root, backend=backend)
        if payload is None:
            return None
        return json.loads(payload.decode("utf-8"))

    def stat_object(
        self,
        *,
        object_name: str,
        project_root: str | None = None,
        backend: Any | None = None,
    ) -> dict[str, Any] | None:
        active_backend = self._resolve_backend(backend=backend, project_root=project_root)
        stater = getattr(active_backend, "stat_object", None)
        if callable(stater):
            return stater(object_name=object_name)
        path = self._resolve_local_backend_path(backend=active_backend, object_name=object_name)
        if not path.exists() or not path.is_file():
            return None
        return {
            "object_name": object_name,
            "etag": "",
            "size": int(path.stat().st_size),
            "content_type": mimetypes.guess_type(str(path.name))[0] or "application/octet-stream",
            "last_modified": path.stat().st_mtime,
        }

    @staticmethod
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
            path = raw[len("local://") :]
            return {"scheme": "local", "bucket": None, "object_name": None, "local_path": path}
        return None

    @staticmethod
    def mirror_file(
        *,
        local_path: str,
        object_name: str,
        content_type: str | None,
        project_root: str,
        logger: Any,
    ) -> str | None:
        path = Path(local_path)
        if not path.exists() or not path.is_file():
            return None
        try:
            backend = get_storage_backend(project_root=project_root)
            return backend.upload_file(local_path=str(path), object_name=object_name, content_type=content_type)
        except Exception as exc:
            logger.warning(f"object storage mirror skipped: {exc}")
            return None

    def paper_exists(self, *, doi: str, papers_dir: str | Path, project_root: str, logger: Any | None = None) -> bool:
        papers_path = Path(papers_dir)
        normalized = self.normalize_doi(doi)
        local_path = papers_path / self.build_paper_filename(normalized)
        backend = get_storage_backend(project_root=project_root)
        if isinstance(backend, MinIOStorageBackend):
            try:
                if backend.object_exists(object_name=self.build_paper_object_name(normalized)):
                    return True
            except Exception as exc:
                if logger is not None:
                    logger.warning(f"paper exists check via object storage failed: {exc}")
        return local_path.exists() and local_path.is_file()

    def ensure_local_paper_pdf(
        self,
        *,
        doi: str,
        papers_dir: str | Path,
        project_root: str,
        logger: Any | None = None,
    ) -> Path | None:
        papers_path = Path(papers_dir)
        papers_path.mkdir(parents=True, exist_ok=True)
        normalized = self.normalize_doi(doi)
        local_path = papers_path / self.build_paper_filename(normalized)
        lock = self._get_paper_download_lock(local_path)

        with lock:
            backend = get_storage_backend(project_root=project_root)
            if isinstance(backend, MinIOStorageBackend):
                object_name = self.build_paper_object_name(normalized)
                if local_path.exists() and local_path.is_file():
                    null_logger = logger or type("_NullLogger", (), {"warning": lambda *args, **kwargs: None})()
                    if not backend.object_exists(object_name=object_name):
                        self.mirror_file(
                            local_path=str(local_path),
                            object_name=object_name,
                            content_type="application/pdf",
                            project_root=project_root,
                            logger=null_logger,
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
                    if backend.object_exists(object_name=object_name) and backend.download_file(
                        object_name=object_name,
                        local_path=str(tmp_path),
                    ):
                        os.replace(tmp_path, local_path)
                        return local_path
                except Exception as exc:
                    if logger is not None:
                        logger.warning(f"paper object download failed: {exc}")
                finally:
                    if tmp_path.exists():
                        try:
                            tmp_path.unlink()
                        except Exception:
                            pass

            if local_path.exists() and local_path.is_file():
                return local_path
            return None

    def cleanup_resources(self, *, file_row: dict[str, Any], project_root: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "storage_attempted": False,
            "storage_deleted": False,
            "local_attempted": False,
            "local_deleted": False,
            "errors": [],
        }
        storage_ref = str(file_row.get("storage_ref") or "").strip()
        local_path_text = str(file_row.get("local_path") or "").strip()
        parsed = self.parse_storage_ref(storage_ref)

        if parsed and parsed.get("scheme") == "minio" and parsed.get("object_name"):
            result["storage_attempted"] = True
            try:
                backend = get_storage_backend(project_root=project_root)
                result["storage_deleted"] = bool(
                    backend.delete_object(
                        object_name=str(parsed.get("object_name") or ""),
                        bucket=str(parsed.get("bucket") or "") or None,
                    )
                )
            except Exception as exc:
                result["errors"].append(f"storage_delete_failed:{exc}")

        if local_path_text:
            result["local_attempted"] = True
            try:
                local_path = Path(local_path_text)
                if local_path.exists() and local_path.is_file():
                    local_path.unlink()
                    result["local_deleted"] = True
                elif not local_path.exists():
                    result["local_deleted"] = True
                else:
                    result["errors"].append("local_path_not_file")
            except Exception as exc:
                result["errors"].append(f"local_delete_failed:{exc}")
        return result

    def resolve_download(
        self,
        *,
        file_row: dict[str, Any],
        project_root: str,
        use_proxy: bool,
        expires_seconds: int,
    ) -> dict[str, Any] | None:
        file_name = str(file_row.get("file_name") or "file")
        local_path = str(file_row.get("local_path") or "").strip()
        storage_ref = str(file_row.get("storage_ref") or "").strip()
        parsed = self.parse_storage_ref(storage_ref)

        if parsed and parsed.get("scheme") == "minio" and parsed.get("object_name"):
            backend = get_storage_backend(project_root=project_root)
            object_name = str(parsed.get("object_name") or "")
            if not use_proxy:
                url = backend.get_file_url(object_name=object_name, expires_seconds=expires_seconds)
                return {"mode": "redirect", "target": url, "file_name": file_name}

            suffix = Path(file_name).suffix or ".bin"
            fd, temp_path = tempfile.mkstemp(prefix="fastapi-storage-", suffix=suffix)
            os.close(fd)
            ok = backend.download_file(object_name=object_name, local_path=temp_path)
            if ok:
                return {"mode": "proxy_file", "target": temp_path, "file_name": file_name}
            try:
                os.remove(temp_path)
            except Exception:
                pass

        if parsed and parsed.get("scheme") == "local" and parsed.get("local_path"):
            candidate = Path(str(parsed.get("local_path")))
            if candidate.exists() and candidate.is_file():
                return {"mode": "local_file", "target": str(candidate), "file_name": file_name}

        if local_path:
            candidate = Path(local_path)
            if candidate.exists() and candidate.is_file():
                return {"mode": "local_file", "target": str(candidate), "file_name": file_name}
        return None


storage_service = StorageService()
