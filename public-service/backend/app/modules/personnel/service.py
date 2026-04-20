from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from typing import Any

from app.modules.personnel.repository import PersonnelRepository, REMARKS_UNSET


logger = logging.getLogger(__name__)


def _is_db_unavailable_error(exc: Exception) -> bool:
    return exc.__class__.__name__ in {"DatabaseConfigError", "DatabaseConnectionError", "DatabaseUnavailableError"}


class PersonnelService:
    def __init__(self, *, repository: PersonnelRepository | None = None) -> None:
        self._repository = repository or PersonnelRepository()

    @property
    def repository(self) -> PersonnelRepository:
        return self._repository

    @staticmethod
    def clean_text(value: object) -> str:
        return str(value or "").strip()

    @staticmethod
    def _db_error(exc: Exception) -> dict[str, Any]:
        return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}

    @staticmethod
    def _duplicate_error(exc: Exception) -> bool:
        message = str(exc or "").lower()
        return "duplicate" in message or "unique" in message or "1062" in message

    @staticmethod
    def _valid_status(value: object) -> bool:
        return str(value or "").strip().lower() in {"active", "disabled"}

    @staticmethod
    def hash_verification_code(verification_code: str, *, iterations: int = 120_000) -> str:
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            str(verification_code or "").encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        ).hex()
        return f"pbkdf2_sha256${iterations}${salt}${digest}"

    @staticmethod
    def verify_verification_code(verification_code: str, verification_code_hash: str) -> bool:
        try:
            algo, iter_text, salt, digest_hex = str(verification_code_hash or "").split("$", 3)
        except ValueError:
            return False
        if algo != "pbkdf2_sha256":
            return False
        try:
            iterations = int(iter_text)
        except ValueError:
            return False
        expected = hashlib.pbkdf2_hmac(
            "sha256",
            str(verification_code or "").encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        ).hex()
        return hmac.compare_digest(expected, digest_hex)

    @staticmethod
    def status_code_for(result: dict[str, Any], *, ok_status: int) -> int:
        if result.get("success"):
            return ok_status
        code = str(result.get("code") or "")
        if code in {
            "VALIDATION_ERROR",
            "EMPLOYEE_NO_REQUIRED",
            "FULL_NAME_REQUIRED",
            "VERIFICATION_CODE_REQUIRED",
            "STATUS_INVALID",
            "FILE_MISSING",
            "FILENAME_EMPTY",
            "INVALID_FILE_TYPE",
            "INVALID_FORMAT",
        }:
            return 400
        if code in {"PERSONNEL_NOT_FOUND"}:
            return 404
        if code in {"EMPLOYEE_NO_EXISTS"}:
            return 409
        if code in {"DB_UNAVAILABLE"}:
            return 503
        return 500

    def _build_personnel_payload(self, record: dict[str, Any] | None) -> dict[str, Any] | None:
        if not record:
            return None
        created_at = record.get("created_at")
        updated_at = record.get("updated_at")
        return {
            "id": int(record["id"]),
            "employee_no": record["employee_no"],
            "full_name": record["full_name"],
            "personnel_record_status": str(record.get("status") or "active"),
            "remarks": record.get("remarks"),
            "binding_count": int(record.get("binding_count") or 0),
            "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else created_at,
            "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
        }

    def get_personnel_by_id(self, *, personnel_id: int | None) -> dict[str, Any] | None:
        if personnel_id is None:
            return None
        return self._repository.get_by_id(int(personnel_id))

    def describe_user_personnel(self, *, personnel_id: int | None) -> dict[str, Any]:
        if personnel_id is None:
            return {
                "personnel_id": None,
                "employee_no": None,
                "full_name": None,
                "personnel_binding_status": "unbound",
                "require_personnel_setup": True,
            }

        record = self._repository.get_by_id(int(personnel_id))
        if not record:
            return {
                "personnel_id": int(personnel_id),
                "employee_no": None,
                "full_name": None,
                "personnel_binding_status": "bound_missing",
                "require_personnel_setup": True,
            }

        status = self.clean_text(record.get("status")).lower()
        binding_status = "bound_active" if status == "active" else "bound_disabled"
        return {
            "personnel_id": int(record["id"]),
            "employee_no": record.get("employee_no"),
            "full_name": record.get("full_name"),
            "personnel_binding_status": binding_status,
            "require_personnel_setup": binding_status != "bound_active",
        }

    def verify_personnel_identity(
        self,
        *,
        employee_no: str,
        full_name: str,
        verification_code: str,
    ) -> dict[str, Any]:
        employee_no = self.clean_text(employee_no)
        full_name = self.clean_text(full_name)
        verification_code = self.clean_text(verification_code)
        if not employee_no or not full_name or not verification_code:
            return {"success": False, "error": "人员信息不完整", "code": "VALIDATION_ERROR"}

        record = self._repository.get_by_employee_no(employee_no)
        if not record:
            return {"success": False, "error": "人员信息校验失败", "code": "PERSONNEL_BINDING_INVALID"}
        if self.clean_text(record.get("full_name")) != full_name:
            return {"success": False, "error": "人员信息校验失败", "code": "PERSONNEL_BINDING_INVALID"}
        if not self.verify_verification_code(verification_code, str(record.get("verification_code_hash") or "")):
            return {"success": False, "error": "人员信息校验失败", "code": "PERSONNEL_BINDING_INVALID"}
        if self.clean_text(record.get("status")).lower() != "active":
            return {"success": False, "error": "该人员已停用", "code": "PERSONNEL_DISABLED"}
        return {"success": True, "data": record}

    def list_personnel(
        self,
        *,
        page: int,
        page_size: int,
        employee_no: str = "",
        full_name: str = "",
        status: str = "",
        keyword: str = "",
    ) -> dict[str, Any]:
        try:
            page = max(1, int(page))
            page_size = int(page_size)
            if page_size < 1 or page_size > 100:
                page_size = 20
            offset = (page - 1) * page_size
            total = self._repository.count_personnel(
                employee_no=employee_no,
                full_name=full_name,
                status=status,
                keyword=keyword,
            )
            rows = self._repository.list_personnel(
                employee_no=employee_no,
                full_name=full_name,
                status=status,
                keyword=keyword,
                offset=offset,
                limit=page_size,
            )
            return {
                "success": True,
                "data": {"items": [self._build_personnel_payload(row) for row in rows]},
                "pagination": {"page": page, "page_size": page_size, "total": total},
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "获取人员列表失败", "code": "FETCH_ERROR"}

    def create_personnel(
        self,
        *,
        employee_no: str,
        full_name: str,
        verification_code: str,
        status: str = "active",
        remarks: str | None = None,
    ) -> dict[str, Any]:
        try:
            employee_no = self.clean_text(employee_no)
            full_name = self.clean_text(full_name)
            verification_code = self.clean_text(verification_code)
            status = self.clean_text(status).lower() or "active"
            remarks = self.clean_text(remarks) or None
            if not employee_no:
                return {"success": False, "error": "工号不能为空", "code": "EMPLOYEE_NO_REQUIRED"}
            if not full_name:
                return {"success": False, "error": "姓名不能为空", "code": "FULL_NAME_REQUIRED"}
            if not verification_code:
                return {"success": False, "error": "校验码不能为空", "code": "VERIFICATION_CODE_REQUIRED"}
            if not self._valid_status(status):
                return {"success": False, "error": "状态必须是 active 或 disabled", "code": "STATUS_INVALID"}
            if self._repository.get_by_employee_no(employee_no):
                return {"success": False, "error": "工号已存在", "code": "EMPLOYEE_NO_EXISTS"}
            created_id = self._repository.create_personnel(
                employee_no=employee_no,
                full_name=full_name,
                verification_code_hash=self.hash_verification_code(verification_code),
                status=status,
                remarks=remarks,
            )
            record = self._repository.get_by_id(created_id)
            logger.info("personnel_created", extra={"event": "personnel_created", "personnel_id": created_id, "employee_no": employee_no})
            return {"success": True, "message": "人员创建成功", "data": self._build_personnel_payload(record)}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            if self._duplicate_error(exc):
                return {"success": False, "error": "工号已存在", "code": "EMPLOYEE_NO_EXISTS"}
            return {"success": False, "error": "创建人员失败", "code": "CREATE_ERROR"}

    def update_personnel(
        self,
        *,
        personnel_id: int,
        full_name: str,
        remarks: object = REMARKS_UNSET,
        verification_code: str | None = None,
    ) -> dict[str, Any]:
        try:
            record = self._repository.get_by_id(int(personnel_id))
            if not record:
                return {"success": False, "error": "人员不存在", "code": "PERSONNEL_NOT_FOUND"}
            full_name = self.clean_text(full_name)
            if not full_name:
                return {"success": False, "error": "姓名不能为空", "code": "FULL_NAME_REQUIRED"}
            update_payload: dict[str, Any] = {
                "personnel_id": int(personnel_id),
                "full_name": full_name,
            }
            if remarks is not REMARKS_UNSET:
                update_payload["remarks"] = self.clean_text(remarks) or None
            verification_code_text = self.clean_text(verification_code)
            if verification_code is not None and verification_code_text:
                update_payload["verification_code_hash"] = self.hash_verification_code(verification_code_text)
            self._repository.update_personnel(**update_payload)
            refreshed = self._repository.get_by_id(int(personnel_id)) or record
            logger.info("personnel_updated", extra={"event": "personnel_updated", "personnel_id": int(personnel_id)})
            return {"success": True, "message": "人员更新成功", "data": self._build_personnel_payload(refreshed)}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "更新人员失败", "code": "UPDATE_ERROR"}

    def update_personnel_status(self, *, personnel_id: int, status: str) -> dict[str, Any]:
        try:
            record = self._repository.get_by_id(int(personnel_id))
            if not record:
                return {"success": False, "error": "人员不存在", "code": "PERSONNEL_NOT_FOUND"}
            status = self.clean_text(status).lower()
            if not self._valid_status(status):
                return {"success": False, "error": "状态必须是 active 或 disabled", "code": "STATUS_INVALID"}
            self._repository.update_personnel_status(personnel_id=int(personnel_id), status=status)
            refreshed = self._repository.get_by_id(int(personnel_id)) or {**record, "status": status}
            logger.info("personnel_status_changed", extra={"event": "personnel_status_changed", "personnel_id": int(personnel_id), "status": status})
            return {"success": True, "message": "人员状态更新成功", "data": self._build_personnel_payload(refreshed)}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "更新人员状态失败", "code": "UPDATE_ERROR"}

    def list_bindings(self, *, personnel_id: int) -> dict[str, Any]:
        try:
            record = self._repository.get_by_id(int(personnel_id))
            if not record:
                return {"success": False, "error": "人员不存在", "code": "PERSONNEL_NOT_FOUND"}
            return {"success": True, "data": {"items": self._repository.list_bindings(personnel_id=int(personnel_id))}}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "获取绑定账号失败", "code": "FETCH_ERROR"}


personnel_service = PersonnelService()
