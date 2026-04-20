from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime
from typing import Any, Protocol

from itsdangerous import URLSafeTimedSerializer
from itsdangerous.exc import BadSignature, BadTimeSignature, SignatureExpired

from app.modules.departments.service import department_service as shared_department_service
from app.modules.personnel.service import personnel_service as shared_personnel_service


class DatabaseUnavailableError(Exception):
    """Raised when the migrated auth module has no repository wiring yet."""


class AuthRepositoryProtocol(Protocol):
    def get_by_username(self, username: str) -> dict[str, Any] | None: ...
    def get_by_id(self, user_id: int) -> dict[str, Any] | None: ...
    def create_registered_user(
        self,
        *,
        username: str,
        password_hash: str,
        primary_department_id: int,
        secondary_department_id: int,
        tertiary_department_id: int,
        personnel_id: int,
        security_question_items: list[dict[str, Any]],
        user_type: int = 2,
    ) -> int: ...
    def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        role: str = "user",
        user_type: int | None = None,
        is_first_login: bool | None = None,
        must_set_security_questions: bool | None = None,
    ) -> int: ...
    def update_password_hash(self, *, user_id: int, password_hash: str) -> int: ...
    def reset_login_attempts(self, *, user_id: int) -> int: ...
    def increment_login_attempts(self, *, user_id: int, lock_threshold: int, lock_minutes: int) -> dict[str, Any]: ...
    def mark_first_login_completed(self, *, user_id: int) -> int: ...
    def set_security_setup_required(self, *, user_id: int, required: bool) -> int: ...
    def list_recent_password_hashes(self, *, user_id: int, limit: int) -> list[str]: ...
    def add_password_history(self, *, user_id: int, password_hash: str) -> int: ...
    def trim_password_history(self, *, user_id: int, keep_limit: int) -> int: ...
    def list_security_questions(self, *, user_id: int) -> list[dict[str, Any]]: ...
    def replace_security_questions(self, *, user_id: int, items: list[dict[str, Any]]) -> int: ...
    def has_security_questions(self, *, user_id: int) -> bool: ...
    def update_user_department(
        self,
        *,
        user_id: int,
        primary_department_id: int | None,
        secondary_department_id: int | None,
        tertiary_department_id: int | None = None,
    ) -> int: ...
    def update_user_personnel(self, *, user_id: int, personnel_id: int | None) -> int: ...
    def update_username(self, *, user_id: int, username: str) -> int: ...


class DepartmentServiceProtocol(Protocol):
    def describe_user_department(
        self,
        *,
        primary_department_id: int | None,
        secondary_department_id: int | None,
        tertiary_department_id: int | None = None,
    ) -> dict[str, Any]: ...


class PersonnelServiceProtocol(Protocol):
    def describe_user_personnel(self, *, personnel_id: int | None) -> dict[str, Any]: ...

    def verify_personnel_identity(
        self,
        *,
        employee_no: str,
        full_name: str,
        verification_code: str,
    ) -> dict[str, Any]: ...

    def get_selectable_tree(self) -> dict[str, Any]: ...

    def validate_department_selection(
        self,
        *,
        primary_department_id: int | None,
        secondary_department_id: int | None,
        tertiary_department_id: int | None = None,
        require_active: bool,
        allow_empty: bool,
        allow_legacy_two_level: bool = True,
    ) -> dict[str, Any]: ...


class UnavailableAuthRepository:
    def _raise(self):
        raise DatabaseUnavailableError("auth_repository_unavailable")

    def get_by_username(self, username: str) -> dict[str, Any] | None:
        self._raise()

    def get_by_id(self, user_id: int) -> dict[str, Any] | None:
        self._raise()

    def create_registered_user(self, **kwargs) -> int:
        self._raise()

    def create_user(self, **kwargs) -> int:
        self._raise()

    def update_password_hash(self, *, user_id: int, password_hash: str) -> int:
        self._raise()

    def reset_login_attempts(self, *, user_id: int) -> int:
        self._raise()

    def increment_login_attempts(self, *, user_id: int, lock_threshold: int, lock_minutes: int) -> dict[str, Any]:
        self._raise()

    def mark_first_login_completed(self, *, user_id: int) -> int:
        self._raise()

    def set_security_setup_required(self, *, user_id: int, required: bool) -> int:
        self._raise()

    def list_recent_password_hashes(self, *, user_id: int, limit: int) -> list[str]:
        self._raise()

    def add_password_history(self, *, user_id: int, password_hash: str) -> int:
        self._raise()

    def trim_password_history(self, *, user_id: int, keep_limit: int) -> int:
        self._raise()

    def list_security_questions(self, *, user_id: int) -> list[dict[str, Any]]:
        self._raise()

    def replace_security_questions(self, *, user_id: int, items: list[dict[str, Any]]) -> int:
        self._raise()

    def has_security_questions(self, *, user_id: int) -> bool:
        self._raise()

    def update_user_department(
        self,
        *,
        user_id: int,
        primary_department_id: int | None,
        secondary_department_id: int | None,
        tertiary_department_id: int | None = None,
    ) -> int:
        self._raise()

    def update_user_personnel(self, *, user_id: int, personnel_id: int | None) -> int:
        self._raise()

    def update_username(self, *, user_id: int, username: str) -> int:
        self._raise()


