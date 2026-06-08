from __future__ import annotations

import logging
import secrets
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from typing import Any

from app.modules.departments.service import department_service as shared_department_service
from app.modules.auth.repository import AuthRepository
from app.modules.auth.service import auth_service
from app.modules.personnel.service import personnel_service as shared_personnel_service


logger = logging.getLogger(__name__)


def _is_db_unavailable_error(exc: Exception) -> bool:
    return exc.__class__.__name__ in {"DatabaseConfigError", "DatabaseConnectionError", "DatabaseUnavailableError"}


class AdminUsersService:
    def __init__(
        self,
        *,
        users_repo: AuthRepository | None = None,
        department_service: Any | None = None,
        personnel_service: Any | None = None,
    ) -> None:
        self._users = users_repo or AuthRepository()
        self._departments = department_service or shared_department_service
        self._personnel = personnel_service or shared_personnel_service

    @property
    def users(self) -> AuthRepository:
        return self._users

    @staticmethod
    def hash_password(password: str, *, iterations: int = 120_000) -> str:
        salt = secrets.token_hex(16)
        digest = pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations).hex()
        return f"pbkdf2_sha256${iterations}${salt}${digest}"

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        try:
            algo, iter_text, salt, digest_hex = str(password_hash or "").split("$", 3)
        except ValueError:
            return False
        if algo != "pbkdf2_sha256":
            return False
        try:
            iterations = int(iter_text)
        except ValueError:
            return False
        expected = pbkdf2_hmac(
            "sha256",
            str(password or "").encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        ).hex()
        return compare_digest(expected, digest_hex)

    @staticmethod
    def _role_to_user_type(role: str) -> int:
        if role == "admin":
            return 1
        if role == "super":
            return 2
        return 3

    @staticmethod
    def _password_history_limit(role: str) -> int:
        return 5 if str(role or "").strip().lower() == "admin" else 3

    @staticmethod
    def clean_text(value: object) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize_optional_int(value: object) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _build_personnel_display(self, payload: dict[str, Any]) -> str:
        binding_status = self.clean_text(payload.get("personnel_binding_status")).lower()
        employee_no = self.clean_text(payload.get("employee_no"))
        full_name = self.clean_text(payload.get("full_name"))
        parts = [part for part in (employee_no, full_name) if part]
        base = " / ".join(parts)
        if binding_status == "unbound":
            return "未绑定"
        if binding_status == "bound_missing":
            return base or "绑定记录缺失"
        if binding_status == "bound_disabled":
            return f"{base}（已停用）" if base else "已停用"
        return base or "未绑定"

    def _build_user_payload(self, user: dict[str, Any]) -> dict[str, Any]:
        role = str(user.get("role") or "user")
        user_type = int(user.get("user_type") or self._role_to_user_type(role))
        department_payload = dict(
            self._departments.describe_user_department(
                primary_department_id=user.get("primary_department_id"),
                secondary_department_id=user.get("secondary_department_id"),
                tertiary_department_id=user.get("tertiary_department_id"),
            )
        )
        if user_type == 1:
            department_payload["require_department_setup"] = False
        if not department_payload.get("department_display"):
            parts = [
                part
                for part in (
                    department_payload.get("primary_department_name"),
                    department_payload.get("secondary_department_name"),
                    department_payload.get("tertiary_department_name"),
                )
                if part
            ]
            department_payload["department_display"] = " / ".join(parts) if parts else "未填写"
        personnel_payload = dict(
            self._personnel.describe_user_personnel(
                personnel_id=user.get("personnel_id"),
            )
        )
        return {
            "id": int(user["id"]),
            "username": user["username"],
            "role": role,
            "user_type": user_type,
            "status": user["status"],
            **department_payload,
            **personnel_payload,
            "personnel_display": self._build_personnel_display(personnel_payload),
            "created_at": user["created_at"].isoformat() if user.get("created_at") else None,
        }

    def _validate_username_candidate(self, *, username: str, owner_user_id: int | None = None) -> dict[str, Any]:
        validation_service = auth_service.__class__(
            repo=self._users,
            token_service=auth_service._tokens,
            department_service=self._departments,
        )
        return validation_service.validate_username_candidate(username=username, owner_user_id=owner_user_id)

    def _parse_user_type_code(self, value: object) -> int | None:
        text = self.clean_text(value).lower()
        if text in {"super", "2"}:
            return 2
        if text in {"common", "3"}:
            return 3
        return None

    @staticmethod
    def _department_triplet_from_mapping(data: dict[str, Any] | None) -> tuple[int | None, int | None, int | None]:
        if not data:
            return (None, None, None)
        primary_id = data.get("primary_department_id")
        secondary_id = data.get("secondary_department_id")
        tertiary_id = data.get("tertiary_department_id")
        return (
            int(primary_id) if primary_id is not None else None,
            int(secondary_id) if secondary_id is not None else None,
            int(tertiary_id) if tertiary_id is not None else None,
        )

    @staticmethod
    def _db_error(exc: Exception) -> dict[str, Any]:
        return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}

    @staticmethod
    def _duplicate_error(exc: Exception) -> bool:
        message = str(exc or "").lower()
        return "duplicate" in message or "unique" in message or "1062" in message

    @staticmethod
    def status_code_for(result: dict[str, Any], *, ok_status: int) -> int:
        if result.get("success"):
            return ok_status
        code = str(result.get("code") or "")
        if code in {
            "VALIDATION_ERROR",
            "USERNAME_INVALID",
            "NOT_SUPPORTED",
            "INVALID_FILE_TYPE",
            "FILE_MISSING",
            "FILENAME_EMPTY",
            "INVALID_FORMAT",
            "PASSWORD_TOO_SHORT",
            "PASSWORD_NO_LOWERCASE",
            "PASSWORD_NO_UPPERCASE",
            "PASSWORD_NO_DIGIT",
            "PASSWORD_NO_SYMBOL",
            "PASSWORD_WEAK",
            "DEPARTMENT_REQUIRED",
            "DEPARTMENT_RELATION_INVALID",
            "DEPARTMENT_DISABLED",
            "PERSONNEL_DISABLED",
            "DEPARTMENT_MANAGED_BY_PERSONNEL",
        }:
            return 400
        if code in {"PERMISSION_DENIED"}:
            return 403
        if code in {"USERNAME_EXISTS"}:
            return 409
        if code in {"QUOTA_EXCEEDED"}:
            return 429
        if code in {
            "USER_NOT_FOUND",
            "PRIMARY_DEPARTMENT_NOT_FOUND",
            "SECONDARY_DEPARTMENT_NOT_FOUND",
            "TERTIARY_DEPARTMENT_NOT_FOUND",
            "PERSONNEL_NOT_FOUND",
        }:
            return 404
        if code in {"DB_UNAVAILABLE"}:
            return 503
        return 500

    def list_users(self, *, page: int, page_size: int) -> dict[str, Any]:
        try:
            page = max(1, int(page))
            page_size = int(page_size)
            if page_size < 1 or page_size > 100:
                page_size = 10
            offset = (page - 1) * page_size
            total = self._users.count_users()
            rows = self._users.list_users(offset=offset, limit=page_size)
            data = [self._build_user_payload(row) for row in rows]
            return {
                "success": True,
                "data": data,
                "pagination": {"page": page, "page_size": page_size, "total": total},
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "获取用户列表失败", "code": "FETCH_ERROR"}

    def create_user(
        self,
        *,
        username: str,
        password: str,
        user_type: str,
    ) -> dict[str, Any]:
        try:
            username = self.clean_text(username)
            password = str(password or "")
            user_type = self.clean_text(user_type or "common").lower()
            if not username or not password:
                return {"success": False, "error": "用户名和密码不能为空", "code": "VALIDATION_ERROR"}
            if user_type not in {"common", "super"}:
                return {"success": False, "error": "用户类型必须是 super 或 common", "code": "VALIDATION_ERROR"}
            username_validation = self._validate_username_candidate(username=username)
            if not username_validation.get("success"):
                return username_validation
            normalized_username = str(username_validation.get("data", {}).get("username") or "").strip()
            user_type_code = 2 if user_type == "super" else 3
            password_hash = self.hash_password(password)
            created_id = self._users.create_user(
                username=normalized_username,
                password_hash=password_hash,
                role="user",
                user_type=user_type_code,
                is_first_login=True,
                must_set_security_questions=True,
            )
            self._users.add_password_history(user_id=created_id, password_hash=password_hash)
            self._users.trim_password_history(user_id=created_id, keep_limit=self._password_history_limit("user"))
            return {
                "success": True,
                "message": f"用户 {normalized_username} 创建成功",
                "data": {
                    "id": created_id,
                    "username": normalized_username,
                    "role": "user",
                    "user_type": user_type_code,
                    "status": "active",
                },
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            if self._duplicate_error(exc):
                return {"success": False, "error": "用户名已存在", "code": "USERNAME_EXISTS"}
            return {"success": False, "error": "创建用户失败", "code": "CREATE_ERROR"}

    def update_username(self, *, target_user_id: int, username: str) -> dict[str, Any]:
        try:
            user = self._users.get_by_id(target_user_id)
            if not user:
                return {"success": False, "error": "用户不存在", "code": "USER_NOT_FOUND"}
            role_text = str(user.get("role") or "").strip().lower()
            if role_text == "admin" or int(user.get("user_type") or 0) == 1:
                return {"success": False, "error": "不能修改管理员用户名", "code": "PERMISSION_DENIED"}
            username_validation = self._validate_username_candidate(username=username, owner_user_id=target_user_id)
            if not username_validation.get("success"):
                return username_validation
            normalized_username = str(username_validation.get("data", {}).get("username") or "").strip()
            updated_count = self._users.update_username(user_id=target_user_id, username=normalized_username)
            refreshed_user = self._users.get_by_id(target_user_id) or user
            current_username = str(refreshed_user.get("username") or "").strip()
            if updated_count <= 0 and current_username != normalized_username:
                return {"success": False, "error": "修改用户名失败", "code": "UPDATE_ERROR"}
            return {
                "success": True,
                "message": "用户名已更新",
                "data": {
                    "id": int(refreshed_user.get("id") or user["id"]),
                    "username": normalized_username,
                    "role": str(refreshed_user.get("role") or user.get("role") or "user"),
                    "user_type": int(refreshed_user.get("user_type") or self._role_to_user_type(role_text or "user")),
                    "status": str(refreshed_user.get("status") or user.get("status") or "active"),
                },
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            if self._duplicate_error(exc):
                return {"success": False, "error": "用户名已存在", "code": "USERNAME_EXISTS"}
            return {"success": False, "error": "修改用户名失败", "code": "UPDATE_ERROR"}

    def update_user_personnel_binding(self, *, target_user_id: int, actor_user_id: int, personnel_id: int) -> dict[str, Any]:
        try:
            user = self._users.get_by_id(target_user_id)
            if not user:
                return {"success": False, "error": "用户不存在", "code": "USER_NOT_FOUND"}
            target_personnel_id = int(personnel_id)
            record = self._personnel.get_personnel_by_id(personnel_id=target_personnel_id)
            if not record:
                return {"success": False, "error": "人员不存在", "code": "PERSONNEL_NOT_FOUND"}
            if self.clean_text(record.get("status")).lower() != "active":
                return {"success": False, "error": "该人员已停用", "code": "PERSONNEL_DISABLED"}
            department_payload = self._departments.describe_user_department(
                primary_department_id=record.get("primary_department_id"),
                secondary_department_id=record.get("secondary_department_id"),
                tertiary_department_id=record.get("tertiary_department_id"),
            )
            if (
                not isinstance(department_payload, dict)
                or department_payload.get("primary_department_id") is None
                or bool(department_payload.get("require_department_setup"))
            ):
                return {
                    "success": False,
                    "error": "绑定人员未维护完整部门信息，请联系管理员",
                    "code": "DEPARTMENT_REQUIRED",
                }

            old_personnel_id = self._normalize_optional_int(user.get("personnel_id"))
            bind_user_personnel_with_departments = getattr(self._users, "bind_user_personnel_with_departments", None)
            if callable(bind_user_personnel_with_departments):
                updated_count = bind_user_personnel_with_departments(
                    user_id=target_user_id,
                    personnel_id=target_personnel_id,
                    primary_department_id=record.get("primary_department_id"),
                    secondary_department_id=record.get("secondary_department_id"),
                    tertiary_department_id=record.get("tertiary_department_id"),
                )
            else:
                updated_count = self._users.update_user_personnel(user_id=target_user_id, personnel_id=target_personnel_id)
                sync_departments_for_personnel = getattr(self._users, "sync_departments_for_personnel", None)
                if callable(sync_departments_for_personnel):
                    sync_departments_for_personnel(
                        personnel_id=target_personnel_id,
                        primary_department_id=record.get("primary_department_id"),
                        secondary_department_id=record.get("secondary_department_id"),
                        tertiary_department_id=record.get("tertiary_department_id"),
                    )
            refreshed_user = self._users.get_by_id(target_user_id) or user
            current_personnel_id = self._normalize_optional_int(refreshed_user.get("personnel_id"))
            if updated_count <= 0 and current_personnel_id != target_personnel_id:
                return {"success": False, "error": "修改用户人员绑定失败", "code": "UPDATE_ERROR"}

            logger.info(
                "personnel_bound_by_admin",
                extra={
                    "event": "personnel_bound_by_admin",
                    "actor_user_id": int(actor_user_id),
                    "target_user_id": int(target_user_id),
                    "old_personnel_id": old_personnel_id,
                    "new_personnel_id": target_personnel_id,
                },
            )
            updated_user = {
                **refreshed_user,
                "personnel_id": target_personnel_id,
                "primary_department_id": record.get("primary_department_id"),
                "secondary_department_id": record.get("secondary_department_id"),
                "tertiary_department_id": record.get("tertiary_department_id"),
            }
            return {
                "success": True,
                "message": "用户人员绑定已更新",
                "data": self._build_user_payload(updated_user),
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "修改用户人员绑定失败", "code": "UPDATE_ERROR"}

    def clear_user_personnel_binding(self, *, target_user_id: int, actor_user_id: int) -> dict[str, Any]:
        try:
            user = self._users.get_by_id(target_user_id)
            if not user:
                return {"success": False, "error": "用户不存在", "code": "USER_NOT_FOUND"}

            old_personnel_id = self._normalize_optional_int(user.get("personnel_id"))
            clear_user_personnel_with_department_cache = getattr(self._users, "clear_user_personnel_with_department_cache", None)
            if callable(clear_user_personnel_with_department_cache):
                updated_count = clear_user_personnel_with_department_cache(user_id=target_user_id)
            else:
                updated_count = self._users.update_user_personnel(user_id=target_user_id, personnel_id=None)
                clear_user_department_cache = getattr(self._users, "clear_user_department_cache", None)
                if callable(clear_user_department_cache):
                    clear_user_department_cache(user_id=target_user_id)
            refreshed_user = self._users.get_by_id(target_user_id) or user
            current_personnel_id = self._normalize_optional_int(refreshed_user.get("personnel_id"))
            if updated_count <= 0 and current_personnel_id is not None:
                return {"success": False, "error": "解绑用户人员失败", "code": "UPDATE_ERROR"}

            logger.info(
                "personnel_unbound_by_admin",
                extra={
                    "event": "personnel_unbound_by_admin",
                    "actor_user_id": int(actor_user_id),
                    "target_user_id": int(target_user_id),
                    "old_personnel_id": old_personnel_id,
                    "new_personnel_id": None,
                },
            )
            updated_user = {
                **refreshed_user,
                "personnel_id": None,
                "primary_department_id": None,
                "secondary_department_id": None,
                "tertiary_department_id": None,
            }
            return {
                "success": True,
                "message": "用户人员绑定已解除",
                "data": self._build_user_payload(updated_user),
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "解绑用户人员失败", "code": "UPDATE_ERROR"}

    def update_department(
        self,
        *,
        target_user_id: int,
        primary_department_id: int | None,
        secondary_department_id: int | None,
        tertiary_department_id: int | None = None,
    ) -> dict[str, Any]:
        del target_user_id, primary_department_id, secondary_department_id, tertiary_department_id
        return {
            "success": False,
            "error": "部门由人员信息维护，请联系管理员或修改绑定人员",
            "code": "DEPARTMENT_MANAGED_BY_PERSONNEL",
        }

    def reset_password(self, *, target_user_id: int, actor_user_id: int, new_password: str) -> dict[str, Any]:
        try:
            new_password = str(new_password or "")
            if not new_password:
                return {"success": False, "error": "新密码不能为空", "code": "VALIDATION_ERROR"}
            user = self._users.get_by_id(target_user_id)
            if not user:
                return {"success": False, "error": "用户不存在", "code": "USER_NOT_FOUND"}
            if int(actor_user_id or 0) == int(target_user_id):
                return {"success": False, "error": "请使用个人中心修改自己的密码", "code": "PERMISSION_DENIED"}
            password_validation = auth_service.validate_password_strength(password=new_password, role=str(user.get("role") or "user"))
            if not password_validation.get("success"):
                return password_validation
            new_hash = self.hash_password(new_password)
            self._users.update_password_hash(user_id=target_user_id, password_hash=new_hash)
            self._users.add_password_history(user_id=target_user_id, password_hash=new_hash)
            self._users.trim_password_history(
                user_id=target_user_id,
                keep_limit=self._password_history_limit(str(user.get("role") or "user")),
            )
            self._users.mark_first_login_required(user_id=target_user_id)
            self._users.set_security_setup_required(user_id=target_user_id, required=True)
            return {"success": True, "message": f"用户 {user['username']} 的密码已修改"}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "修改密码失败", "code": "PASSWORD_CHANGE_ERROR"}

    def get_password_hint(self, *, target_user_id: int) -> dict[str, Any]:
        try:
            user = self._users.get_by_id(target_user_id)
            if not user:
                return {"success": False, "error": "用户不存在", "code": "USER_NOT_FOUND"}
            return {
                "success": True,
                "data": {
                    "username": user["username"],
                    "password": "当前系统采用哈希存储，无法查看明文密码",
                },
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "获取密码失败", "code": "PASSWORD_FETCH_ERROR"}

    def update_status(self, *, target_user_id: int, actor_user_id: int, status: str) -> dict[str, Any]:
        try:
            status = self.clean_text(status).lower()
            if status not in {"active", "disabled"}:
                return {"success": False, "error": "状态必须是 active 或 disabled", "code": "VALIDATION_ERROR"}
            user = self._users.get_by_id(target_user_id)
            if not user:
                return {"success": False, "error": "用户不存在", "code": "USER_NOT_FOUND"}
            if int(actor_user_id or 0) == int(target_user_id) and status == "disabled":
                return {"success": False, "error": "不能停用自己的账号", "code": "PERMISSION_DENIED"}
            if str(user.get("role") or "") == "admin" and status == "disabled":
                return {"success": False, "error": "不能停用管理员账号", "code": "PERMISSION_DENIED"}
            self._users.update_status(user_id=target_user_id, status=status)
            return {"success": True, "message": "用户已停用" if status == "disabled" else "用户已启用"}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "修改用户状态失败", "code": "STATUS_CHANGE_ERROR"}

    def update_type(self, *, target_user_id: int, target_type_raw: object) -> dict[str, Any]:
        try:
            if not self._users.has_user_type_column():
                return {"success": False, "error": "当前数据库未启用user_type字段", "code": "NOT_SUPPORTED"}
            target_type = self._parse_user_type_code(target_type_raw)
            if target_type not in {2, 3}:
                return {"success": False, "error": "用户类型必须是 super/common 或 2/3", "code": "VALIDATION_ERROR"}
            user = self._users.get_by_id(target_user_id)
            if not user:
                return {"success": False, "error": "用户不存在", "code": "USER_NOT_FOUND"}
            role_text = str(user.get("role") or "").strip().lower()
            if role_text == "admin" or int(user.get("user_type") or 0) == 1:
                return {"success": False, "error": "不能修改管理员身份", "code": "PERMISSION_DENIED"}
            current_type = int(user.get("user_type") or self._role_to_user_type(role_text))
            if current_type == target_type:
                return {
                    "success": True,
                    "message": "用户身份无变化",
                    "data": {
                        "id": int(user["id"]),
                        "username": user["username"],
                        "user_type": current_type,
                        "role": role_text or "user",
                    },
                }
            self._users.update_user_type(user_id=target_user_id, user_type=target_type)
            updated = self._users.get_by_id(target_user_id) or user
            updated_role = str(updated.get("role") or "user")
            updated_type = int(updated.get("user_type") or target_type)
            label = "超级用户" if updated_type == 2 else "普通用户"
            return {
                "success": True,
                "message": f"用户 {updated.get('username') or user.get('username')} 已切换为{label}",
                "data": {
                    "id": int(updated.get("id") or user["id"]),
                    "username": updated.get("username") or user["username"],
                    "role": updated_role,
                    "user_type": updated_type,
                },
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "修改用户身份失败", "code": "TYPE_CHANGE_ERROR"}

    def delete_user(self, *, target_user_id: int, actor_user_id: int) -> dict[str, Any]:
        try:
            user = self._users.get_by_id(target_user_id)
            if not user:
                return {"success": False, "error": "用户不存在", "code": "USER_NOT_FOUND"}
            if int(actor_user_id or 0) == int(target_user_id):
                return {"success": False, "error": "不能删除自己的账号", "code": "PERMISSION_DENIED"}
            if str(user.get("role") or "") == "admin":
                return {"success": False, "error": "不能删除管理员账号", "code": "PERMISSION_DENIED"}
            self._users.delete_user(user_id=target_user_id)
            return {"success": True, "message": f"用户 {user['username']} 已删除"}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "删除用户失败", "code": "DELETE_ERROR"}

    def batch_delete_users(self, *, target_user_ids: list[int], actor_user_id: int) -> dict[str, Any]:
        try:
            normalized_ids: list[int] = []
            seen: set[int] = set()
            for item in list(target_user_ids or []):
                try:
                    parsed = int(item)
                except Exception:
                    continue
                if parsed <= 0 or parsed in seen:
                    continue
                seen.add(parsed)
                normalized_ids.append(parsed)
            if not normalized_ids:
                return {"success": False, "error": "请选择至少一个用户", "code": "VALIDATION_ERROR"}

            details: list[dict[str, Any]] = []
            success_count = 0
            failed_count = 0
            skipped_count = 0

            for index, user_id in enumerate(normalized_ids, start=1):
                user = self._users.get_by_id(user_id)
                if not user:
                    failed_count += 1
                    details.append(
                        {
                            "row": index,
                            "user_id": user_id,
                            "username": "",
                            "status": "failed",
                            "message": "用户不存在",
                        }
                    )
                    continue

                username = str(user.get("username") or "")
                if int(actor_user_id or 0) == int(user_id):
                    failed_count += 1
                    details.append(
                        {
                            "row": index,
                            "user_id": user_id,
                            "username": username,
                            "status": "failed",
                            "message": "不能删除自己的账号",
                        }
                    )
                    continue
                if str(user.get("role") or "").strip().lower() == "admin":
                    failed_count += 1
                    details.append(
                        {
                            "row": index,
                            "user_id": user_id,
                            "username": username,
                            "status": "failed",
                            "message": "不能删除管理员账号",
                        }
                    )
                    continue

                self._users.delete_user(user_id=user_id)
                success_count += 1
                details.append(
                    {
                        "row": index,
                        "user_id": user_id,
                        "username": username,
                        "status": "success",
                        "message": "删除成功",
                    }
                )

            return {
                "success": True,
                "message": "批量删除完成",
                "data": {
                    "summary": {
                        "total": len(normalized_ids),
                        "success": success_count,
                        "failed": failed_count,
                        "skipped": skipped_count,
                    },
                    "details": details,
                },
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "批量删除用户失败", "code": "BATCH_DELETE_ERROR"}

    def batch_change_user_type(self, *, target_user_ids: list[int], target_type_raw: object) -> dict[str, Any]:
        try:
            if not self._users.has_user_type_column():
                return {"success": False, "error": "当前数据库未启用user_type字段", "code": "NOT_SUPPORTED"}

            normalized_ids: list[int] = []
            seen: set[int] = set()
            for item in list(target_user_ids or []):
                try:
                    parsed = int(item)
                except Exception:
                    continue
                if parsed <= 0 or parsed in seen:
                    continue
                seen.add(parsed)
                normalized_ids.append(parsed)
            if not normalized_ids:
                return {"success": False, "error": "请选择至少一个用户", "code": "VALIDATION_ERROR"}

            target_type = self._parse_user_type_code(target_type_raw)
            if target_type not in {2, 3}:
                return {"success": False, "error": "用户类型必须是 super/common 或 2/3", "code": "VALIDATION_ERROR"}

            details: list[dict[str, Any]] = []
            success_count = 0
            failed_count = 0
            skipped_count = 0
            label = "超级用户" if target_type == 2 else "普通用户"

            for index, user_id in enumerate(normalized_ids, start=1):
                user = self._users.get_by_id(user_id)
                if not user:
                    failed_count += 1
                    details.append(
                        {
                            "row": index,
                            "user_id": user_id,
                            "username": "",
                            "status": "failed",
                            "message": "用户不存在",
                        }
                    )
                    continue

                username = str(user.get("username") or "")
                role_text = str(user.get("role") or "").strip().lower()
                if role_text == "admin" or int(user.get("user_type") or 0) == 1:
                    failed_count += 1
                    details.append(
                        {
                            "row": index,
                            "user_id": user_id,
                            "username": username,
                            "status": "failed",
                            "message": "不能修改管理员身份",
                        }
                    )
                    continue

                current_type = int(user.get("user_type") or self._role_to_user_type(role_text))
                if current_type == target_type:
                    skipped_count += 1
                    details.append(
                        {
                            "row": index,
                            "user_id": user_id,
                            "username": username,
                            "status": "skipped",
                            "message": f"用户已是{label}",
                        }
                    )
                    continue

                self._users.update_user_type(user_id=user_id, user_type=target_type)
                success_count += 1
                details.append(
                    {
                        "row": index,
                        "user_id": user_id,
                        "username": username,
                        "status": "success",
                        "message": f"已切换为{label}",
                    }
                )

            return {
                "success": True,
                "message": "批量修改用户类型完成",
                "data": {
                    "summary": {
                        "total": len(normalized_ids),
                        "success": success_count,
                        "failed": failed_count,
                        "skipped": skipped_count,
                    },
                    "details": details,
                },
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "批量修改用户身份失败", "code": "BATCH_TYPE_CHANGE_ERROR"}


admin_users_service = AdminUsersService()
