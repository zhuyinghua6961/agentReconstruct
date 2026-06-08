from __future__ import annotations

import csv
import io
import time
from typing import Any

from fastapi.responses import Response

from app.core.import_columns import resolve_column_aliases
from app.core.spreadsheet import build_xlsx, load_rows
from app.modules.admin_users.service import _is_db_unavailable_error, admin_users_service
from app.modules.quota.deps import finalize_quota, precheck_quota
from app.modules.quota.service import QuotaGrant


REQUIRED_COLUMN_ALIASES = {
    "username": ("用户名", "username"),
    "password": ("密码", "password"),
}
OPTIONAL_COLUMN_ALIASES = {
    "user_type": ("用户类型", "user_type"),
}
TEMPLATE_HEADERS = ["用户名", "密码", "用户类型"]
USER_TYPE_CODES = {
    "common": 3,
    "super": 2,
}


class AdminUsersImportService:
    def import_users(self, *, file_bytes: bytes, filename: str, actor_user_id: int) -> dict[str, Any]:
        filename = admin_users_service.clean_text(filename)
        if not filename:
            return {"success": False, "error": "文件名为空", "code": "FILENAME_EMPTY"}

        quota_grant, quota_error = self._precheck_excel_upload_quota(actor_user_id=actor_user_id)
        if quota_error is not None:
            return quota_error
        result: dict[str, Any] = {"success": False, "error": "import_aborted", "code": "IMPORT_ABORTED"}
        try:
            ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
            if ext not in {"xlsx", "csv"}:
                result = {"success": False, "error": "不支持的文件格式，只支持.xlsx和.csv", "code": "INVALID_FILE_TYPE"}
                return result

            started = time.monotonic()
            try:
                rows = load_rows(file_bytes=file_bytes, ext=ext)
            except ValueError as exc:
                result = {"success": False, "error": str(exc), "code": "VALIDATION_ERROR"}
                return result
            except Exception:
                result = {"success": False, "error": "批量导入失败", "code": "IMPORT_ERROR"}
                return result

            columns, missing = resolve_column_aliases(
                rows["columns"],
                REQUIRED_COLUMN_ALIASES,
                OPTIONAL_COLUMN_ALIASES,
            )
            if missing:
                result = {
                    "success": False,
                    "error": f"缺少必要列: {', '.join(missing)}",
                    "code": "VALIDATION_ERROR",
                }
                return result

            username_col = columns["username"]
            password_col = columns["password"]
            user_type_col = columns.get("user_type")

            duplicate_usernames = self._duplicate_usernames_by_row(rows=rows["items"], username_col=username_col)

            details: list[dict[str, Any]] = []
            success_count = 0
            updated_count = 0
            failed_count = 0
            skipped_count = 0

            for index, row in enumerate(rows["items"]):
                line_no = index + 2
                username = admin_users_service.clean_text(row.get(username_col))
                password = str(row.get(password_col) or "")
                user_type = admin_users_service.clean_text(
                    row.get(user_type_col) if user_type_col else "common"
                ).lower() or "common"

                if not username:
                    failed_count += 1
                    details.append({"row": line_no, "username": "", "status": "failed", "reason": "用户名为空"})
                    continue
                if username in duplicate_usernames:
                    failed_count += 1
                    line_nos = ",".join(str(item) for item in duplicate_usernames[username])
                    details.append(
                        {
                            "row": line_no,
                            "username": username,
                            "status": "failed",
                            "reason": f"导入文件中存在重复用户名（行号: {line_nos}）",
                        }
                    )
                    continue
                if len(password) < 6:
                    failed_count += 1
                    details.append({"row": line_no, "username": username, "status": "failed", "reason": "密码长度不能少于6位"})
                    continue
                if user_type not in {"common", "super"}:
                    failed_count += 1
                    details.append({"row": line_no, "username": username, "status": "failed", "reason": "user_type必须是common或super"})
                    continue

                apply_result = self._apply_import_row(username=username, password=password, user_type=user_type)
                status = str(apply_result.get("status") or "")
                if status == "success":
                    success_count += 1
                    details.append(
                        {
                            "row": line_no,
                            "username": str(apply_result.get("username") or username),
                            "status": "success",
                            "message": str(apply_result.get("message") or "新增成功"),
                            **({"user_id": apply_result["user_id"]} if apply_result.get("user_id") is not None else {}),
                        }
                    )
                    continue
                if status == "updated":
                    updated_count += 1
                    details.append(
                        {
                            "row": line_no,
                            "username": str(apply_result.get("username") or username),
                            "status": "updated",
                            "message": str(apply_result.get("message") or "更新成功"),
                            **({"user_id": apply_result["user_id"]} if apply_result.get("user_id") is not None else {}),
                        }
                    )
                    continue
                if status == "skipped":
                    skipped_count += 1
                    details.append(
                        {
                            "row": line_no,
                            "username": str(apply_result.get("username") or username),
                            "status": "skipped",
                            "reason": str(apply_result.get("message") or "账号已存在且未变化"),
                            **({"user_id": apply_result["user_id"]} if apply_result.get("user_id") is not None else {}),
                        }
                    )
                    continue
                failed_count += 1
                details.append(
                    {
                        "row": line_no,
                        "username": username,
                        "status": "failed",
                        "reason": str(apply_result.get("message") or apply_result.get("code") or "导入失败"),
                    }
                )

            total = success_count + updated_count + failed_count + skipped_count
            duration = round(time.monotonic() - started, 2)
            result = {
                "success": True,
                "message": "批量导入完成",
                "data": {
                    "summary": {
                        "total": total,
                        "success": success_count,
                        "updated": updated_count,
                        "failed": failed_count,
                        "skipped": skipped_count,
                    },
                    "details": details,
                    "duration": duration,
                },
            }
            return result
        finally:
            self._finalize_excel_upload_quota(actor_user_id=actor_user_id, grant=quota_grant, result=result)

    def _duplicate_usernames_by_row(self, *, rows: list[dict[str, Any]], username_col: Any) -> dict[str, list[int]]:
        seen_rows: dict[str, list[int]] = {}
        for index, row in enumerate(rows):
            line_no = index + 2
            username = admin_users_service.clean_text(row.get(username_col))
            if username:
                seen_rows.setdefault(username, []).append(line_no)
        return {username: line_nos for username, line_nos in seen_rows.items() if len(line_nos) > 1}

    def _apply_import_row(self, *, username: str, password: str, user_type: str) -> dict[str, Any]:
        try:
            existing = admin_users_service.users.get_by_username(username)
            if existing:
                return self._apply_existing_user_update(existing=existing, password=password, user_type=user_type)

            create_result = admin_users_service.create_user(username=username, password=password, user_type=user_type)
            if create_result.get("success"):
                data = create_result.get("data") if isinstance(create_result.get("data"), dict) else {}
                return {
                    "status": "success",
                    "username": str(data.get("username") or username),
                    "user_id": data.get("id"),
                    "message": "新增成功",
                }
            return {
                "status": "failed",
                "username": username,
                "message": str(create_result.get("error") or create_result.get("code") or "创建失败"),
                "code": str(create_result.get("code") or ""),
            }
        except Exception as exc:
            return {
                "status": "failed",
                "username": username,
                "message": str(exc) if _is_db_unavailable_error(exc) else "导入用户失败",
                "code": "DB_UNAVAILABLE" if _is_db_unavailable_error(exc) else "IMPORT_ERROR",
            }

    def _apply_existing_user_update(
        self,
        *,
        existing: dict[str, Any],
        password: str,
        user_type: str,
    ) -> dict[str, Any]:
        user_id = int(existing.get("id") or 0)
        username = str(existing.get("username") or "")
        role_text = admin_users_service.clean_text(existing.get("role")).lower()
        current_type = int(existing.get("user_type") or admin_users_service._role_to_user_type(role_text))
        if role_text == "admin" or current_type == 1:
            return {
                "status": "failed",
                "username": username,
                "user_id": user_id,
                "message": "不能通过导入修改管理员账号",
                "code": "PERMISSION_DENIED",
            }

        target_type = USER_TYPE_CODES[user_type]
        password_changed = not admin_users_service.verify_password(password, str(existing.get("password_hash") or ""))
        type_changed = current_type != target_type
        if not password_changed and not type_changed:
            return {
                "status": "skipped",
                "username": username,
                "user_id": user_id,
                "message": "账号已存在且未变化",
            }

        changed_parts: list[str] = []
        if password_changed:
            password_hash = admin_users_service.hash_password(password)
            admin_users_service.users.update_password_hash(user_id=user_id, password_hash=password_hash)
            admin_users_service.users.add_password_history(user_id=user_id, password_hash=password_hash)
            admin_users_service.users.trim_password_history(
                user_id=user_id,
                keep_limit=admin_users_service._password_history_limit(role_text or "user"),
            )
            mark_first_login_required = getattr(admin_users_service.users, "mark_first_login_required", None)
            if callable(mark_first_login_required):
                mark_first_login_required(user_id=user_id)
            set_security_setup_required = getattr(admin_users_service.users, "set_security_setup_required", None)
            if callable(set_security_setup_required):
                set_security_setup_required(user_id=user_id, required=True)
            changed_parts.append("密码")

        if type_changed:
            admin_users_service.users.update_user_type(user_id=user_id, user_type=target_type)
            changed_parts.append("用户类型")

        return {
            "status": "updated",
            "username": username,
            "user_id": user_id,
            "message": f"已更新{','.join(changed_parts)}",
        }

    def template_response(self, *, fmt: str) -> Response | dict[str, Any]:
        fmt = admin_users_service.clean_text(fmt or "xlsx").lower()
        if fmt not in {"xlsx", "csv"}:
            return {"success": False, "error": "不支持的格式，只支持xlsx和csv", "code": "INVALID_FORMAT"}

        headers = TEMPLATE_HEADERS
        rows = [
            ["user001", "Pass123!", "common"],
            ["user002", "Test456@", "super"],
            ["user003", "Demo789#", "common"],
        ]

        if fmt == "csv":
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerow(headers)
            writer.writerows(rows)
            return Response(
                content=buffer.getvalue().encode("utf-8-sig"),
                media_type="text/csv",
                headers={"Content-Disposition": 'attachment; filename="user_import_template.csv"'},
            )

        return Response(
            content=build_xlsx(headers=headers, rows=rows, sheet_name="用户导入"),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="user_import_template.xlsx"'},
        )

    def _precheck_excel_upload_quota(
        self,
        *,
        actor_user_id: int,
    ) -> tuple[QuotaGrant | None, dict[str, Any] | None]:
        try:
            user = admin_users_service.users.get_by_id(int(actor_user_id))
            if user and int(user.get("user_type") or 0) in {1, 2}:
                return None, None
        except Exception as exc:
            return None, {
                "success": False,
                "error": str(exc),
                "code": "DB_UNAVAILABLE",
            }

        try:
            return precheck_quota(user_id=int(actor_user_id), quota_type="excel_upload"), None
        except Exception as exc:
            if hasattr(exc, "extra_payload") and isinstance(getattr(exc, "extra_payload"), dict):
                payload = dict(getattr(exc, "extra_payload"))
            else:
                payload = {}
            return None, {
                "success": False,
                "error": getattr(exc, "message", "quota_check_failed"),
                "code": getattr(exc, "code", "DB_UNAVAILABLE"),
                **({"data": payload.get("data")} if "data" in payload else {}),
            }

    def _finalize_excel_upload_quota(
        self,
        *,
        actor_user_id: int,
        grant: QuotaGrant | None,
        result: dict[str, Any],
    ) -> None:
        _ = actor_user_id
        finalize_result = finalize_quota(grant, result=result)
        if isinstance(finalize_result, dict) and finalize_result.get("success") is False:
            result["quota_counted"] = False
            result["quota_warning"] = str(
                finalize_result.get("error") or finalize_result.get("code") or "quota_increment_failed"
            )


admin_users_import_service = AdminUsersImportService()
