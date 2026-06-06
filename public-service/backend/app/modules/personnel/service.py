from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from typing import Any

from app.modules.auth.repository import AuthRepository
from app.modules.departments.service import department_service as shared_department_service
from app.modules.personnel.repository import PersonnelRepository, REMARKS_UNSET


logger = logging.getLogger(__name__)


def _is_db_unavailable_error(exc: Exception) -> bool:
    return exc.__class__.__name__ in {"DatabaseConfigError", "DatabaseConnectionError", "DatabaseUnavailableError"}


class PersonnelService:
    def __init__(
        self,
        *,
        repository: PersonnelRepository | None = None,
        department_service: Any | None = None,
        users_repo: AuthRepository | Any | None = None,
    ) -> None:
        self._repository = repository or PersonnelRepository()
        self._departments = department_service or shared_department_service
        self._users = users_repo or AuthRepository()

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
    def _normalize_optional_int(value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _department_triplet_from_mapping(cls, data: dict[str, Any] | None) -> tuple[int | None, int | None, int | None]:
        if not data:
            return (None, None, None)
        return (
            cls._normalize_optional_int(data.get("primary_department_id")),
            cls._normalize_optional_int(data.get("secondary_department_id")),
            cls._normalize_optional_int(data.get("tertiary_department_id")),
        )

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
            "DEPARTMENT_REQUIRED",
            "DEPARTMENT_RELATION_INVALID",
            "DEPARTMENT_DISABLED",
        }:
            return 400
        if code in {"PERSONNEL_NOT_FOUND", "PRIMARY_DEPARTMENT_NOT_FOUND", "SECONDARY_DEPARTMENT_NOT_FOUND", "TERTIARY_DEPARTMENT_NOT_FOUND"}:
            return 404
        if code in {"EMPLOYEE_NO_EXISTS", "PERSONNEL_HAS_BINDINGS"}:
            return 409
        if code in {"DB_UNAVAILABLE"}:
            return 503
        return 500

    def _describe_personnel_department(self, record: dict[str, Any] | None) -> dict[str, Any]:
        primary_department_id, secondary_department_id, tertiary_department_id = self._department_triplet_from_mapping(record)
        if primary_department_id is None and secondary_department_id is None and tertiary_department_id is None:
            return {
                "primary_department_id": None,
                "primary_department_name": None,
                "secondary_department_id": None,
                "secondary_department_name": None,
                "tertiary_department_id": None,
                "tertiary_department_name": None,
                "department_display": "未填写",
                "department_completion_level": "empty",
                "require_department_setup": True,
            }
        describe = getattr(self._departments, "describe_user_department", None)
        if not callable(describe):
            return {
                "primary_department_id": primary_department_id,
                "primary_department_name": None,
                "secondary_department_id": secondary_department_id,
                "secondary_department_name": None,
                "tertiary_department_id": tertiary_department_id,
                "tertiary_department_name": None,
                "department_display": "未填写",
                "department_completion_level": "invalid_partial",
                "require_department_setup": True,
            }
        payload = describe(
            primary_department_id=primary_department_id,
            secondary_department_id=secondary_department_id,
            tertiary_department_id=tertiary_department_id,
        )
        if isinstance(payload, dict):
            return payload
        return {
            "primary_department_id": primary_department_id,
            "primary_department_name": None,
            "secondary_department_id": secondary_department_id,
            "secondary_department_name": None,
            "tertiary_department_id": tertiary_department_id,
            "tertiary_department_name": None,
            "department_display": "未填写",
            "department_completion_level": "invalid_partial",
            "require_department_setup": True,
        }

    def _validate_personnel_department(
        self,
        *,
        primary_department_id: int | None,
        secondary_department_id: int | None,
        tertiary_department_id: int | None,
    ) -> dict[str, Any]:
        validate = getattr(self._departments, "validate_department_selection", None)
        if not callable(validate):
            return {"success": False, "error": "部门服务不可用", "code": "FETCH_ERROR"}
        return validate(
            primary_department_id=primary_department_id,
            secondary_department_id=secondary_department_id,
            tertiary_department_id=tertiary_department_id,
            require_active=True,
            allow_empty=False,
            allow_legacy_two_level=False,
        )

    def _sync_bound_user_departments(
        self,
        *,
        personnel_id: int,
        department_data: dict[str, Any] | None,
    ) -> None:
        sync = getattr(self._users, "sync_departments_for_personnel", None)
        if not callable(sync):
            return
        sync(
            personnel_id=int(personnel_id),
            primary_department_id=department_data.get("primary_department_id") if department_data else None,
            secondary_department_id=department_data.get("secondary_department_id") if department_data else None,
            tertiary_department_id=department_data.get("tertiary_department_id") if department_data else None,
        )

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
            **self._describe_personnel_department(record),
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
        primary_department_id: int | None = None,
        secondary_department_id: int | None = None,
        tertiary_department_id: int | None = None,
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

            department_validation = self._validate_personnel_department(
                primary_department_id=primary_department_id,
                secondary_department_id=secondary_department_id,
                tertiary_department_id=tertiary_department_id,
            )
            if not department_validation.get("success"):
                return department_validation
            department_data = department_validation.get("data") if isinstance(department_validation.get("data"), dict) else {}

            created_id = self._repository.create_personnel(
                employee_no=employee_no,
                full_name=full_name,
                verification_code_hash=self.hash_verification_code(verification_code),
                primary_department_id=department_data.get("primary_department_id"),
                secondary_department_id=department_data.get("secondary_department_id"),
                tertiary_department_id=department_data.get("tertiary_department_id"),
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
        primary_department_id: int | None = None,
        secondary_department_id: int | None = None,
        tertiary_department_id: int | None = None,
        status: str | None = None,
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

            department_validation = self._validate_personnel_department(
                primary_department_id=primary_department_id,
                secondary_department_id=secondary_department_id,
                tertiary_department_id=tertiary_department_id,
            )
            if not department_validation.get("success"):
                return department_validation
            department_data = department_validation.get("data") if isinstance(department_validation.get("data"), dict) else {}
            normalized_status = None
            if status is not None:
                normalized_status = self.clean_text(status).lower()
                if not self._valid_status(normalized_status):
                    return {"success": False, "error": "状态必须是 active 或 disabled", "code": "STATUS_INVALID"}

            update_payload: dict[str, Any] = {
                "personnel_id": int(personnel_id),
                "full_name": full_name,
                "primary_department_id": department_data.get("primary_department_id"),
                "secondary_department_id": department_data.get("secondary_department_id"),
                "tertiary_department_id": department_data.get("tertiary_department_id"),
            }
            if normalized_status is not None:
                update_payload["status"] = normalized_status
            if remarks is not REMARKS_UNSET:
                update_payload["remarks"] = self.clean_text(remarks) or None
            verification_code_text = self.clean_text(verification_code)
            if verification_code is not None and verification_code_text:
                update_payload["verification_code_hash"] = self.hash_verification_code(verification_code_text)

            previous_triplet = self._department_triplet_from_mapping(record)
            next_triplet = self._department_triplet_from_mapping(department_data)
            sync_bound_users = previous_triplet != next_triplet
            atomic_update = getattr(self._repository, "update_personnel_and_sync_bound_users", None)
            if callable(atomic_update):
                atomic_update(**update_payload, sync_bound_users=sync_bound_users)
            else:
                self._repository.update_personnel(**update_payload)
                if sync_bound_users:
                    self._sync_bound_user_departments(personnel_id=int(personnel_id), department_data=department_data)
            refreshed = self._repository.get_by_id(int(personnel_id)) or {**record, **update_payload}
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

    def delete_personnel(self, *, personnel_id: int) -> dict[str, Any]:
        try:
            normalized_id = int(personnel_id)
            record = self._repository.get_by_id(normalized_id)
            if not record:
                return {"success": False, "error": "人员不存在", "code": "PERSONNEL_NOT_FOUND"}
            if int(record.get("binding_count") or 0) > 0:
                return {"success": False, "error": "该人员仍有绑定账号，请先解绑后再删除", "code": "PERSONNEL_HAS_BINDINGS"}

            deleted_count = self._repository.delete_personnel(personnel_id=normalized_id)
            if deleted_count <= 0:
                return {"success": False, "error": "该人员仍有绑定账号，请先解绑后再删除", "code": "PERSONNEL_HAS_BINDINGS"}
            logger.info("personnel_deleted", extra={"event": "personnel_deleted", "personnel_id": normalized_id})
            return {
                "success": True,
                "message": f"人员 {record.get('employee_no')} / {record.get('full_name')} 已删除",
                "data": self._build_personnel_payload(record),
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "删除人员失败", "code": "DELETE_ERROR"}


personnel_service = PersonnelService()