def _is_db_unavailable_error(exc: Exception) -> bool:
    return exc.__class__.__name__ in {"DatabaseConfigError", "DatabaseConnectionError", "DatabaseUnavailableError"}


def _hash_password(password: str, *, iterations: int = 120_000) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        algo, iter_text, salt, digest_hex = password_hash.split("$", 3)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    try:
        iterations = int(iter_text)
    except ValueError:
        return False
    expected = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations).hex()
    return hmac.compare_digest(expected, digest_hex)


class TokenService:
    def __init__(self) -> None:
        secret = str(os.getenv("JWT_SECRET", "") or "").strip()
        if not secret:
            raise RuntimeError("JWT_SECRET is required")
        self._salt = "agentcode.auth.access"
        self._expire_seconds = int(os.getenv("JWT_EXPIRE_SECONDS", "86400") or "86400")
        self._serializer = URLSafeTimedSerializer(secret)

    def issue_access_token(self, *, user_id: int, role: str) -> str:
        payload = {"user_id": int(user_id), "role": role, "iat": int(datetime.now().timestamp())}
        return self._serializer.dumps(payload, salt=self._salt)

    def decode_access_token(self, token: str) -> dict[str, Any] | None:
        if not token or not token.strip():
            return None
        try:
            data = self._serializer.loads(token.strip(), salt=self._salt, max_age=self._expire_seconds)
            return data if isinstance(data, dict) else None
        except (BadSignature, BadTimeSignature, SignatureExpired):
            return None


