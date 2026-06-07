from __future__ import annotations

from typing import Any

from app.modules.departments.repository import DepartmentRepository


def _is_db_unavailable_error(exc: Exception) -> bool:
    return exc.__class__.__name__ in {"DatabaseConfigError", "DatabaseConnectionError", "DatabaseUnavailableError"}


class DepartmentService:
    def __init__(self, *, repository: DepartmentRepository | None = None) -> None:
        self._repository = repository or DepartmentRepository()

    @staticmethod
    def clean_text(value: object) -> str:
        return str(value or "").strip()

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
            "DEPARTMENT_NAME_REQUIRED",
            "PRIMARY_DEPARTMENT_REQUIRED",
            "DEPARTMENT_REQUIRED",
            "DEPARTMENT_RELATION_INVALID",
            "DEPARTMENT_DISABLED",
            "PRIMARY_DEPARTMENT_NAME_EXISTS",
            "SECONDARY_DEPARTMENT_NAME_EXISTS",
            "TERTIARY_DEPARTMENT_NAME_EXISTS",
            "FILE_MISSING",
            "FILENAME_EMPTY",
            "INVALID_FILE_TYPE",
            "INVALID_FORMAT",
        }:
            return 400
        if code in {"DEPARTMENT_IN_USE"}:
            return 409
        if code in {"PRIMARY_DEPARTMENT_NOT_FOUND", "SECONDARY_DEPARTMENT_NOT_FOUND", "TERTIARY_DEPARTMENT_NOT_FOUND"}:
            return 404
        if code in {"DB_UNAVAILABLE"}:
            return 503
        return 500

    @staticmethod
    def _optional_int(value: object) -> int | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    @staticmethod
    def _normalize_status(value: object) -> str:
        return "disabled" if str(value or "").strip().lower() == "disabled" else "active"

    @staticmethod
    def _valid_status(value: object) -> bool:
        return str(value or "").strip().lower() in {"active", "disabled"}

    @staticmethod
    def _duplicate_error(exc: Exception) -> bool:
        message = str(exc or "").lower()
        return "duplicate" in message or "uq_" in message or "unique" in message

    @staticmethod
    def _effective_status(
        *,
        primary_status: str | None,
        secondary_status: str | None,
        tertiary_status: str | None = None,
    ) -> str | None:
        statuses = [status for status in (primary_status, secondary_status, tertiary_status) if status is not None]
        if len(statuses) < 2:
            return None
        if all(status == "active" for status in statuses):
            return "active"
        return "disabled"

    @staticmethod
    def _department_display(
        *,
        primary_name: str | None,
        secondary_name: str | None,
        tertiary_name: str | None = None,
        effective_status: str | None,
    ) -> str:
        parts = [part for part in (primary_name, secondary_name, tertiary_name) if part]
        if parts:
            label = " / ".join(parts)
            if effective_status == "disabled":
                return f"{label}（已停用）"
            return label
        return "未填写"

    @staticmethod
    def _user_type_code(*, user_type: object, role: object) -> int:
        try:
            user_type_code = int(user_type) if user_type is not None else None
        except (TypeError, ValueError):
            user_type_code = None

        role_text = str(role or "").strip().lower()
        if user_type_code == 1 or role_text == "admin":
            return 1
        if user_type_code == 2 or role_text == "super":
            return 2
        return 3

    @classmethod
    def _user_type_label(cls, *, user_type: object, role: object) -> str:
        user_type_code = cls._user_type_code(user_type=user_type, role=role)
        if user_type_code == 1:
            return "管理员"
        if user_type_code == 2:
            return "超级用户"
        return "普通用户"

    def _build_primary_payload(self, primary: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(primary["id"]),
            "name": primary["name"],
            "status": self._normalize_status(primary.get("status")),
            "secondary_items": [],
        }

    def _build_secondary_payload(self, secondary: dict[str, Any], *, primary_status: str | None) -> dict[str, Any]:
        secondary_status = self._normalize_status(secondary.get("status"))
        return {
            "id": int(secondary["id"]),
            "primary_department_id": int(secondary["primary_department_id"]),
            "name": secondary["name"],
            "status": secondary_status,
            "effective_status": self._effective_status(
                primary_status=primary_status,
                secondary_status=secondary_status,
            ),
        }

    def _build_tertiary_payload(
        self,
        tertiary: dict[str, Any],
        *,
        primary_status: str | None,
        secondary_status: str | None,
    ) -> dict[str, Any]:
        tertiary_status = self._normalize_status(tertiary.get("status"))
        return {
            "id": int(tertiary["id"]),
            "secondary_department_id": int(tertiary["secondary_department_id"]),
            "name": tertiary["name"],
            "status": tertiary_status,
            "effective_status": self._effective_status(
                primary_status=primary_status,
                secondary_status=secondary_status,
                tertiary_status=tertiary_status,
            ),
        }

    @staticmethod
    def _delete_payload(department: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(department["id"]),
            "name": department.get("name"),
        }

    @staticmethod
    def _department_in_use(reason: str) -> dict[str, Any]:
        return {"success": False, "error": reason, "code": "DEPARTMENT_IN_USE"}

    def _get_tertiary_by_id(self, tertiary_id: int | None) -> dict[str, Any] | None:
        if tertiary_id is None:
            return None
        getter = getattr(self._repository, "get_tertiary_by_id", None)
        if not callable(getter):
            return None
        return getter(int(tertiary_id))

    def _get_tertiary_by_name(self, *, secondary_department_id: int, name: str) -> dict[str, Any] | None:
        getter = getattr(self._repository, "get_tertiary_by_name", None)
        if not callable(getter):
            return None
        return getter(secondary_department_id=int(secondary_department_id), name=name)

    def get_admin_tree(self) -> dict[str, Any]:
        try:
            rows = self._repository.list_department_tree(include_disabled=True)
            items = []
            for row in rows:
                primary_status = self._normalize_status(row.get("primary_status"))
                secondary_items = []
                for secondary in row.get("secondary_items") or []:
                    child_status = self._normalize_status(secondary.get("status"))
                    effective_status = self._effective_status(
                        primary_status=primary_status,
                        secondary_status=child_status,
                    )
                    tertiary_items = []
                    for tertiary in secondary.get("tertiary_items") or []:
                        tertiary_status = self._normalize_status(tertiary.get("status"))
                        tertiary_items.append(
                            {
                                "id": int(tertiary["id"]),
                                "secondary_department_id": int(tertiary.get("secondary_department_id") or secondary["id"]),
                                "name": tertiary["name"],
                                "status": tertiary_status,
                                "effective_status": self._effective_status(
                                    primary_status=primary_status,
                                    secondary_status=child_status,
                                    tertiary_status=tertiary_status,
                                ),
                                "user_count": int(tertiary.get("user_count") or 0),
                            }
                        )
                    secondary_items.append(
                        {
                            "id": int(secondary["id"]),
                            "name": secondary["name"],
                            "status": child_status,
                            "effective_status": effective_status,
                            "user_count": int(secondary.get("user_count") or 0),
                            "legacy_user_count": int(secondary.get("legacy_user_count") or 0),
                            "tertiary_count": len(tertiary_items),
                            "tertiary_items": tertiary_items,
                        }
                    )
                items.append(
                    {
                        "id": int(row["primary_id"]),
                        "name": row["primary_name"],
                        "status": primary_status,
                        "secondary_items": secondary_items,
                    }
                )
            return {"success": True, "data": {"items": items}}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "获取部门列表失败", "code": "FETCH_ERROR"}

    def list_secondary_users(self, *, secondary_id: int) -> dict[str, Any]:
        try:
            secondary = self._repository.get_secondary_by_id(int(secondary_id))
            if not secondary:
                return {"success": False, "error": "二级部门不存在", "code": "SECONDARY_DEPARTMENT_NOT_FOUND"}

            primary_id = int(secondary["primary_department_id"])
            primary = self._repository.get_primary_by_id(primary_id)
            rows = self._repository.list_users_by_secondary_department(secondary_id=int(secondary["id"]))
            users = [
                {
                    "id": int(row["id"]),
                    "username": row["username"],
                    "user_type": self._user_type_code(
                        user_type=row.get("user_type"),
                        role=row.get("role"),
                    ),
                    "user_type_label": self._user_type_label(
                        user_type=row.get("user_type"),
                        role=row.get("role"),
                    ),
                    "status": self._normalize_status(row.get("status")),
                }
                for row in rows
            ]
            return {
                "success": True,
                "data": {
                    "secondary_department_id": int(secondary["id"]),
                    "primary_department_id": primary_id,
                    "primary_department_name": (primary or {}).get("name"),
                    "secondary_department_name": secondary.get("name"),
                    "user_count": len(users),
                    "users": users,
                },
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "获取部门用户失败", "code": "FETCH_ERROR"}

    def list_secondary_legacy_users(self, *, secondary_id: int) -> dict[str, Any]:
        try:
            secondary = self._repository.get_secondary_by_id(int(secondary_id))
            if not secondary:
                return {"success": False, "error": "二级部门不存在", "code": "SECONDARY_DEPARTMENT_NOT_FOUND"}

            primary_id = int(secondary["primary_department_id"])
            primary = self._repository.get_primary_by_id(primary_id)
            rows = self._repository.list_legacy_users_by_secondary_department(secondary_id=int(secondary["id"]))
            users = [
                {
                    "id": int(row["id"]),
                    "username": row["username"],
                    "user_type": self._user_type_code(
                        user_type=row.get("user_type"),
                        role=row.get("role"),
                    ),
                    "user_type_label": self._user_type_label(
                        user_type=row.get("user_type"),
                        role=row.get("role"),
                    ),
                    "status": self._normalize_status(row.get("status")),
                }
                for row in rows
            ]
            return {
                "success": True,
                "data": {
                    "secondary_department_id": int(secondary["id"]),
                    "primary_department_id": primary_id,
                    "primary_department_name": (primary or {}).get("name"),
                    "secondary_department_name": secondary.get("name"),
                    "user_count": len(users),
                    "users": users,
                },
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "获取部门遗留用户失败", "code": "FETCH_ERROR"}

    def list_tertiary_users(self, *, tertiary_id: int) -> dict[str, Any]:
        try:
            tertiary = self._get_tertiary_by_id(int(tertiary_id))
            if not tertiary:
                return {"success": False, "error": "三级部门不存在", "code": "TERTIARY_DEPARTMENT_NOT_FOUND"}

            secondary_id = int(tertiary["secondary_department_id"])
            secondary = self._repository.get_secondary_by_id(secondary_id)
            primary = self._repository.get_primary_by_id(int((secondary or {}).get("primary_department_id") or 0)) if secondary else None
            rows = self._repository.list_users_by_tertiary_department(tertiary_id=int(tertiary["id"]))
            users = [
                {
                    "id": int(row["id"]),
                    "username": row["username"],
                    "user_type": self._user_type_code(
                        user_type=row.get("user_type"),
                        role=row.get("role"),
                    ),
                    "user_type_label": self._user_type_label(
                        user_type=row.get("user_type"),
                        role=row.get("role"),
                    ),
                    "status": self._normalize_status(row.get("status")),
                }
                for row in rows
            ]
            return {
                "success": True,
                "data": {
                    "tertiary_department_id": int(tertiary["id"]),
                    "secondary_department_id": secondary_id,
                    "primary_department_id": int((secondary or {}).get("primary_department_id") or 0) if secondary else None,
                    "primary_department_name": (primary or {}).get("name"),
                    "secondary_department_name": (secondary or {}).get("name"),
                    "tertiary_department_name": tertiary.get("name"),
                    "user_count": len(users),
                    "users": users,
                },
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "获取三级部门用户失败", "code": "FETCH_ERROR"}

    def get_selectable_tree(self) -> dict[str, Any]:
        try:
            rows = self._repository.list_department_tree(include_disabled=False)
            return {
                "success": True,
                "data": {
                    "items": [
                        {
                            "id": int(row["primary_id"]),
                            "name": row["primary_name"],
                            "secondary_items": [
                                {
                                    "id": int(secondary["id"]),
                                    "name": secondary["name"],
                                    "selectable": bool(secondary.get("tertiary_items")),
                                    "disabled_reason": None if secondary.get("tertiary_items") else "暂无三级部门，请联系管理员维护",
                                    "tertiary_items": [
                                        {
                                            "id": int(tertiary["id"]),
                                            "name": tertiary["name"],
                                        }
                                        for tertiary in secondary.get("tertiary_items") or []
                                    ],
                                }
                                for secondary in row.get("secondary_items") or []
                            ],
                        }
                        for row in rows
                    ]
                },
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "获取部门选项失败", "code": "FETCH_ERROR"}

    def describe_user_department(
        self,
        *,
        primary_department_id: int | None,
        secondary_department_id: int | None,
        tertiary_department_id: int | None = None,
    ) -> dict[str, Any]:
        primary_id = self._optional_int(primary_department_id)
        secondary_id = self._optional_int(secondary_department_id)
        tertiary_id = self._optional_int(tertiary_department_id)
        primary = self._repository.get_primary_by_id(primary_id) if primary_id is not None else None
        secondary = self._repository.get_secondary_by_id(secondary_id) if secondary_id is not None else None
        tertiary = self._get_tertiary_by_id(tertiary_id)
        primary_status = self._normalize_status((primary or {}).get("status")) if primary else None
        secondary_status = self._normalize_status((secondary or {}).get("status")) if secondary else None
        tertiary_status = self._normalize_status((tertiary or {}).get("status")) if tertiary else None

        secondary_relation_valid = (
            primary_id is not None
            and secondary_id is not None
            and primary is not None
            and secondary is not None
            and int(secondary.get("primary_department_id") or 0) == int(primary.get("id") or 0)
        )
        tertiary_relation_valid = (
            tertiary_id is not None
            and secondary_relation_valid
            and tertiary is not None
            and int(tertiary.get("secondary_department_id") or 0) == int(secondary.get("id") or 0)
        )

        if primary_id is None and secondary_id is None and tertiary_id is None:
            completion_level = "empty"
        elif secondary_relation_valid and tertiary_id is None:
            completion_level = "legacy_two_level_complete"
        elif tertiary_relation_valid:
            completion_level = "complete"
        else:
            completion_level = "invalid_partial"

        effective_status = None
        if completion_level == "legacy_two_level_complete":
            effective_status = self._effective_status(
                primary_status=primary_status,
                secondary_status=secondary_status,
            )
        elif completion_level == "complete":
            effective_status = self._effective_status(
                primary_status=primary_status,
                secondary_status=secondary_status,
                tertiary_status=tertiary_status,
            )
        primary_name = primary.get("name") if primary else None
        secondary_name = secondary.get("name") if secondary else None
        tertiary_name = tertiary.get("name") if tertiary else None

        return {
            "primary_department_id": primary_id,
            "primary_department_name": primary_name,
            "primary_department_status": primary_status,
            "secondary_department_id": secondary_id,
            "secondary_department_name": secondary_name,
            "secondary_department_status": secondary_status,
            "tertiary_department_id": tertiary_id,
            "tertiary_department_name": tertiary_name,
            "tertiary_department_status": tertiary_status,
            "department_effective_status": effective_status,
            "department_display": self._department_display(
                primary_name=primary_name,
                secondary_name=secondary_name,
                tertiary_name=tertiary_name,
                effective_status=effective_status,
            ),
            "department_completion_level": completion_level,
            "require_department_setup": completion_level in {"empty", "invalid_partial"},
        }

    def validate_department_selection(
        self,
        *,
        primary_department_id: int | None,
        secondary_department_id: int | None,
        tertiary_department_id: int | None = None,
        require_active: bool,
        allow_empty: bool,
        allow_legacy_two_level: bool = True,
    ) -> dict[str, Any]:
        primary_id = self._optional_int(primary_department_id)
        secondary_id = self._optional_int(secondary_department_id)
        tertiary_id = self._optional_int(tertiary_department_id)

        if primary_id is None and secondary_id is None and tertiary_id is None:
            if allow_empty:
                return {"success": True, "data": self.describe_user_department(
                    primary_department_id=None,
                    secondary_department_id=None,
                    tertiary_department_id=None,
                )}
            return {"success": False, "error": "请选择一级、二级和三级部门", "code": "DEPARTMENT_REQUIRED"}

        if primary_id is None or secondary_id is None:
            return {"success": False, "error": "一级和二级部门必须同时填写", "code": "DEPARTMENT_REQUIRED"}

        primary = self._repository.get_primary_by_id(primary_id)
        if not primary:
            return {"success": False, "error": "一级部门不存在", "code": "PRIMARY_DEPARTMENT_NOT_FOUND"}

        secondary = self._repository.get_secondary_by_id(secondary_id)
        if not secondary:
            return {"success": False, "error": "二级部门不存在", "code": "SECONDARY_DEPARTMENT_NOT_FOUND"}

        if int(secondary.get("primary_department_id") or 0) != primary_id:
            return {"success": False, "error": "二级部门不属于所选一级部门", "code": "DEPARTMENT_RELATION_INVALID"}

        if require_active and (
            str(primary.get("status") or "").strip().lower() != "active"
            or str(secondary.get("status") or "").strip().lower() != "active"
        ):
            return {"success": False, "error": "部门已停用，无法选择", "code": "DEPARTMENT_DISABLED"}

        if tertiary_id is None:
            if not allow_legacy_two_level:
                return {"success": False, "error": "一级、二级和三级部门必须同时填写", "code": "DEPARTMENT_REQUIRED"}
            return {"success": True, "data": self.describe_user_department(
                primary_department_id=primary_id,
                secondary_department_id=secondary_id,
                tertiary_department_id=None,
            )}

        tertiary = self._get_tertiary_by_id(tertiary_id)
        if not tertiary:
            return {"success": False, "error": "三级部门不存在", "code": "TERTIARY_DEPARTMENT_NOT_FOUND"}

        if int(tertiary.get("secondary_department_id") or 0) != secondary_id:
            return {"success": False, "error": "三级部门不属于所选二级部门", "code": "DEPARTMENT_RELATION_INVALID"}

        if require_active and str(tertiary.get("status") or "").strip().lower() != "active":
            return {"success": False, "error": "部门已停用，无法选择", "code": "DEPARTMENT_DISABLED"}

        return {"success": True, "data": self.describe_user_department(
            primary_department_id=primary_id,
            secondary_department_id=secondary_id,
            tertiary_department_id=tertiary_id,
        )}

    def resolve_by_names(
        self,
        *,
        primary_name: str,
        secondary_name: str,
        tertiary_name: str | None = None,
        active_only: bool,
        allow_legacy_two_level: bool = True,
    ) -> dict[str, Any]:
        primary_text = self.clean_text(primary_name)
        secondary_text = self.clean_text(secondary_name)
        tertiary_text = self.clean_text(tertiary_name)
        if not primary_text or not secondary_text:
            return {"success": False, "error": "一级和二级部门必须同时填写", "code": "DEPARTMENT_REQUIRED"}

        primary = self._repository.get_primary_by_name(primary_text)
        if not primary:
            return {"success": False, "error": "一级部门不存在", "code": "PRIMARY_DEPARTMENT_NOT_FOUND"}

        secondary = self._repository.get_secondary_by_name(
            primary_department_id=int(primary["id"]),
            name=secondary_text,
        )
        if not secondary:
            return {"success": False, "error": "二级部门不存在", "code": "SECONDARY_DEPARTMENT_NOT_FOUND"}

        if not tertiary_text:
            if not allow_legacy_two_level:
                return {"success": False, "error": "一级、二级和三级部门必须同时填写", "code": "DEPARTMENT_REQUIRED"}
            if active_only and (
                str(primary.get("status") or "").strip().lower() != "active"
                or str(secondary.get("status") or "").strip().lower() != "active"
            ):
                return {"success": False, "error": "部门已停用，无法选择", "code": "DEPARTMENT_DISABLED"}
            return {
                "success": True,
                "data": self.describe_user_department(
                    primary_department_id=int(primary["id"]),
                    secondary_department_id=int(secondary["id"]),
                    tertiary_department_id=None,
                ),
            }

        tertiary = self._get_tertiary_by_name(
            secondary_department_id=int(secondary["id"]),
            name=tertiary_text,
        )
        if not tertiary:
            return {"success": False, "error": "三级部门不存在", "code": "TERTIARY_DEPARTMENT_NOT_FOUND"}

        if active_only and (
            str(primary.get("status") or "").strip().lower() != "active"
            or str(secondary.get("status") or "").strip().lower() != "active"
            or str(tertiary.get("status") or "").strip().lower() != "active"
        ):
            return {"success": False, "error": "部门已停用，无法选择", "code": "DEPARTMENT_DISABLED"}

        return {
            "success": True,
            "data": self.describe_user_department(
                primary_department_id=int(primary["id"]),
                secondary_department_id=int(secondary["id"]),
                tertiary_department_id=int(tertiary["id"]),
            ),
        }

    def create_primary(self, *, name: str) -> dict[str, Any]:
        primary_name = self.clean_text(name)
        if not primary_name:
            return {"success": False, "error": "部门名称不能为空", "code": "DEPARTMENT_NAME_REQUIRED"}
        try:
            if self._repository.get_primary_by_name(primary_name):
                return {"success": False, "error": "一级部门名称已存在", "code": "PRIMARY_DEPARTMENT_NAME_EXISTS"}
            primary_id = self._repository.create_primary(name=primary_name)
            created = self._repository.get_primary_by_id(primary_id)
            if not created:
                return {"success": False, "error": "创建一级部门失败", "code": "CREATE_ERROR"}
            return {"success": True, "message": "一级部门创建成功", "data": self._build_primary_payload(created)}
        except Exception as exc:
            if self._duplicate_error(exc):
                return {"success": False, "error": "一级部门名称已存在", "code": "PRIMARY_DEPARTMENT_NAME_EXISTS"}
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "创建一级部门失败", "code": "CREATE_ERROR"}

    def rename_primary(self, *, primary_id: int, name: str) -> dict[str, Any]:
        primary_name = self.clean_text(name)
        if not primary_name:
            return {"success": False, "error": "部门名称不能为空", "code": "DEPARTMENT_NAME_REQUIRED"}
        try:
            primary = self._repository.get_primary_by_id(primary_id)
            if not primary:
                return {"success": False, "error": "一级部门不存在", "code": "PRIMARY_DEPARTMENT_NOT_FOUND"}
            existing = self._repository.get_primary_by_name(primary_name)
            if existing and int(existing["id"]) != int(primary_id):
                return {"success": False, "error": "一级部门名称已存在", "code": "PRIMARY_DEPARTMENT_NAME_EXISTS"}
            self._repository.update_primary_name(primary_id=primary_id, name=primary_name)
            updated = self._repository.get_primary_by_id(primary_id) or {**primary, "name": primary_name}
            return {"success": True, "message": "一级部门已更新", "data": self._build_primary_payload(updated)}
        except Exception as exc:
            if self._duplicate_error(exc):
                return {"success": False, "error": "一级部门名称已存在", "code": "PRIMARY_DEPARTMENT_NAME_EXISTS"}
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "更新一级部门失败", "code": "UPDATE_ERROR"}

    def delete_primary(self, *, primary_id: int) -> dict[str, Any]:
        try:
            primary = self._repository.get_primary_by_id(primary_id)
            if not primary:
                return {"success": False, "error": "一级部门不存在", "code": "PRIMARY_DEPARTMENT_NOT_FOUND"}

            secondary_count = self._repository.count_secondary_departments_by_primary(primary_id=primary_id)
            if secondary_count > 0:
                return self._department_in_use("该一级部门下还有二级部门，请先删除下级部门")

            user_count = self._repository.count_users_by_primary_department(primary_id=primary_id)
            if user_count > 0:
                return self._department_in_use("该一级部门仍有关联账号，请先调整账号部门")

            personnel_count = self._repository.count_personnel_by_primary_department(primary_id=primary_id)
            if personnel_count > 0:
                return self._department_in_use("该一级部门仍有关联人员，请先调整人员部门")

            deleted_count = self._repository.delete_primary(primary_id=primary_id)
            if deleted_count <= 0:
                return {"success": False, "error": "一级部门不存在", "code": "PRIMARY_DEPARTMENT_NOT_FOUND"}
            return {"success": True, "message": "一级部门已删除", "data": self._delete_payload(primary)}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "删除一级部门失败", "code": "DELETE_ERROR"}

    def update_primary_status(self, *, primary_id: int, status: str) -> dict[str, Any]:
        status_value = self.clean_text(status).lower()
        if not self._valid_status(status_value):
            return {"success": False, "error": "状态必须是 active 或 disabled", "code": "VALIDATION_ERROR"}
        try:
            primary = self._repository.get_primary_by_id(primary_id)
            if not primary:
                return {"success": False, "error": "一级部门不存在", "code": "PRIMARY_DEPARTMENT_NOT_FOUND"}
            self._repository.update_primary_status(primary_id=primary_id, status=status_value)
            updated = self._repository.get_primary_by_id(primary_id) or {**primary, "status": status_value}
            return {"success": True, "message": "一级部门状态已更新", "data": self._build_primary_payload(updated)}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "更新一级部门状态失败", "code": "UPDATE_ERROR"}

    def create_secondary(self, *, primary_department_id: int, name: str) -> dict[str, Any]:
        secondary_name = self.clean_text(name)
        primary_id = self._optional_int(primary_department_id)
        if primary_id is None:
            return {"success": False, "error": "请选择一级部门", "code": "PRIMARY_DEPARTMENT_REQUIRED"}
        if not secondary_name:
            return {"success": False, "error": "部门名称不能为空", "code": "DEPARTMENT_NAME_REQUIRED"}
        try:
            primary = self._repository.get_primary_by_id(primary_id)
            if not primary:
                return {"success": False, "error": "一级部门不存在", "code": "PRIMARY_DEPARTMENT_NOT_FOUND"}
            existing = self._repository.get_secondary_by_name(primary_department_id=primary_id, name=secondary_name)
            if existing:
                return {"success": False, "error": "二级部门名称已存在", "code": "SECONDARY_DEPARTMENT_NAME_EXISTS"}
            secondary_id = self._repository.create_secondary(primary_department_id=primary_id, name=secondary_name)
            created = self._repository.get_secondary_by_id(secondary_id)
            if not created:
                return {"success": False, "error": "创建二级部门失败", "code": "CREATE_ERROR"}
            return {
                "success": True,
                "message": "二级部门创建成功",
                "data": self._build_secondary_payload(
                    created,
                    primary_status=self._normalize_status(primary.get("status")),
                ),
            }
        except Exception as exc:
            if self._duplicate_error(exc):
                return {"success": False, "error": "二级部门名称已存在", "code": "SECONDARY_DEPARTMENT_NAME_EXISTS"}
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "创建二级部门失败", "code": "CREATE_ERROR"}

    def create_tertiary(self, *, secondary_department_id: int, name: str) -> dict[str, Any]:
        tertiary_name = self.clean_text(name)
        secondary_id = self._optional_int(secondary_department_id)
        if secondary_id is None:
            return {"success": False, "error": "请选择二级部门", "code": "DEPARTMENT_REQUIRED"}
        if not tertiary_name:
            return {"success": False, "error": "部门名称不能为空", "code": "DEPARTMENT_NAME_REQUIRED"}
        try:
            secondary = self._repository.get_secondary_by_id(secondary_id)
            if not secondary:
                return {"success": False, "error": "二级部门不存在", "code": "SECONDARY_DEPARTMENT_NOT_FOUND"}
            existing = self._get_tertiary_by_name(secondary_department_id=secondary_id, name=tertiary_name)
            if existing:
                return {"success": False, "error": "三级部门名称已存在", "code": "TERTIARY_DEPARTMENT_NAME_EXISTS"}
            tertiary_id = self._repository.create_tertiary(secondary_department_id=secondary_id, name=tertiary_name)
            created = self._get_tertiary_by_id(tertiary_id)
            if not created:
                return {"success": False, "error": "创建三级部门失败", "code": "CREATE_ERROR"}
            primary = self._repository.get_primary_by_id(int(secondary["primary_department_id"]))
            return {
                "success": True,
                "message": "三级部门创建成功",
                "data": self._build_tertiary_payload(
                    created,
                    primary_status=self._normalize_status((primary or {}).get("status")),
                    secondary_status=self._normalize_status(secondary.get("status")),
                ),
            }
        except Exception as exc:
            if self._duplicate_error(exc):
                return {"success": False, "error": "三级部门名称已存在", "code": "TERTIARY_DEPARTMENT_NAME_EXISTS"}
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "创建三级部门失败", "code": "CREATE_ERROR"}

    def rename_secondary(self, *, secondary_id: int, name: str) -> dict[str, Any]:
        secondary_name = self.clean_text(name)
        if not secondary_name:
            return {"success": False, "error": "部门名称不能为空", "code": "DEPARTMENT_NAME_REQUIRED"}
        try:
            secondary = self._repository.get_secondary_by_id(secondary_id)
            if not secondary:
                return {"success": False, "error": "二级部门不存在", "code": "SECONDARY_DEPARTMENT_NOT_FOUND"}
            existing = self._repository.get_secondary_by_name(
                primary_department_id=int(secondary["primary_department_id"]),
                name=secondary_name,
            )
            if existing and int(existing["id"]) != int(secondary_id):
                return {"success": False, "error": "二级部门名称已存在", "code": "SECONDARY_DEPARTMENT_NAME_EXISTS"}
            self._repository.update_secondary_name(secondary_id=secondary_id, name=secondary_name)
            updated = self._repository.get_secondary_by_id(secondary_id) or {**secondary, "name": secondary_name}
            primary = self._repository.get_primary_by_id(int(updated["primary_department_id"]))
            return {
                "success": True,
                "message": "二级部门已更新",
                "data": self._build_secondary_payload(
                    updated,
                    primary_status=self._normalize_status((primary or {}).get("status")),
                ),
            }
        except Exception as exc:
            if self._duplicate_error(exc):
                return {"success": False, "error": "二级部门名称已存在", "code": "SECONDARY_DEPARTMENT_NAME_EXISTS"}
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "更新二级部门失败", "code": "UPDATE_ERROR"}

    def delete_secondary(self, *, secondary_id: int) -> dict[str, Any]:
        try:
            secondary = self._repository.get_secondary_by_id(secondary_id)
            if not secondary:
                return {"success": False, "error": "二级部门不存在", "code": "SECONDARY_DEPARTMENT_NOT_FOUND"}

            tertiary_count = self._repository.count_tertiary_departments_by_secondary(secondary_id=secondary_id)
            if tertiary_count > 0:
                return self._department_in_use("该二级部门下还有三级部门，请先删除下级部门")

            user_count = self._repository.count_users_by_secondary_department(secondary_id=secondary_id)
            if user_count > 0:
                return self._department_in_use("该二级部门仍有关联账号，请先调整账号部门")

            personnel_count = self._repository.count_personnel_by_secondary_department(secondary_id=secondary_id)
            if personnel_count > 0:
                return self._department_in_use("该二级部门仍有关联人员，请先调整人员部门")

            deleted_count = self._repository.delete_secondary(secondary_id=secondary_id)
            if deleted_count <= 0:
                return {"success": False, "error": "二级部门不存在", "code": "SECONDARY_DEPARTMENT_NOT_FOUND"}
            return {"success": True, "message": "二级部门已删除", "data": self._delete_payload(secondary)}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "删除二级部门失败", "code": "DELETE_ERROR"}

    def rename_tertiary(self, *, tertiary_id: int, name: str) -> dict[str, Any]:
        tertiary_name = self.clean_text(name)
        if not tertiary_name:
            return {"success": False, "error": "部门名称不能为空", "code": "DEPARTMENT_NAME_REQUIRED"}
        try:
            tertiary = self._get_tertiary_by_id(tertiary_id)
            if not tertiary:
                return {"success": False, "error": "三级部门不存在", "code": "TERTIARY_DEPARTMENT_NOT_FOUND"}
            existing = self._get_tertiary_by_name(
                secondary_department_id=int(tertiary["secondary_department_id"]),
                name=tertiary_name,
            )
            if existing and int(existing["id"]) != int(tertiary_id):
                return {"success": False, "error": "三级部门名称已存在", "code": "TERTIARY_DEPARTMENT_NAME_EXISTS"}
            self._repository.update_tertiary_name(tertiary_id=tertiary_id, name=tertiary_name)
            updated = self._get_tertiary_by_id(tertiary_id) or {**tertiary, "name": tertiary_name}
            secondary = self._repository.get_secondary_by_id(int(updated["secondary_department_id"]))
            primary = self._repository.get_primary_by_id(int((secondary or {}).get("primary_department_id") or 0)) if secondary else None
            return {
                "success": True,
                "message": "三级部门已更新",
                "data": self._build_tertiary_payload(
                    updated,
                    primary_status=self._normalize_status((primary or {}).get("status")),
                    secondary_status=self._normalize_status((secondary or {}).get("status")),
                ),
            }
        except Exception as exc:
            if self._duplicate_error(exc):
                return {"success": False, "error": "三级部门名称已存在", "code": "TERTIARY_DEPARTMENT_NAME_EXISTS"}
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "更新三级部门失败", "code": "UPDATE_ERROR"}

    def delete_tertiary(self, *, tertiary_id: int) -> dict[str, Any]:
        try:
            tertiary = self._get_tertiary_by_id(tertiary_id)
            if not tertiary:
                return {"success": False, "error": "三级部门不存在", "code": "TERTIARY_DEPARTMENT_NOT_FOUND"}

            user_count = self._repository.count_users_by_tertiary_department(tertiary_id=tertiary_id)
            if user_count > 0:
                return self._department_in_use("该三级部门仍有关联账号，请先调整账号部门")

            personnel_count = self._repository.count_personnel_by_tertiary_department(tertiary_id=tertiary_id)
            if personnel_count > 0:
                return self._department_in_use("该三级部门仍有关联人员，请先调整人员部门")

            deleted_count = self._repository.delete_tertiary(tertiary_id=tertiary_id)
            if deleted_count <= 0:
                return {"success": False, "error": "三级部门不存在", "code": "TERTIARY_DEPARTMENT_NOT_FOUND"}
            return {"success": True, "message": "三级部门已删除", "data": self._delete_payload(tertiary)}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "删除三级部门失败", "code": "DELETE_ERROR"}

    def update_secondary_status(self, *, secondary_id: int, status: str) -> dict[str, Any]:
        status_value = self.clean_text(status).lower()
        if not self._valid_status(status_value):
            return {"success": False, "error": "状态必须是 active 或 disabled", "code": "VALIDATION_ERROR"}
        try:
            secondary = self._repository.get_secondary_by_id(secondary_id)
            if not secondary:
                return {"success": False, "error": "二级部门不存在", "code": "SECONDARY_DEPARTMENT_NOT_FOUND"}
            self._repository.update_secondary_status(secondary_id=secondary_id, status=status_value)
            updated = self._repository.get_secondary_by_id(secondary_id) or {**secondary, "status": status_value}
            primary = self._repository.get_primary_by_id(int(updated["primary_department_id"]))
            return {
                "success": True,
                "message": "二级部门状态已更新",
                "data": self._build_secondary_payload(
                    updated,
                    primary_status=self._normalize_status((primary or {}).get("status")),
                ),
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "更新二级部门状态失败", "code": "UPDATE_ERROR"}

    def update_tertiary_status(self, *, tertiary_id: int, status: str) -> dict[str, Any]:
        status_value = self.clean_text(status).lower()
        if not self._valid_status(status_value):
            return {"success": False, "error": "状态必须是 active 或 disabled", "code": "VALIDATION_ERROR"}
        try:
            tertiary = self._get_tertiary_by_id(tertiary_id)
            if not tertiary:
                return {"success": False, "error": "三级部门不存在", "code": "TERTIARY_DEPARTMENT_NOT_FOUND"}
            self._repository.update_tertiary_status(tertiary_id=tertiary_id, status=status_value)
            updated = self._get_tertiary_by_id(tertiary_id) or {**tertiary, "status": status_value}
            secondary = self._repository.get_secondary_by_id(int(updated["secondary_department_id"]))
            primary = self._repository.get_primary_by_id(int((secondary or {}).get("primary_department_id") or 0)) if secondary else None
            return {
                "success": True,
                "message": "三级部门状态已更新",
                "data": self._build_tertiary_payload(
                    updated,
                    primary_status=self._normalize_status((primary or {}).get("status")),
                    secondary_status=self._normalize_status((secondary or {}).get("status")),
                ),
            }
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return self._db_error(exc)
            return {"success": False, "error": "更新三级部门状态失败", "code": "UPDATE_ERROR"}


department_service = DepartmentService()
