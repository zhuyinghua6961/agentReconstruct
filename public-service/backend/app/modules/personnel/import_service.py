from __future__ import annotations

import csv
import io
import logging
from typing import Any

from fastapi.responses import Response

from app.core.import_columns import resolve_column_aliases
from app.core.spreadsheet import build_xlsx, load_rows
from app.modules.auth.repository import AuthRepository
from app.modules.departments.service import department_service as shared_department_service
from app.modules.personnel.repository import PersonnelRepository, REMARKS_UNSET
from app.modules.personnel.service import PersonnelService, _is_db_unavailable_error


logger = logging.getLogger(__name__)

TEMPLATE_COLUMNS = ["工号", "姓名", "一级部门", "二级部门", "三级部门", "校验码", "备注"]
REQUIRED_COLUMN_ALIASES = {
    "employee_no": ("工号", "employee_no"),
    "full_name": ("姓名", "full_name"),
    "primary_department_name": ("一级部门名称", "一级部门", "primary_department_name"),
    "verification_code": ("校验码", "verification_code"),
}
OPTIONAL_COLUMN_ALIASES = {
    "secondary_department_name": ("二级部门名称", "二级部门", "secondary_department_name"),
    "tertiary_department_name": ("三级部门名称", "三级部门", "tertiary_department_name"),
    "remarks": ("备注", "remarks"),
    "status": ("状态", "status"),
}
VALID_STATUSES = {"active", "disabled"}
STATUS_ALIASES = {
    "active": "active",
    "enabled": "active",
    "启用": "active",
    "正常": "active",
    "disabled": "disabled",
    "停用": "disabled",
    "禁用": "disabled",
}