class AuthService:
    def __init__(
        self,
        *,
        repo: AuthRepositoryProtocol | None = None,
        token_service: TokenService | None = None,
        department_service: DepartmentServiceProtocol | None = None,
        personnel_service: PersonnelServiceProtocol | None = None,
    ) -> None:
        self._repo = repo or UnavailableAuthRepository()
        self._tokens = token_service or TokenService()
        self._departments = department_service or shared_department_service
        self._personnel = personnel_service or shared_personnel_service
        self._password_expire_days = max(1, int(os.getenv("PASSWORD_EXPIRE_DAYS", "180") or "180"))
        self._lock_threshold = max(2, int(os.getenv("LOGIN_FAILURE_LOCK_THRESHOLD", "5") or "5"))
        self._lock_minutes = max(1, int(os.getenv("LOGIN_FAILURE_LOCK_MINUTES", "5") or "5"))

    def status_code_for(self, result: dict[str, Any], *, ok_status: int) -> int:
        if result.get("success"):
            return ok_status
        code = str(result.get("code") or "")
        if code in {"VALIDATION_ERROR", "USERNAME_INVALID", "PERSONNEL_BINDING_INVALID", "PERSONNEL_DISABLED"}:
            return 400
        if code in {
            "PASSWORD_TOO_SHORT",
            "PASSWORD_NO_LOWERCASE",
            "PASSWORD_NO_UPPERCASE",
            "PASSWORD_NO_DIGIT",
            "PASSWORD_NO_SYMBOL",
            "PASSWORD_WEAK",
            "PASSWORD_REUSED",
            "INVALID_PASSWORD",
            "WRONG_ANSWERS",
            "NO_SECURITY_QUESTIONS",
            "DEPARTMENT_REQUIRED",
            "DEPARTMENT_RELATION_INVALID",
            "DEPARTMENT_DISABLED",
        }:
            return 400
        if code in {"INVALID_CREDENTIALS", "TOKEN_MISSING", "TOKEN_INVALID"}:
            return 401
        if code in {"USER_NOT_FOUND", "PRIMARY_DEPARTMENT_NOT_FOUND", "SECONDARY_DEPARTMENT_NOT_FOUND", "TERTIARY_DEPARTMENT_NOT_FOUND"}:
            return 404
        if code in {"ACCOUNT_DISABLED"}:
            return 403
        if code in {"PERMISSION_DENIED"}:
            return 403
        if code in {"ACCOUNT_LOCKED", "ACCOUNT_LOCKED_DUE_TO_FAILURES"}:
            return 423
        if code in {"USERNAME_EXISTS"}:
            return 409
        if code in {"DB_UNAVAILABLE"}:
            return 503
        return 500

    def decode_token(self, token: str) -> dict[str, Any] | None:
        return self._tokens.decode_access_token(token)

    @staticmethod
    def _resolve_user_type(user: dict[str, Any] | None) -> int:
        if not user:
            return 3
        try:
            value = int(user.get("user_type"))
            if value in {1, 2, 3}:
                return value
        except (TypeError, ValueError):
            pass
        role = str(user.get("role") or "").strip().lower()
        if role == "admin":
            return 1
        if role == "super":
            return 2
        return 3

    @classmethod
    def _is_admin_user(cls, user: dict[str, Any] | None) -> bool:
        return cls._resolve_user_type(user) == 1

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
    def _to_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    @staticmethod
    def _normalize_answer(answer: str) -> str:
        return " ".join(str(answer or "").strip().lower().split())

    @staticmethod
    def _password_history_limit(role: str) -> int:
        return 5 if str(role or "").strip().lower() == "admin" else 3

    @staticmethod
    def _has_lowercase(password: str) -> bool:
        return any("a" <= ch <= "z" for ch in password)

    @staticmethod
    def _has_uppercase(password: str) -> bool:
        return any("A" <= ch <= "Z" for ch in password)

    @staticmethod
    def _has_digit(password: str) -> bool:
        return any(ch.isdigit() for ch in password)

    @staticmethod
    def _has_symbol(password: str) -> bool:
        return any(not ch.isalnum() for ch in password)

    @staticmethod
    def _duplicate_error(exc: Exception) -> bool:
        message = str(exc or "").lower()
        return "duplicate" in message or "unique" in message or "1062" in message

    def validate_username_candidate(
        self,
        *,
        username: str,
        owner_user_id: int | None = None,
    ) -> dict[str, Any]:
        normalized = str(username or "").strip()
        if len(normalized) < 3 or len(normalized) > 50:
            return {"success": False, "error": "用户名长度必须在3-50之间", "code": "VALIDATION_ERROR"}
        if normalized.lower().startswith("admin"):
            return {"success": False, "error": "不能以 admin 开头", "code": "USERNAME_INVALID"}
        existing = self._repo.get_by_username(normalized)
        if existing:
            try:
                existing_id = int(existing.get("id") or 0)
            except Exception:
                existing_id = 0
            if owner_user_id is None or existing_id != int(owner_user_id):
                return {"success": False, "error": "用户名已存在", "code": "USERNAME_EXISTS"}
        return {"success": True, "data": {"username": normalized}}

    def _validate_password_strength(self, *, password: str, role: str) -> dict[str, Any]:
        password = str(password or "")
        role = str(role or "").strip().lower()
        if role == "admin":
            if len(password) < 12:
                return {"success": False, "error": "管理员密码长度不能少于12位", "code": "PASSWORD_TOO_SHORT"}
            if not self._has_lowercase(password):
                return {"success": False, "error": "管理员密码必须包含小写字母", "code": "PASSWORD_NO_LOWERCASE"}
            if not self._has_uppercase(password):
                return {"success": False, "error": "管理员密码必须包含大写字母", "code": "PASSWORD_NO_UPPERCASE"}
            if not self._has_digit(password):
                return {"success": False, "error": "管理员密码必须包含数字", "code": "PASSWORD_NO_DIGIT"}
            if not self._has_symbol(password):
                return {"success": False, "error": "管理员密码必须包含英文符号", "code": "PASSWORD_NO_SYMBOL"}
            return {"success": True}
        if len(password) < 8:
            return {"success": False, "error": "密码长度不能少于8位", "code": "PASSWORD_TOO_SHORT"}
        category_count = 0
        category_count += 1 if self._has_lowercase(password) else 0
        category_count += 1 if self._has_uppercase(password) else 0
        category_count += 1 if self._has_digit(password) else 0
        category_count += 1 if self._has_symbol(password) else 0
        if category_count < 3:
            return {
                "success": False,
                "error": "密码必须包含数字、小写字母、大写字母、特殊符号中的至少3类",
                "code": "PASSWORD_WEAK",
            }
        return {"success": True}

    def validate_password_strength(self, *, password: str, role: str) -> dict[str, Any]:
        return self._validate_password_strength(password=password, role=role)

    def _check_password_history(self, *, user_id: int, role: str, new_password: str) -> dict[str, Any]:
        history_limit = self._password_history_limit(role)
        recent_hashes = self._repo.list_recent_password_hashes(user_id=user_id, limit=history_limit)
        for old_hash in recent_hashes:
            if _verify_password(new_password, old_hash):
                return {
                    "success": False,
                    "error": f"新密码不能与最近{history_limit}次使用的密码相同",
                    "code": "PASSWORD_REUSED",
                }
        return {"success": True}

    def _normalize_security_question_items(self, questions: list[dict[str, Any]]) -> dict[str, Any]:
        if not isinstance(questions, list) or not questions:
            return {"success": False, "error": "请提供安全问题设置", "code": "VALIDATION_ERROR"}
        if len(questions) < 1 or len(questions) > 3:
            return {"success": False, "error": "安全问题数量必须在1-3个之间", "code": "VALIDATION_ERROR"}

        items: list[dict[str, Any]] = []
        for index, item in enumerate(questions):
            question = str((item or {}).get("question") or "").strip()
            answer = str((item or {}).get("answer") or "")
            normalized_answer = self._normalize_answer(answer)
            if not question or not normalized_answer:
                return {"success": False, "error": f"第{index + 1}个问题缺少问题或答案", "code": "VALIDATION_ERROR"}
            items.append(
                {
                    "question": question,
                    "answer_hash": _hash_password(normalized_answer),
                    "sort_order": index + 1,
                }
            )
        return {"success": True, "data": items}

    def _password_expired(self, user: dict[str, Any]) -> tuple[bool, int | None]:
        updated_at = self._to_datetime(user.get("password_updated_at"))
        if not updated_at:
            return False, None
        days_since_update = (datetime.now() - updated_at).days
        return days_since_update >= self._password_expire_days, days_since_update

    def _build_user_payload(self, user: dict[str, Any]) -> dict[str, Any]:
        user_id = int(user["id"])
        user_type = self._resolve_user_type(user)
        has_security_questions = self._repo.has_security_questions(user_id=user_id)
        require_security_questions_setup = bool(user.get("must_set_security_questions", False)) and not has_security_questions
        created_at = self._to_datetime(user.get("created_at"))
        department_payload = dict(
            self._departments.describe_user_department(
                primary_department_id=user.get("primary_department_id"),
                secondary_department_id=user.get("secondary_department_id"),
                tertiary_department_id=user.get("tertiary_department_id"),
            )
        )
        if self._is_admin_user(user):
            department_payload["require_department_setup"] = False
        personnel_payload = dict(
            self._personnel.describe_user_personnel(
                personnel_id=user.get("personnel_id"),
            )
        )
        if self._is_admin_user(user):
            personnel_payload["require_personnel_setup"] = False
        return {
            "id": user_id,
            "username": user["username"],
            "role": user["role"],
            "user_type": user_type,
            "status": user["status"],
            "is_first_login": bool(user.get("is_first_login", False)),
            "has_security_questions": has_security_questions,
            "require_security_questions_setup": require_security_questions_setup,
            **department_payload,
            **personnel_payload,
            "created_at": created_at.isoformat() if created_at else None,
        }

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        return self._repo.get_by_id(user_id)

    def get_user_info(self, user_id: int) -> dict[str, Any]:
        try:
            user = self._repo.get_by_id(user_id)
            if not user:
                return {"success": False, "error": "user_not_found", "code": "USER_NOT_FOUND"}
            return {"success": True, "data": self._build_user_payload(user)}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "FETCH_ERROR"}

    def get_selectable_department_tree(self, *, user_id: int | None = None) -> dict[str, Any]:
        del user_id
        try:
            return self._departments.get_selectable_tree()
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "FETCH_ERROR"}

    def update_department(
        self,
        *,
        user_id: int,
        primary_department_id: int | None,
        secondary_department_id: int | None,
        tertiary_department_id: int | None = None,
    ) -> dict[str, Any]:
        try:
            user = self._repo.get_by_id(user_id)
            if not user:
                return {"success": False, "error": "user_not_found", "code": "USER_NOT_FOUND"}

            current_department = self._departments.describe_user_department(
                primary_department_id=user.get("primary_department_id"),
                secondary_department_id=user.get("secondary_department_id"),
                tertiary_department_id=user.get("tertiary_department_id"),
            )
            if (
                self._department_triplet_from_mapping(user)
                == self._department_triplet_from_mapping(
                    {
                        "primary_department_id": primary_department_id,
                        "secondary_department_id": secondary_department_id,
                        "tertiary_department_id": tertiary_department_id,
                    }
                )
                and not bool(current_department.get("require_department_setup"))
            ):
                return {"success": True, "message": "department_updated", "data": self._build_user_payload(user)}

            validation = self._departments.validate_department_selection(
                primary_department_id=primary_department_id,
                secondary_department_id=secondary_department_id,
                tertiary_department_id=tertiary_department_id,
                require_active=True,
                allow_empty=True,
                allow_legacy_two_level=False,
            )
            if not validation.get("success"):
                return validation

            department_data = validation.get("data") if isinstance(validation.get("data"), dict) else {}
            updated_count = self._repo.update_user_department(
                user_id=user_id,
                primary_department_id=department_data.get("primary_department_id"),
                secondary_department_id=department_data.get("secondary_department_id"),
                tertiary_department_id=department_data.get("tertiary_department_id"),
            )
            refreshed_user = self._repo.get_by_id(user_id) or user
            if (
                updated_count <= 0
                and self._department_triplet_from_mapping(refreshed_user)
                != self._department_triplet_from_mapping(department_data)
            ):
                return {"success": False, "error": "department_update_failed", "code": "UPDATE_ERROR"}
            updated_user = {
                **refreshed_user,
                "primary_department_id": department_data.get("primary_department_id"),
                "secondary_department_id": department_data.get("secondary_department_id"),
                "tertiary_department_id": department_data.get("tertiary_department_id"),
            }
            return {"success": True, "message": "department_updated", "data": self._build_user_payload(updated_user)}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "UPDATE_ERROR"}

    def update_username(self, *, user_id: int, username: str) -> dict[str, Any]:
        try:
            user = self._repo.get_by_id(user_id)
            if not user:
                return {"success": False, "error": "user_not_found", "code": "USER_NOT_FOUND"}
            if self._is_admin_user(user):
                return {"success": False, "error": "管理员不能在个人中心修改用户名", "code": "PERMISSION_DENIED"}
            validation = self.validate_username_candidate(username=username, owner_user_id=user_id)
            if not validation.get("success"):
                return validation
            normalized_username = str(validation.get("data", {}).get("username") or "").strip()
            updated_count = self._repo.update_username(user_id=user_id, username=normalized_username)
            refreshed_user = self._repo.get_by_id(user_id) or user
            current_username = str(refreshed_user.get("username") or "").strip()
            if updated_count <= 0 and current_username != normalized_username:
                return {"success": False, "error": "username_update_failed", "code": "UPDATE_ERROR"}
            updated_user = {**refreshed_user, "username": normalized_username}
            return {"success": True, "message": "username_updated", "data": self._build_user_payload(updated_user)}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            if self._duplicate_error(exc):
                return {"success": False, "error": "用户名已存在", "code": "USERNAME_EXISTS"}
            return {"success": False, "error": str(exc), "code": "UPDATE_ERROR"}

    def update_personnel_binding(
        self,
        *,
        user_id: int,
        employee_no: str,
        full_name: str,
        verification_code: str,
    ) -> dict[str, Any]:
        try:
            user = self._repo.get_by_id(user_id)
            if not user:
                return {"success": False, "error": "user_not_found", "code": "USER_NOT_FOUND"}
            employee_no = str(employee_no or "").strip()
            full_name = str(full_name or "").strip()
            verification_code = str(verification_code or "").strip()
            if not employee_no or not full_name or not verification_code:
                return {"success": False, "error": "请完整填写工号、姓名和校验码", "code": "VALIDATION_ERROR"}

            verification = self._personnel.verify_personnel_identity(
                employee_no=employee_no,
                full_name=full_name,
                verification_code=verification_code,
            )
            if not verification.get("success"):
                code = str(verification.get("code") or "")
                if code == "PERSONNEL_DISABLED":
                    return {"success": False, "error": "该人员已停用", "code": "PERSONNEL_DISABLED"}
                return {"success": False, "error": "人员信息校验失败", "code": "PERSONNEL_BINDING_INVALID"}

            target_record = verification.get("data") if isinstance(verification.get("data"), dict) else {}
            target_personnel_id = int(target_record.get("id") or 0)
            if target_personnel_id <= 0:
                return {"success": False, "error": "人员信息校验失败", "code": "PERSONNEL_BINDING_INVALID"}

            updated_count = self._repo.update_user_personnel(user_id=user_id, personnel_id=target_personnel_id)
            refreshed_user = self._repo.get_by_id(user_id) or user
            current_personnel_id = refreshed_user.get("personnel_id")
            try:
                current_personnel_id = int(current_personnel_id) if current_personnel_id is not None else None
            except (TypeError, ValueError):
                current_personnel_id = None
            if updated_count <= 0 and current_personnel_id != target_personnel_id:
                return {"success": False, "error": "personnel_binding_update_failed", "code": "UPDATE_ERROR"}

            updated_user = {**refreshed_user, "personnel_id": target_personnel_id}
            return {"success": True, "message": "personnel_binding_updated", "data": self._build_user_payload(updated_user)}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "UPDATE_ERROR"}

    def register(
        self,
        *,
        username: str,
        password: str,
        primary_department_id: int | None,
        secondary_department_id: int | None,
        tertiary_department_id: int | None,
        employee_no: str,
        full_name: str,
        verification_code: str,
        security_questions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        username = (username or "").strip()
        password = password or ""
        validation = self._validate_password_strength(password=password, role="user")
        if not validation.get("success"):
            return validation
        try:
            username_validation = self.validate_username_candidate(username=username)
            if not username_validation.get("success"):
                return username_validation
            normalized_username = str(username_validation.get("data", {}).get("username") or "").strip()
            department_validation = self._departments.validate_department_selection(
                primary_department_id=primary_department_id,
                secondary_department_id=secondary_department_id,
                tertiary_department_id=tertiary_department_id,
                require_active=True,
                allow_empty=False,
                allow_legacy_two_level=False,
            )
            if not department_validation.get("success"):
                return department_validation

            personnel_validation = self._personnel.verify_personnel_identity(
                employee_no=str(employee_no or "").strip(),
                full_name=str(full_name or "").strip(),
                verification_code=str(verification_code or "").strip(),
            )
            if not personnel_validation.get("success"):
                return personnel_validation

            security_question_validation = self._normalize_security_question_items(security_questions)
            if not security_question_validation.get("success"):
                return security_question_validation

            department_data = department_validation.get("data") if isinstance(department_validation.get("data"), dict) else {}
            personnel_record = personnel_validation.get("data") if isinstance(personnel_validation.get("data"), dict) else {}
            personnel_id = int(personnel_record.get("id") or 0)
            if personnel_id <= 0:
                return {"success": False, "error": "人员信息校验失败", "code": "PERSONNEL_BINDING_INVALID"}

            password_hash = _hash_password(password)
            user_id = self._repo.create_registered_user(
                username=normalized_username,
                password_hash=password_hash,
                primary_department_id=int(department_data.get("primary_department_id") or 0),
                secondary_department_id=int(department_data.get("secondary_department_id") or 0),
                tertiary_department_id=int(department_data.get("tertiary_department_id") or 0),
                personnel_id=personnel_id,
                security_question_items=list(security_question_validation.get("data") or []),
                user_type=2,
            )
            created_user = self._repo.get_by_id(user_id)
            if not created_user:
                return {"success": False, "error": "注册结果校验失败", "code": "REGISTER_ERROR"}
            user_payload = self._build_user_payload(created_user)
            token = self._tokens.issue_access_token(user_id=user_id, role="user")
            return {
                "success": True,
                "message": "register_success",
                "data": {
                    "token": token,
                    "user": {
                        "id": user_payload["id"],
                        "username": user_payload["username"],
                        "role": user_payload["role"],
                        "user_type": user_payload["user_type"],
                        "primary_department_id": user_payload.get("primary_department_id"),
                        "primary_department_name": user_payload.get("primary_department_name"),
                        "secondary_department_id": user_payload.get("secondary_department_id"),
                        "secondary_department_name": user_payload.get("secondary_department_name"),
                        "tertiary_department_id": user_payload.get("tertiary_department_id"),
                        "tertiary_department_name": user_payload.get("tertiary_department_name"),
                        "department_completion_level": user_payload.get("department_completion_level"),
                        "require_department_setup": bool(user_payload.get("require_department_setup")),
                        "personnel_id": user_payload.get("personnel_id"),
                        "employee_no": user_payload.get("employee_no"),
                        "full_name": user_payload.get("full_name"),
                        "personnel_binding_status": user_payload.get("personnel_binding_status"),
                        "require_personnel_setup": bool(user_payload.get("require_personnel_setup")),
                        "has_security_questions": bool(user_payload.get("has_security_questions")),
                        "require_security_questions_setup": bool(user_payload.get("require_security_questions_setup")),
                        "is_first_login": bool(user_payload.get("is_first_login")),
                    },
                    "is_first_login": bool(user_payload.get("is_first_login")),
                    "has_security_questions": bool(user_payload.get("has_security_questions")),
                    "require_security_questions_setup": bool(user_payload.get("require_security_questions_setup")),
                    "require_department_setup": bool(user_payload.get("require_department_setup")),
                    "require_personnel_setup": bool(user_payload.get("require_personnel_setup")),
                },
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            if self._duplicate_error(exc):
                return {"success": False, "error": "用户名已存在", "code": "USERNAME_EXISTS"}
            return {"success": False, "error": str(exc), "code": "REGISTER_ERROR"}

    def login(self, username: str, password: str) -> dict[str, Any]:
        username = (username or "").strip()
        password = password or ""
        if not username or not password:
            return {"success": False, "error": "missing_credentials", "code": "VALIDATION_ERROR"}
        try:
            user = self._repo.get_by_username(username)
            if not user:
                return {"success": False, "error": "invalid_credentials", "code": "INVALID_CREDENTIALS"}
            if user.get("status") != "active":
                return {"success": False, "error": "account_disabled", "code": "ACCOUNT_DISABLED"}

            locked_until = self._to_datetime(user.get("locked_until"))
            now = datetime.now()
            if locked_until:
                if now < locked_until:
                    remaining_seconds = max(1, int((locked_until - now).total_seconds()))
                    return {
                        "success": False,
                        "error": f"账号已被锁定，请在 {remaining_seconds // 60} 分钟后重试",
                        "code": "ACCOUNT_LOCKED",
                        "locked_until": locked_until.isoformat(),
                        "remaining_seconds": remaining_seconds,
                    }
                self._repo.reset_login_attempts(user_id=int(user["id"]))

            if not _verify_password(password, user.get("password_hash", "")):
                fail_stat = self._repo.increment_login_attempts(
                    user_id=int(user["id"]),
                    lock_threshold=self._lock_threshold,
                    lock_minutes=self._lock_minutes,
                )
                failed_attempts = int(fail_stat.get("failed_login_attempts") or 1)
                if failed_attempts >= self._lock_threshold:
                    return {
                        "success": False,
                        "error": f"密码错误次数过多，账号已被锁定{self._lock_minutes}分钟",
                        "code": "ACCOUNT_LOCKED_DUE_TO_FAILURES",
                        "failed_attempts": failed_attempts,
                    }
                return {
                    "success": False,
                    "error": f"用户名或密码错误（剩余尝试次数：{self._lock_threshold - failed_attempts}）",
                    "code": "INVALID_CREDENTIALS",
                    "failed_attempts": failed_attempts,
                    "remaining_attempts": max(0, self._lock_threshold - failed_attempts),
                }

            self._repo.reset_login_attempts(user_id=int(user["id"]))
            token = self._tokens.issue_access_token(user_id=int(user["id"]), role=str(user["role"]))
            user_payload = self._build_user_payload(user)
            result: dict[str, Any] = {
                "success": True,
                "message": "login_success",
                "data": {
                    "token": token,
                    "user": {
                        "id": user_payload["id"],
                        "username": user_payload["username"],
                        "role": user_payload["role"],
                        "user_type": user_payload["user_type"],
                        "primary_department_id": user_payload.get("primary_department_id"),
                        "primary_department_name": user_payload.get("primary_department_name"),
                        "secondary_department_id": user_payload.get("secondary_department_id"),
                        "secondary_department_name": user_payload.get("secondary_department_name"),
                        "tertiary_department_id": user_payload.get("tertiary_department_id"),
                        "tertiary_department_name": user_payload.get("tertiary_department_name"),
                        "department_completion_level": user_payload.get("department_completion_level"),
                        "personnel_id": user_payload.get("personnel_id"),
                        "employee_no": user_payload.get("employee_no"),
                        "full_name": user_payload.get("full_name"),
                        "personnel_binding_status": user_payload.get("personnel_binding_status"),
                        "require_personnel_setup": bool(user_payload.get("require_personnel_setup")),
                    },
                    "is_first_login": bool(user_payload.get("is_first_login")),
                    "has_security_questions": bool(user_payload.get("has_security_questions")),
                    "require_security_questions_setup": bool(user_payload.get("require_security_questions_setup")),
                    "require_department_setup": bool(user_payload.get("require_department_setup")),
                    "require_personnel_setup": bool(user_payload.get("require_personnel_setup")),
                },
                "require_department_setup": bool(user_payload.get("require_department_setup")),
                "require_personnel_setup": bool(user_payload.get("require_personnel_setup")),
            }
            if bool(user_payload.get("is_first_login")):
                result["require_password_change"] = True
                result["message"] = "首次登录，请立即修改密码"
            if bool(user_payload.get("require_security_questions_setup")):
                result["require_security_questions_setup"] = True
            password_expired, days_since_update = self._password_expired(user)
            if password_expired:
                result["warning"] = {
                    "code": "PASSWORD_EXPIRED",
                    "message": f"您的密码已经 {days_since_update} 天未更新，为了账号安全，建议您尽快修改密码",
                    "days_since_update": days_since_update,
                }
            return result
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "LOGIN_ERROR"}

    def change_password(self, *, user_id: int, old_password: str, new_password: str) -> dict[str, Any]:
        try:
            user = self._repo.get_by_id(user_id)
            if not user:
                return {"success": False, "error": "user_not_found", "code": "USER_NOT_FOUND"}
            if not _verify_password(old_password or "", user.get("password_hash", "")):
                return {"success": False, "error": "旧密码错误", "code": "INVALID_PASSWORD"}
            role = str(user.get("role") or "user")
            validation = self._validate_password_strength(password=new_password, role=role)
            if not validation.get("success"):
                return validation
            if _verify_password(new_password, user.get("password_hash", "")):
                return {
                    "success": False,
                    "error": f"新密码不能与最近{self._password_history_limit(role)}次使用的密码相同",
                    "code": "PASSWORD_REUSED",
                }
            history_check = self._check_password_history(user_id=user_id, role=role, new_password=new_password)
            if not history_check.get("success"):
                return history_check
            new_hash = _hash_password(new_password)
            self._repo.update_password_hash(user_id=user_id, password_hash=new_hash)
            self._repo.add_password_history(user_id=user_id, password_hash=new_hash)
            self._repo.trim_password_history(user_id=user_id, keep_limit=self._password_history_limit(role))
            self._repo.mark_first_login_completed(user_id=user_id)
            self._repo.reset_login_attempts(user_id=user_id)
            return {"success": True, "message": "password_updated"}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "PASSWORD_UPDATE_ERROR"}

    def initiate_password_reset(self, username: str) -> dict[str, Any]:
        username = (username or "").strip()
        if not username:
            return {"success": False, "error": "用户名不能为空", "code": "VALIDATION_ERROR"}
        try:
            user = self._repo.get_by_username(username)
            if not user:
                return {"success": False, "error": "用户不存在", "code": "USER_NOT_FOUND"}
            questions = self._repo.list_security_questions(user_id=int(user["id"]))
            question_texts = [str(item.get("question") or "") for item in questions if str(item.get("question") or "")]
            return {"success": True, "data": {"has_security_questions": bool(question_texts), "questions": question_texts}}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "RESET_ERROR"}

    def verify_and_reset_password(self, *, username: str, answers: list[Any], new_password: str) -> dict[str, Any]:
        username = (username or "").strip()
        if not username:
            return {"success": False, "error": "用户名不能为空", "code": "VALIDATION_ERROR"}
        if not isinstance(answers, list) or not answers:
            return {"success": False, "error": "请提供安全问题答案", "code": "VALIDATION_ERROR"}
        try:
            user = self._repo.get_by_username(username)
            if not user:
                return {"success": False, "error": "用户不存在", "code": "USER_NOT_FOUND"}
            role = str(user.get("role") or "user")
            validation = self._validate_password_strength(password=str(new_password or ""), role=role)
            if not validation.get("success"):
                return validation
            question_rows = self._repo.list_security_questions(user_id=int(user["id"]))
            if not question_rows:
                return {"success": False, "error": "该用户未设置安全问题，请联系管理员重置密码", "code": "NO_SECURITY_QUESTIONS"}
            if len(answers) < len(question_rows):
                return {"success": False, "error": "请回答所有安全问题", "code": "WRONG_ANSWERS"}
            for index, row in enumerate(question_rows):
                expected_hash = str(row.get("answer_hash") or "")
                provided = self._normalize_answer(str(answers[index] if index < len(answers) else ""))
                if not expected_hash or not _verify_password(provided, expected_hash):
                    return {"success": False, "error": "安全问题答案不正确", "code": "WRONG_ANSWERS"}
            if _verify_password(new_password, str(user.get("password_hash") or "")):
                return {
                    "success": False,
                    "error": f"新密码不能与最近{self._password_history_limit(role)}次使用的密码相同",
                    "code": "PASSWORD_REUSED",
                }
            history_check = self._check_password_history(user_id=int(user["id"]), role=role, new_password=str(new_password or ""))
            if not history_check.get("success"):
                return history_check
            new_hash = _hash_password(str(new_password or ""))
            user_id_int = int(user["id"])
            self._repo.update_password_hash(user_id=user_id_int, password_hash=new_hash)
            self._repo.add_password_history(user_id=user_id_int, password_hash=new_hash)
            self._repo.trim_password_history(user_id=user_id_int, keep_limit=self._password_history_limit(role))
            self._repo.mark_first_login_completed(user_id=user_id_int)
            self._repo.reset_login_attempts(user_id=user_id_int)
            return {"success": True, "message": "密码重置成功，请使用新密码登录"}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "VERIFY_ERROR"}

    def get_security_questions(self, *, user_id: int) -> dict[str, Any]:
        try:
            user = self._repo.get_by_id(user_id)
            if not user:
                return {"success": False, "error": "用户不存在", "code": "USER_NOT_FOUND"}
            rows = self._repo.list_security_questions(user_id=user_id)
            question_texts = [str(item.get("question") or "") for item in rows if str(item.get("question") or "")]
            return {"success": True, "data": {"questions": question_texts, "has_questions": bool(question_texts)}}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "FETCH_ERROR"}

    def set_security_questions(self, *, user_id: int, questions: list[dict[str, Any]]) -> dict[str, Any]:
        normalization = self._normalize_security_question_items(questions)
        if not normalization.get("success"):
            return normalization
        items = list(normalization.get("data") or [])
        try:
            user = self._repo.get_by_id(user_id)
            if not user:
                return {"success": False, "error": "用户不存在", "code": "USER_NOT_FOUND"}
            self._repo.replace_security_questions(user_id=user_id, items=items)
            self._repo.set_security_setup_required(user_id=user_id, required=False)
            return {"success": True, "message": "安全问题设置成功"}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "SET_ERROR"}


def set_auth_service(service: AuthService) -> AuthService:
    global auth_service
    auth_service = service
    return auth_service


auth_service = AuthService()
