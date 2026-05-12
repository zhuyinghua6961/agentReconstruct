"""FastAPI upload routes for PDF and Excel files."""

# Deprecated: this router is no longer registered in the current architecture.
# Upload HTTP APIs are owned by public-service behind gateway public proxy.


from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from werkzeug.utils import secure_filename

import config
from server.services.conversation.conversation_service import conversation_service
from server.storage.upload_service import mirror_file_to_object_storage
from server_fastapi.auth.deps import AuthContext, require_auth_context

router = APIRouter()

_EXCEL_EXTS = {".xls", ".xlsx", ".csv"}
_EXCEL_CONTENT_TYPES = {
    ".csv": "text/csv",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        num = int(value)
    except Exception:
        return None
    return num if num > 0 else None


def _upload_base_dir(request: Request) -> Path:
    raw = str(os.getenv("UPLOAD_DIR", "")).strip() or str(request.app.state.config.get("UPLOAD_DIR", config.UPLOAD_DIR))
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path(config.SERVICE_STATE_ROOT) / path
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _timestamped_name(file_name: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = secure_filename(file_name) or "upload.bin"
    return f"{ts}_{safe_name}"


def _missing_file_response() -> tuple[dict[str, Any], int]:
    return {"error": "没有文件被上传"}, 200


async def _save_uploaded_file(
    *,
    upload: UploadFile,
    request: Request,
    category: str,
    content_type: str,
) -> tuple[Path, str, int | None, str | None]:
    original_name = str(upload.filename or "")
    saved_name = _timestamped_name(original_name)
    target_dir = _upload_base_dir(request) / category
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / saved_name

    await upload.seek(0)
    with target_path.open("wb") as handle:
        shutil.copyfileobj(upload.file, handle)

    size_bytes = int(target_path.stat().st_size) if target_path.exists() else None
    storage_ref = mirror_file_to_object_storage(
        local_path=str(target_path),
        object_name=f"uploads/{category}/{saved_name}",
        content_type=content_type,
        project_root=str(config.SERVICE_STATE_ROOT),
        logger=request.app.logger,
    )
    return target_path, original_name, size_bytes, storage_ref


def _persist_upload_metadata_if_needed(
    *,
    request: Request,
    user_id: int | None,
    conversation_id: str | None,
    file_type: str,
    file_name: str,
    local_path: str,
    content_type: str,
    size_bytes: int | None,
    storage_ref: str | None,
) -> dict[str, Any]:
    if not bool(request.app.state.config.get("chat_persistence_enabled", False)):
        return {}

    user_id_num = _to_int(user_id)
    conversation_id_num = _to_int(conversation_id)
    if not user_id_num or not conversation_id_num:
        return {}

    result = conversation_service.add_uploaded_file(
        user_id=int(user_id_num),
        conversation_id=int(conversation_id_num),
        file_type=file_type,
        file_name=file_name,
        local_path=local_path,
        storage_ref=storage_ref,
        content_type=content_type,
        size_bytes=size_bytes,
    )
    if not result.get("success"):
        request.app.logger.warning("persist upload metadata skipped: %s", result)
        return {}

    file_id = int((result.get("data") or {}).get("file_id") or 0)
    if file_id <= 0:
        return {}
    return {
        "file_id": file_id,
        "parse_status": "uploaded",
        "index_status": "pending",
        "processing_stage": "uploaded",
        "processing_queued": False,
    }


def _payload_success(
    *,
    original_name: str,
    target_path: Path,
    size_bytes: int | None,
    content_type: str,
    storage_ref: str | None,
    extra: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], int]:
    payload: dict[str, Any] = {
        "message": f"文件 {original_name} 上传成功",
        "filename": original_name,
        "filepath": str(target_path),
        "size_bytes": size_bytes,
        "content_type": content_type,
    }
    if storage_ref:
        payload["storage_ref"] = storage_ref
    if extra:
        payload.update(extra)
    return payload, 200


@router.post("/api/v1/upload_pdf")
@router.post("/api/upload_pdf")
@router.post("/upload_pdf")
async def upload_pdf(
    request: Request,
    file: UploadFile | None = File(default=None),
    conversation_id: str | None = Form(default=None),
    context: AuthContext = Depends(require_auth_context),
):
    if file is None:
        payload, status = _missing_file_response()
        return JSONResponse(content=payload, status_code=status)
    if not str(file.filename or "").strip():
        return JSONResponse(content={"error": "没有选择文件"}, status_code=200)
    if Path(str(file.filename)).suffix.lower() != ".pdf":
        return JSONResponse(content={"error": "只支持PDF文件"}, status_code=200)

    try:
        target_path, original_name, size_bytes, storage_ref = await _save_uploaded_file(
            upload=file,
            request=request,
            category="pdf",
            content_type="application/pdf",
        )
        extra = _persist_upload_metadata_if_needed(
            request=request,
            user_id=context.user_id,
            conversation_id=conversation_id,
            file_type="pdf",
            file_name=original_name,
            local_path=str(target_path),
            content_type="application/pdf",
            size_bytes=size_bytes,
            storage_ref=storage_ref,
        )
        payload, status = _payload_success(
            original_name=original_name,
            target_path=target_path,
            size_bytes=size_bytes,
            content_type="application/pdf",
            storage_ref=storage_ref,
            extra=extra,
        )
        return JSONResponse(content=payload, status_code=status)
    except Exception as exc:  # pragma: no cover - runtime env specific
        request.app.logger.error("upload pdf failed: %s", exc)
        return JSONResponse(content={"error": f"上传失败: {exc}"}, status_code=200)


@router.post("/api/v1/upload_excel")
@router.post("/api/upload_excel")
@router.post("/upload_excel")
async def upload_excel(
    request: Request,
    file: UploadFile | None = File(default=None),
    conversation_id: str | None = Form(default=None),
    context: AuthContext = Depends(require_auth_context),
):
    if file is None:
        payload, status = _missing_file_response()
        return JSONResponse(content=payload, status_code=status)
    if not str(file.filename or "").strip():
        return JSONResponse(content={"error": "没有选择文件"}, status_code=200)

    suffix = Path(str(file.filename)).suffix.lower()
    if suffix not in _EXCEL_EXTS:
        return JSONResponse(content={"error": "只支持 Excel 或 CSV 文件 (.xls/.xlsx/.csv)"}, status_code=200)

    content_type = _EXCEL_CONTENT_TYPES.get(
        suffix,
        str(file.content_type or "").strip() or "application/octet-stream",
    )

    try:
        target_path, original_name, size_bytes, storage_ref = await _save_uploaded_file(
            upload=file,
            request=request,
            category="excel",
            content_type=content_type,
        )
        extra = _persist_upload_metadata_if_needed(
            request=request,
            user_id=context.user_id,
            conversation_id=conversation_id,
            file_type="excel",
            file_name=original_name,
            local_path=str(target_path),
            content_type=content_type,
            size_bytes=size_bytes,
            storage_ref=storage_ref,
        )
        payload, status = _payload_success(
            original_name=original_name,
            target_path=target_path,
            size_bytes=size_bytes,
            content_type=content_type,
            storage_ref=storage_ref,
            extra=extra,
        )
        return JSONResponse(content=payload, status_code=status)
    except Exception as exc:  # pragma: no cover - runtime env specific
        request.app.logger.error("upload excel failed: %s", exc)
        return JSONResponse(content={"error": f"上传失败: {exc}"}, status_code=200)
