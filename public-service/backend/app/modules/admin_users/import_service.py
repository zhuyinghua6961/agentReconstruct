from __future__ import annotations

import csv
import io
import time
from typing import Any

from fastapi.responses import Response

from app.core.spreadsheet import build_xlsx, load_rows
from app.modules.departments.service import department_service
from app.modules.admin_users.service import admin_users_service
from app.modules.quota.deps import finalize_quota, precheck_quota
from app.modules.quota.service import QuotaGrant


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

            normalized = {str(col).strip().lower(): col for col in rows["columns"]}
            if "username" not in normalized or "password" not in normalized:
                result = {
                    "success": False,
                    "error": "缺少必要列，至少需要 username/password",
                    "code": "VALIDATION_ERROR",
                }
                return result

            username_col = normalized["username"]
            password_col = normalized["password"]
            user_type_col = normalized.get("user_type")
            primary_department_name_col = normalized.get("primary_department_name")
            secondary_department_name_col = normalized.get("secondary_department_name")

            details: list[dict[str, Any]] = []
            success_count = 0
            failed_count = 0
            skipped_count = 0

            for index, row in enumerate(rows["items"]):
                line_no = index + 2
                username = admin_users_service.clean_text(row.get(username_col))
                password = str(row.get(password_col) or "")
                user_type = admin_users_service.clean_text(
                    row.get(user_type_col) if user_type_col else "common"
                ).lower() or "common"
                primary_department_name = admin_users_service.clean_text(
                    row.get(primary_department_name_col) if primary_department_name_col else ""
                )
                secondary_department_name = admin_users_service.clean_text(
                    row.get(secondary_department_name_col) if secondary_department_name_col else ""
                )

                if not username:
                    failed_count += 1
                    details.append({"row": line_no, "username": "", "status": "failed", "reason": "用户名为空"})
                    continue
                if len(password) < 6:
                    failed_count += 1
                    details.append({"row": line_no, "username": username, "status": "failed", "reason": "密码长度不能少于6位"})
                    continue
                if user_type not in {"common", "super"}:
                    failed_count += 1
                    details.append({"row": line_no, "username": username, "status": "failed", "reason": "user_type必须是common或super"})
                    continue

                primary_department_id = None
                secondary_department_id = None
                if bool(primary_department_name) ^ bool(secondary_department_name):
                    failed_count += 1
                    details.append(
                        {
                            "row": line_no,
                            "username": username,
                            "status": "failed",
                            "reason": "部门信息必须同时填写一级和二级",
                        }
                    )
                    continue
                if primary_department_name and secondary_department_name:
                    resolved = department_service.resolve_by_names(
                        primary_name=primary_department_name,
                        secondary_name=secondary_department_name,
                        active_only=True,
                    )
                    if not resolved.get("success"):
                        failed_count += 1
                        details.append(
                            {
                                "row": line_no,
                                "username": username,
                                "status": "failed",
                                "reason": str(resolved.get("error") or resolved.get("code") or "部门解析失败"),
                            }
                        )
                        continue
                    resolved_data = resolved.get("data") if isinstance(resolved.get("data"), dict) else {}
                    primary_department_id = resolved_data.get("primary_department_id")
                    secondary_department_id = resolved_data.get("secondary_department_id")

                create_result = admin_users_service.create_user(
                    username=username,
                    password=password,
                    user_type=user_type,
                    primary_department_id=primary_department_id,
                    secondary_department_id=secondary_department_id,
                )
                if create_result.get("success"):
                    normalized_username = str((create_result.get("data") or {}).get("username") or username)
                    success_count += 1
                    details.append({"row": line_no, "username": normalized_username, "status": "success"})
                    continue
                if str(create_result.get("code") or "") == "USERNAME_EXISTS":
                    skipped_count += 1
                    details.append(
                        {
                            "row": line_no,
                            "username": username,
                            "status": "skipped",
                            "reason": str(create_result.get("error") or "用户名已存在"),
                        }
                    )
                    continue
                failed_count += 1
                details.append(
                    {
                        "row": line_no,
                        "username": username,
                        "status": "failed",
                        "reason": str(create_result.get("error") or create_result.get("code") or "创建失败"),
                    }
                )

            total = success_count + failed_count + skipped_count
            duration = round(time.monotonic() - started, 2)
            result = {
                "success": True,
                "message": "批量导入完成",
                "data": {
                    "summary": {
                        "total": total,
                        "success": success_count,
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

    def template_response(self, *, fmt: str) -> Response | dict[str, Any]:
        fmt = admin_users_service.clean_text(fmt or "xlsx").lower()
        if fmt not in {"xlsx", "csv"}:
            return {"success": False, "error": "不支持的格式，只支持xlsx和csv", "code": "INVALID_FORMAT"}

        headers = ["username", "password", "user_type", "primary_department_name", "secondary_department_name"]
        rows = [
            ["user001", "Pass123!", "common", "计算机学院", "软件工程系"],
            ["user002", "Test456@", "super", "化学学院", "材料系"],
            ["user003", "Demo789#", "common", "", ""],
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