class PersonnelImportService:
    def __init__(
        self,
        *,
        repository: PersonnelRepository | None = None,
        service: PersonnelService | Any | None = None,
        department_service: Any | None = None,
        users_repo: AuthRepository | Any | None = None,
    ) -> None:
        self._repository = repository or PersonnelRepository()
        self._service = service or PersonnelService(repository=self._repository)
        self._departments = department_service or shared_department_service
        self._users = users_repo or AuthRepository()

    @staticmethod
    def _clean_text(value: object) -> str:
        return str(value or "").strip()

    def _normalize_status(self, value: object) -> str:
        status = self._clean_text(value).lower()
        if not status:
            return "active"
        return STATUS_ALIASES.get(status, status)

    def _validate_rows(
        self,
        *,
        items: list[dict[str, Any]],
        duplicate_employee_nos: dict[str, list[int]] | None,
        employee_no_col: str,
        full_name_col: str,
        verification_code_col: str,
        status_col: str | None,
        primary_department_name_col: str,
        secondary_department_name_col: str,
        tertiary_department_name_col: str,
        remarks_col: str | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
        validated_rows: list[dict[str, Any]] = []
        failed_details: list[dict[str, Any]] = []
        duplicate_employee_nos = duplicate_employee_nos or {}
        created_departments = {"primary": 0, "secondary": 0, "tertiary": 0, "total": 0}
        for index, row in enumerate(items):
            line_no = index + 2
            employee_no = self._clean_text(row.get(employee_no_col))
            full_name = self._clean_text(row.get(full_name_col))
            verification_code = self._clean_text(row.get(verification_code_col))
            status = self._normalize_status(row.get(status_col) if status_col else "")
            primary_department_name = self._clean_text(row.get(primary_department_name_col))
            secondary_department_name = self._clean_text(row.get(secondary_department_name_col))
            tertiary_department_name = self._clean_text(row.get(tertiary_department_name_col))
            remarks = self._clean_text(row.get(remarks_col)) if remarks_col else REMARKS_UNSET

            if not employee_no:
                failed_details.append(self._failed_detail(line_no=line_no, employee_no="", full_name=full_name, reason="工号为空"))
                continue
            if employee_no in duplicate_employee_nos:
                line_nos = ",".join(str(item) for item in duplicate_employee_nos[employee_no])
                failed_details.append(
                    self._failed_detail(
                        line_no=line_no,
                        employee_no=employee_no,
                        full_name=full_name,
                        reason=f"导入文件中存在重复工号（行号: {line_nos}）",
                    )
                )
                continue
            if not full_name:
                failed_details.append(self._failed_detail(line_no=line_no, employee_no=employee_no, full_name="", reason="姓名为空"))
                continue
            if not verification_code:
                failed_details.append(
                    self._failed_detail(line_no=line_no, employee_no=employee_no, full_name=full_name, reason="校验码为空")
                )
                continue
            if status not in VALID_STATUSES:
                failed_details.append(
                    self._failed_detail(
                        line_no=line_no,
                        employee_no=employee_no,
                        full_name=full_name,
                        reason="状态必须是 active/disabled 或 启用/停用",
                    )
                )
                continue
            if not primary_department_name:
                failed_details.append(
                    self._failed_detail(line_no=line_no, employee_no=employee_no, full_name=full_name, reason="一级部门名称不能为空")
                )
                continue
            if tertiary_department_name and not secondary_department_name:
                failed_details.append(
                    self._failed_detail(
                        line_no=line_no,
                        employee_no=employee_no,
                        full_name=full_name,
                        reason="三级部门名称不能在二级部门为空时填写",
                    )
                )
                continue

            resolver = getattr(self._departments, "resolve_or_create_by_names", None)
            if not callable(resolver):
                resolver = getattr(self._departments, "resolve_by_names")
            resolved = resolver(
                primary_name=primary_department_name,
                secondary_name=secondary_department_name,
                tertiary_name=tertiary_department_name,
                active_only=True,
                allow_legacy_two_level=True,
            )
            if not resolved.get("success"):
                failed_details.append(
                    self._failed_detail(
                        line_no=line_no,
                        employee_no=employee_no,
                        full_name=full_name,
                        reason=str(resolved.get("error") or "部门解析失败"),
                        code=str(resolved.get("code") or "VALIDATION_ERROR"),
                    )
                )
                continue
            department_data = resolved.get("data") if isinstance(resolved.get("data"), dict) else {}
            row_created_departments = (
                department_data.get("created_departments")
                if isinstance(department_data.get("created_departments"), dict)
                else {}
            )
            for key in ("primary", "secondary", "tertiary"):
                created_departments[key] += int(row_created_departments.get(key) or 0)
            created_departments["total"] = sum(created_departments[key] for key in ("primary", "secondary", "tertiary"))

            validated_rows.append(
                {
                    "line_no": line_no,
                    "employee_no": employee_no,
                    "full_name": full_name,
                    "verification_code": verification_code,
                    "status": status,
                    "remarks": remarks,
                    "primary_department_id": department_data.get("primary_department_id"),
                    "secondary_department_id": department_data.get("secondary_department_id"),
                    "tertiary_department_id": department_data.get("tertiary_department_id"),
                }
            )

        return validated_rows, failed_details, created_departments

    @staticmethod
    def _failed_detail(
        *,
        line_no: int,
        employee_no: str,
        full_name: str,
        reason: str,
        code: str = "VALIDATION_ERROR",
    ) -> dict[str, Any]:
        return {
            "row": line_no,
            "employee_no": employee_no,
            "full_name": full_name,
            "status": "failed",
            "reason": reason,
            "code": code,
        }

    def import_personnel(self, *, file_bytes: bytes, filename: str) -> dict[str, Any]:
        filename = self._clean_text(filename)
        if not filename:
            return {"success": False, "error": "文件名为空", "code": "FILENAME_EMPTY"}

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in {"xlsx", "csv"}:
            return {"success": False, "error": "不支持的文件格式，只支持.xlsx和.csv", "code": "INVALID_FILE_TYPE"}

        try:
            rows = load_rows(file_bytes=file_bytes, ext=ext)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "code": "VALIDATION_ERROR"}
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": "批量导入失败", "code": "IMPORT_ERROR"}

        columns, missing = resolve_column_aliases(
            rows["columns"],
            REQUIRED_COLUMN_ALIASES,
            OPTIONAL_COLUMN_ALIASES,
        )
        if missing:
            return {"success": False, "error": f"缺少必要列: {', '.join(missing)}", "code": "VALIDATION_ERROR"}

        employee_no_col = columns["employee_no"]
        full_name_col = columns["full_name"]
        verification_code_col = columns["verification_code"]
        status_col = columns.get("status")
        primary_department_name_col = columns["primary_department_name"]
        secondary_department_name_col = columns.get("secondary_department_name") or ""
        tertiary_department_name_col = columns.get("tertiary_department_name") or ""
        remarks_col = columns.get("remarks")

        seen_rows: dict[str, list[int]] = {}
        for index, row in enumerate(rows["items"]):
            line_no = index + 2
            employee_no = self._clean_text(row.get(employee_no_col))
            if employee_no:
                seen_rows.setdefault(employee_no, []).append(line_no)
        duplicates = {employee_no: line_nos for employee_no, line_nos in seen_rows.items() if len(line_nos) > 1}
        validated_rows, failed_details, created_departments = self._validate_rows(
            items=rows["items"],
            duplicate_employee_nos=duplicates,
            employee_no_col=employee_no_col,
            full_name_col=full_name_col,
            verification_code_col=verification_code_col,
            status_col=status_col,
            primary_department_name_col=primary_department_name_col,
            secondary_department_name_col=secondary_department_name_col,
            tertiary_department_name_col=tertiary_department_name_col,
            remarks_col=remarks_col,
        )

        created = 0
        updated = 0
        details: list[dict[str, Any]] = []
        skipped = 0

        prepared_rows = [
            {
                **row,
                "verification_code_hash": self._service.hash_verification_code(str(row["verification_code"])),
            }
            for row in validated_rows
        ]
        if prepared_rows:
            try:
                try:
                    write_result = self._repository.import_personnel_rows(rows=prepared_rows, sync_bound_users=True)
                except TypeError as exc:
                    if "sync_bound_users" not in str(exc):
                        raise
                    write_result = self._repository.import_personnel_rows(rows=prepared_rows)
                    self._sync_imported_personnel_departments(rows=prepared_rows)
                created = int(write_result.get("created") or 0)
                updated = int(write_result.get("updated") or 0)
                skipped = int(write_result.get("skipped") or 0)
                details = list(write_result.get("details") or [])
            except Exception as exc:
                if _is_db_unavailable_error(exc):
                    return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
                return {"success": False, "error": "批量导入失败", "code": "IMPORT_ERROR"}

        logger.info(
            "personnel_import_applied",
            extra={"event": "personnel_import_applied", "created_count": created, "updated_count": updated},
        )
        write_failed_count = sum(1 for item in details if item.get("status") == "failed")
        failed_count = len(failed_details) + write_failed_count
        return {
            "success": True,
            "message": "人员导入完成",
            "data": {
                "summary": {
                    "total": created + updated + skipped + failed_count,
                    "created": created,
                    "updated": updated,
                    "skipped": skipped,
                    "failed": failed_count,
                    "created_departments_total": int(created_departments.get("total") or 0),
                    "created_primary_departments": int(created_departments.get("primary") or 0),
                    "created_secondary_departments": int(created_departments.get("secondary") or 0),
                    "created_tertiary_departments": int(created_departments.get("tertiary") or 0),
                },
                "details": sorted([*details, *failed_details], key=lambda item: int(item.get("row") or 0)),
            },
        }

    def _sync_imported_personnel_departments(self, *, rows: list[dict[str, Any]]) -> None:
        sync = getattr(self._users, "sync_departments_for_personnel", None)
        get_by_employee_no = getattr(self._repository, "get_by_employee_no", None)
        if not callable(sync) or not callable(get_by_employee_no):
            return

        synced_personnel_ids: set[int] = set()
        for row in rows:
            record = get_by_employee_no(str(row.get("employee_no") or ""))
            if not isinstance(record, dict):
                continue
            personnel_id = record.get("id")
            if personnel_id is None:
                continue
            normalized_personnel_id = int(personnel_id)
            if normalized_personnel_id in synced_personnel_ids:
                continue
            synced_personnel_ids.add(normalized_personnel_id)
            sync(
                personnel_id=normalized_personnel_id,
                primary_department_id=row.get("primary_department_id"),
                secondary_department_id=row.get("secondary_department_id"),
                tertiary_department_id=row.get("tertiary_department_id"),
            )

    def template_response(self, *, fmt: str) -> Response | dict[str, Any]:
        fmt = self._clean_text(fmt or "xlsx").lower()
        if fmt not in {"xlsx", "csv"}:
            return {"success": False, "error": "不支持的格式，只支持xlsx和csv", "code": "INVALID_FORMAT"}

        headers = TEMPLATE_COLUMNS
        rows = [
            ["T2024001", "张三", "计算机学院", "", "", "ABC123", "一级部门必填，二级和三级部门可按实际管理层级留空"],
            ["T2024002", "李四", "化学学院", "材料系", "", "XYZ789", "绑定到二级部门"],
            ["T2024003", "王五", "计算机学院", "软件工程系", "智能软件实验室", "LMN456", "绑定到三级部门"],
        ]

        if fmt == "csv":
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerow(headers)
            writer.writerows(rows)
            return Response(
                content=buffer.getvalue().encode("utf-8-sig"),
                media_type="text/csv",
                headers={"Content-Disposition": 'attachment; filename="personnel_import_template.csv"'},
            )

        return Response(
            content=build_xlsx(headers=headers, rows=rows, sheet_name="人员导入"),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="personnel_import_template.xlsx"'},
        )


personnel_import_service = PersonnelImportService()
