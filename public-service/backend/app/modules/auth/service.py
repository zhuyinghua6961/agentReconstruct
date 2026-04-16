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


class DatabaseUnavailableError(Exception):
    """Raised when the migrated auth module has no repository wiring yet."""


class AuthRepositoryProtocol(Protocol):
    def get_by_username(self, username: str) -> dict[str, Any] | None: ...
    def get_by_id(self, user_id: int) -> dict[str, Any] | None: ...
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
    ) -> int: ...


class DepartmentServiceProtocol(Protocol):
    def describe_user_department(
        self,
        *,
        primary_department_id: int | None,
        secondary_department_id: int | None,
    ) -> dict[str, Any]: ...

    def get_selectable_tree(self) -> dict[str, Any]: ...

    def validate_department_selection(
        self,
        *,
        primary_department_id: int | None,
        secondary_department_id: int | None,
        require_active: bool,
        allow_empty: bool,
    ) -> dict[str, Any]: ...


class UnavailableAuthRepository:
    def _raise(self):
        raise DatabaseUnavailableError("auth_repository_unavailable")

    def get_by_username(self, username: str) -> dict[str, Any] | None:
        self._raise()

    def get_by_id(self, user_id: int) -> dict[str, Any] | None:
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
    ) -> None:
        self._repo = repo or UnavailableAuthRepository()
        self._tokens = token_service or TokenService()
        self._departments = department_service or shared_department_service
        self._password_expire_days = max(1, int(os.getenv("PASSWORD_EXPIRE_DAYS", "180") or "180"))
        self._lock_threshold = max(2, int(os.getenv("LOGIN_FAILURE_LOCK_THRESHOLD", "5") or "5"))
        self._lock_minutes = max(1, int(os.getenv("LOGIN_FAILURE_LOCK_MINUTES", "5") or "5"))

    def status_code_for(self, result: dict[str, Any], *, ok_status: int) -> int:
        if result.get("success"):
            return ok_status
        code = str(result.get("code") or "")
        if code in {"VALIDATION_ERROR"}:
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
        if code in {"USER_NOT_FOUND", "PRIMARY_DEPARTMENT_NOT_FOUND", "SECONDARY_DEPARTMENT_NOT_FOUND"}:
            return 404
        if code in {"ACCOUNT_DISABLED"}:
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
            )
        )
        if self._is_admin_user(user):
            department_payload["require_department_setup"] = False
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
    ) -> dict[str, Any]:
        try:
            user = self._repo.get_by_id(user_id)
            if not user:
                return {"success": False, "error": "user_not_found", "code": "USER_NOT_FOUND"}

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
                return {"success": True, "message": "department_updated", "data": self._build_user_payload(user)}

            validation = self._departments.validate_department_selection(
                primary_department_id=primary_department_id,
                secondary_department_id=secondary_department_id,
                require_active=True,
                allow_empty=False,
            )
            if not validation.get("success"):
                return validation

            department_data = validation.get("data") if isinstance(validation.get("data"), dict) else {}
            updated_count = self._repo.update_user_department(
                user_id=user_id,
                primary_department_id=department_data.get("primary_department_id"),
                secondary_department_id=department_data.get("secondary_department_id"),
            )
            refreshed_user = self._repo.get_by_id(user_id) or user
            if (
                updated_count <= 0
                and self._department_pair_from_mapping(refreshed_user)
                != self._department_pair_from_mapping(department_data)
            ):
                return {"success": False, "error": "department_update_failed", "code": "UPDATE_ERROR"}
            updated_user = {
                **refreshed_user,
                "primary_department_id": department_data.get("primary_department_id"),
                "secondary_department_id": department_data.get("secondary_department_id"),
            }
            return {"success": True, "message": "department_updated", "data": self._build_user_payload(updated_user)}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": str(exc), "code": "UPDATE_ERROR"}

    def register(self, username: str, password: str) -> dict[str, Any]:
        username = (username or "").strip()
        password = password or ""
        if len(username) < 3 or len(username) > 64:
            return {"success": False, "error": "invalid_username", "code": "VALIDATION_ERROR"}
        validation = self._validate_password_strength(password=password, role="user")
        if not validation.get("success"):
            return validation
        try:
            if self._repo.get_by_username(username):
                return {"success": False, "error": "username_exists", "code": "USERNAME_EXISTS"}
            password_hash = _hash_password(password)
            user_id = self._repo.create_user(
                username=username,
                password_hash=password_hash,
                role="user",
                user_type=3,
                is_first_login=True,
                must_set_security_questions=True,
            )
            self._repo.add_password_history(user_id=user_id, password_hash=password_hash)
            self._repo.trim_password_history(user_id=user_id, keep_limit=self._password_history_limit("user"))
            token = self._tokens.issue_access_token(user_id=user_id, role="user")
            return {
                "success": True,
                "message": "register_success",
                "data": {
                    "token": token,
                    "user": {"id": user_id, "username": username, "role": "user", "user_type": 3},
                    "is_first_login": True,
                    "has_security_questions": False,
                    "require_security_questions_setup": True,
                },
                "require_password_change": True,
                "require_security_questions_setup": True,
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
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
                    },
                    "is_first_login": bool(user_payload.get("is_first_login")),
                    "has_security_questions": bool(user_payload.get("has_security_questions")),
                    "require_security_questions_setup": bool(user_payload.get("require_security_questions_setup")),
                    "require_department_setup": bool(user_payload.get("require_department_setup")),
                },
                "require_department_setup": bool(user_payload.get("require_department_setup")),
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
            items.append({"question": question, "answer_hash": _hash_password(normalized_answer), "sort_order": index + 1})
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
