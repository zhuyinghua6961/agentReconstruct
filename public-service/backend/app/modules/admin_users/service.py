from __future__ import annotations

import secrets
from hashlib import pbkdf2_hmac
from typing import Any

from app.modules.departments.service import department_service as shared_department_service
from app.modules.auth.repository import AuthRepository
from app.modules.auth.service import auth_service


def _is_db_unavailable_error(exc: Exception) -> bool:
    return exc.__class__.__name__ in {"DatabaseConfigError", "DatabaseConnectionError", "DatabaseUnavailableError"}


class AdminUsersService:
    def __init__(self, *, users_repo: AuthRepository | None = None, department_service: Any | None = None) -> None:
        self._users = users_repo or AuthRepository()
        self._departments = department_service or shared_department_service

    @property
    def users(self) -> AuthRepository:
        return self._users

    @staticmethod
    def hash_password(password: str, *, iterations: int = 120_000) -> str:
        salt = secrets.token_hex(16)
        digest = pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations).hex()
        return f"pbkdf2_sha256${iterations}${salt}${digest}"

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

    def _parse_user_type_code(self, value: object) -> int | None:
        text = self.clean_text(value).lower()
        if text in {"super", "2"}:
            return 2
        if text in {"common", "3"}:
            return 3
        return None

    @staticmethod
    def _department_pair_from_mapping(data: dict[str, Any] | None) -> tuple[int | None, int | None]:
        if not data:
            return (None, None)
        primary_id = data.get("primary_department_id")
        secondary_id = data.get("secondary_department_id")
        return (
            int(primary_id) if primary_id is not None else None,
            int(secondary_id) if secondary_id is not None else None,
        )

    @staticmethod
    def _db_error(exc: Exception) -> dict[str, Any]:
        return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}

    @staticmethod
    def status_code_for(result: dict[str, Any], *, ok_status: int) -> int:
        if result.get("success"):
            return ok_status
        code = str(result.get("code") or "")
        if code in {
            "VALIDATION_ERROR",
            "USERNAME_INVALID",
            "USERNAME_EXISTS",
            "PERMISSION_DENIED",
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
        }:
            return 400
        if code in {"QUOTA_EXCEEDED"}:
            return 429
        if code in {"USER_NOT_FOUND", "PRIMARY_DEPARTMENT_NOT_FOUND", "SECONDARY_DEPARTMENT_NOT_FOUND"}:
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
            data = []
            for row in rows:
                department_payload = self._departments.describe_user_department(
                    primary_department_id=row.get("primary_department_id"),
                    secondary_department_id=row.get("secondary_department_id"),
                )
                primary_name = department_payload.get("primary_department_name")
                secondary_name = department_payload.get("secondary_department_name")
                department_display = department_payload.get("department_display")
                if not department_display:
                    department_display = f"{primary_name} / {secondary_name}" if primary_name and secondary_name else "未填写"
                data.append(
                    {
                        "id": int(row["id"]),
                        "username": row["username"],
                        "role": row["role"],
                        "user_type": int(row.get("user_type") or self._role_to_user_type(str(row.get("role") or "user"))),
                        "status": row["status"],
                        **department_payload,
                        "department_display": department_display,
                        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
                    }
                )
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
        primary_department_id: int | None = None,
        secondary_department_id: int | None = None,
    ) -> dict[str, Any]:
        try:
            username = self.clean_text(username)
            password = str(password or "")
            user_type = self.clean_text(user_type or "common").lower()
            if not username or not password:
                return {"success": False, "error": "用户名和密码不能为空", "code": "VALIDATION_ERROR"}
            if len(username) < 3 or len(username) > 50:
                return {"success": False, "error": "用户名长度必须在3-50之间", "code": "VALIDATION_ERROR"}
            if user_type not in {"common", "super"}:
                return {"success": False, "error": "用户类型必须是 super 或 common", "code": "VALIDATION_ERROR"}
            if username.lower().startswith("admin"):
                return {"success": False, "error": "不能创建以 admin 为前缀的用户名", "code": "USERNAME_INVALID"}
            if self._users.get_by_username(username):
                return {"success": False, "error": "用户名已存在", "code": "USERNAME_EXISTS"}
            department_validation = self._departments.validate_department_selection(
                primary_department_id=primary_department_id,
                secondary_department_id=secondary_department_id,
                require_active=True,
                allow_empty=True,
            )
            if not department_validation.get("success"):
                return department_validation
            department_data = department_validation.get("data") if isinstance(department_validation.get("data"), dict) else {}
            user_type_code = 2 if user_type == "super" else 3
            password_hash = self.hash_password(password)
            created_id = self._users.create_user(
                username=username,
                password_hash=password_hash,
                role="user",
                user_type=user_type_code,
                is_first_login=True,
                must_set_security_questions=True,
                primary_department_id=department_data.get("primary_department_id"),
                secondary_department_id=department_data.get("secondary_department_id"),
            )
            self._users.add_password_history(user_id=created_id, password_hash=password_hash)
            self._users.trim_password_history(user_id=created_id, keep_limit=self._password_history_limit("user"))
            return {
                "success": True,
                "message": f"用户 {username} 创建成功",
                "data": {
                    "id": created_id,
                    "username": username,
                    "role": "user",
                    "user_type": user_type_code,
                    "status": "active",
                    **department_data,
                },
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "创建用户失败", "code": "CREATE_ERROR"}

    def update_department(
        self,
        *,
        target_user_id: int,
        primary_department_id: int | None,
        secondary_department_id: int | None,
    ) -> dict[str, Any]:
        try:
            user = self._users.get_by_id(target_user_id)
            if not user:
                return {"success": False, "error": "用户不存在", "code": "USER_NOT_FOUND"}
            current_department = self._departments.describe_user_department(
                primary_department_id=user.get("primary_department_id"),
                secondary_department_id=user.get("secondary_department_id"),
            )
            if (
                self._department_pair_from_mapping(user)
                == self._department_pair_from_mapping(
                    {
                        "primary_department_id": primary_department_id,
                        "secondary_department_id": secondary_department_id,
                    }
                )
                and not bool(current_department.get("require_department_setup"))
            ):
                return {
                    "success": True,
                    "message": "用户部门已更新",
                    "data": {
                        "id": int(user["id"]),
                        "username": user["username"],
                        **current_department,
                    },
                }
            department_validation = self._departments.validate_department_selection(
                primary_department_id=primary_department_id,
                secondary_department_id=secondary_department_id,
                require_active=True,
                allow_empty=True,
            )
            if not department_validation.get("success"):
                return department_validation
            department_data = department_validation.get("data") if isinstance(department_validation.get("data"), dict) else {}
            updated_count = self._users.update_user_department(
                user_id=target_user_id,
                primary_department_id=department_data.get("primary_department_id"),
                secondary_department_id=department_data.get("secondary_department_id"),
            )
            refreshed_user = self._users.get_by_id(target_user_id) or user
            if (
                updated_count <= 0
                and self._department_pair_from_mapping(refreshed_user)
                != self._department_pair_from_mapping(department_data)
            ):
                return {"success": False, "error": "修改用户部门失败", "code": "UPDATE_ERROR"}
            return {
                "success": True,
                "message": "用户部门已更新",
                "data": {
                    "id": int(user["id"]),
                    "username": user["username"],
                    **department_data,
                },
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "修改用户部门失败", "code": "UPDATE_ERROR"}

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
