"""File download response helpers for conversation uploads."""

from __future__ import annotations

from dataclasses import dataclass
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

from server.storage.storage_factory import get_storage_backend


@dataclass(frozen=True)
class FileDeliveryPlan:
    kind: str
    download_name: str
    local_path: str | None = None
    redirect_url: str | None = None
    cleanup_path: str | None = None


def _parse_storage_ref(storage_ref: str | None) -> tuple[str, str] | None:
    if not storage_ref:
        return None
    raw = storage_ref.strip()
    if raw.startswith("minio://"):
        value = raw[len("minio://") :]
        if "/" not in value:
            return None
        bucket, object_name = value.split("/", 1)
        return ("minio", f"{bucket}/{object_name}")
    if raw.startswith("local://"):
        return ("local", raw[len("local://") :])
    return None


def _bool_env(key: str, default: bool) -> bool:
    value = str(os.getenv(key, "1" if default else "0")).strip().lower()
    return value in {"1", "true", "yes", "on"}


def resolve_uploaded_file_delivery(*, file_row: dict[str, Any], logger: Any) -> FileDeliveryPlan | None:
    """Resolve file delivery into a framework-neutral plan."""
    file_name = str(file_row.get("file_name") or "file")
    local_path = str(file_row.get("local_path") or "").strip()
    storage_ref = str(file_row.get("storage_ref") or "").strip()

    parsed = _parse_storage_ref(storage_ref)
    if parsed:
        scheme, value = parsed
        if scheme == "minio" and "/" in value:
            _, object_name = value.split("/", 1)
            backend = get_storage_backend(project_root=str(Path(__file__).resolve().parents[2]))
            use_proxy = _bool_env("MINIO_USE_PROXY", True)
            try:
                expires = int(str(os.getenv("MINIO_DOWNLOAD_EXPIRES", "3600")).strip() or "3600")
            except Exception:
                expires = 3600

            if not use_proxy:
                try:
                    url = backend.get_file_url(object_name=object_name, expires_seconds=expires)
                    return FileDeliveryPlan(kind="redirect", redirect_url=url, download_name=file_name)
                except Exception as exc:  # pragma: no cover - runtime env specific
                    logger.warning("build presigned url failed: %s", exc)
            else:
                suffix = Path(file_name).suffix or ".bin"
                fd, temp_path = tempfile.mkstemp(prefix="highthinking-download-", suffix=suffix)
                os.close(fd)
                ok = False
                try:
                    ok = backend.download_file(object_name=object_name, local_path=temp_path)
                except Exception as exc:  # pragma: no cover - runtime env specific
                    logger.warning("download minio object failed: %s", exc)

                if ok:
                    return FileDeliveryPlan(
                        kind="file",
                        local_path=temp_path,
                        cleanup_path=temp_path,
                        download_name=file_name,
                    )

                try:
                    os.remove(temp_path)
                except Exception:
                    pass

        if scheme == "local":
            candidate = Path(value)
            if candidate.exists() and candidate.is_file():
                return FileDeliveryPlan(kind="file", local_path=str(candidate), download_name=file_name)

    if local_path:
        candidate = Path(local_path)
        if candidate.exists() and candidate.is_file():
            return FileDeliveryPlan(kind="file", local_path=str(candidate), download_name=file_name)

    return None


def build_uploaded_file_response(
    *,
    file_row: dict[str, Any],
    send_file_fn: Callable[..., Any],
    redirect_fn: Callable[..., Any],
    logger: Any,
) -> Any:
    """Build a Flask file response from conversation file metadata."""
    plan = resolve_uploaded_file_delivery(file_row=file_row, logger=logger)
    if plan is None:
        return None
    if plan.kind == "redirect" and plan.redirect_url:
        return redirect_fn(plan.redirect_url, code=302)
    if plan.kind != "file" or not plan.local_path:
        return None

    response = send_file_fn(plan.local_path, as_attachment=True, download_name=plan.download_name)
    if plan.cleanup_path:
        cleanup_path = plan.cleanup_path

        @response.call_on_close
        def _cleanup():
            try:
                os.remove(cleanup_path)
            except Exception:
                pass

    return response
