from __future__ import annotations

from hashlib import pbkdf2_hmac
from hmac import compare_digest
from typing import Any


def _is_admin_user(user: dict[str, Any] | None) -> bool:
    if not user:
        return False
    try:
        if int(user.get("user_type") or 0) == 1:
            return True
    except (TypeError, ValueError):
        pass
    return str(user.get("role") or "").strip().lower() == "admin"


def _verify_password(password: str, password_hash: str) -> bool:
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


def verify_admin_password(*, users_repo: Any, actor_user_id: int, admin_password: str) -> bool:
    getter = getattr(users_repo, "get_by_id", None)
    if not callable(getter):
        return False
    user = getter(int(actor_user_id or 0))
    if not _is_admin_user(user):
        return False
    if str((user or {}).get("status") or "active").strip().lower() != "active":
        return False
    return _verify_password(admin_password, str((user or {}).get("password_hash") or ""))
