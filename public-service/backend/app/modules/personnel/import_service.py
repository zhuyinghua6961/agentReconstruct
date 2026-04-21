from __future__ import annotations

import csv
import io
import logging
from typing import Any

from fastapi.responses import Response

from app.core.spreadsheet import build_xlsx, load_rows
from app.modules.auth.repository import AuthRepository
from app.modules.departments.service import department_service as shared_department_service
from app.modules.personnel.repository import PersonnelRepository, REMARKS_UNSET
from app.modules.personnel.service import PersonnelService, _is_db_unavailable_error


logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = [
    "employee_no",
    "full_name",
    "verification_code",
    "status",
    "primary_department_name",
    "secondary_department_name",
    "tertiary_department_name",
]
OPTIONAL_COLUMNS = ["remarks"]
VALID_STATUSES = {"active", "disabled"}


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

    def _validate_rows(
        self,
        *,
        items: list[dict[str, Any]],
        employee_no_col: str,
        full_name_col: str,
        verification_code_col: str,
        status_col: str,
        primary_department_name_col: str,
        secondary_department_name_col: str,
        tertiary_department_name_col: str,
        remarks_col: str | None,
    ) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
        validated_rows: list[dict[str, Any]] = []
        for index, row in enumerate(items):
            line_no = index + 2
            employee_no = self._clean_text(row.get(employee_no_col))
            full_name = self._clean_text(row.get(full_name_col))
            verification_code = self._clean_text(row.get(verification_code_col))
            status = self._clean_text(row.get(status_col)).lower()
            primary_department_name = self._clean_text(row.get(primary_department_name_col))
            secondary_department_name = self._clean_text(row.get(secondary_department_name_col))
            tertiary_department_name = self._clean_text(row.get(tertiary_department_name_col))
            remarks = self._clean_text(row.get(remarks_col)) if remarks_col else REMARKS_UNSET

            if not employee_no:
                return None, {"success": False, "error": f"第 {line_no} 行工号为空", "code": "VALIDATION_ERROR"}
            if not full_name:
                return None, {"success": False, "error": f"第 {line_no} 行姓名为空", "code": "VALIDATION_ERROR"}
            if not verification_code:
                return None, {"success": False, "error": f"第 {line_no} 行校验码为空", "code": "VALIDATION_ERROR"}
            if status not in VALID_STATUSES:
                return None, {
                    "success": False,
                    "error": f"第 {line_no} 行状态必须是 active 或 disabled",
                    "code": "VALIDATION_ERROR",
                }
            if not primary_department_name or not secondary_department_name or not tertiary_department_name:
                return None, {
                    "success": False,
                    "error": f"第 {line_no} 行一级、二级和三级部门名称不能为空",
                    "code": "VALIDATION_ERROR",
                }

            resolved = self._departments.resolve_by_names(
                primary_name=primary_department_name,
                secondary_name=secondary_department_name,
                tertiary_name=tertiary_department_name,
                active_only=True,
                allow_legacy_two_level=False,
            )
            if not resolved.get("success"):
                return None, {
                    "success": False,
                    "error": f"第 {line_no} 行{resolved.get('error') or '部门解析失败'}",
                    "code": str(resolved.get("code") or "VALIDATION_ERROR"),
                }
            department_data = resolved.get("data") if isinstance(resolved.get("data"), dict) else {}

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

        return validated_rows, None

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

        normalized = {str(col).strip().lower(): col for col in rows["columns"]}
        missing = [column for column in REQUIRED_COLUMNS if column not in normalized]
        if missing:
            return {"success": False, "error": f"缺少必要列: {', '.join(missing)}", "code": "VALIDATION_ERROR"}

        employee_no_col = normalized["employee_no"]
        full_name_col = normalized["full_name"]
        verification_code_col = normalized["verification_code"]
        status_col = normalized["status"]
        primary_department_name_col = normalized["primary_department_name"]
        secondary_department_name_col = normalized["secondary_department_name"]
        tertiary_department_name_col = normalized["tertiary_department_name"]
        remarks_col = normalized.get("remarks")

        seen_rows: dict[str, list[int]] = {}
        for index, row in enumerate(rows["items"]):
            line_no = index + 2
            employee_no = self._clean_text(row.get(employee_no_col))
            if employee_no:
                seen_rows.setdefault(employee_no, []).append(line_no)
        duplicates = {employee_no: line_nos for employee_no, line_nos in seen_rows.items() if len(line_nos) > 1}
        if duplicates:
            first_employee_no = next(iter(duplicates))
            line_nos = ",".join(str(line_no) for line_no in duplicates[first_employee_no])
            return {
                "success": False,
                "error": f"导入文件中存在重复工号: {first_employee_no}（行号: {line_nos}）",
                "code": "VALIDATION_ERROR",
            }

        validated_rows, validation_error = self._validate_rows(
            items=rows["items"],
            employee_no_col=employee_no_col,
            full_name_col=full_name_col,
            verification_code_col=verification_code_col,
            status_col=status_col,
            primary_department_name_col=primary_department_name_col,
            secondary_department_name_col=secondary_department_name_col,
            tertiary_department_name_col=tertiary_department_name_col,
            remarks_col=remarks_col,
        )
        if validation_error:
            return validation_error

        created = 0
        updated = 0
        details: list[dict[str, Any]] = []

        try:
            prepared_rows = [
                {
                    **row,
                    "verification_code_hash": self._service.hash_verification_code(str(row["verification_code"])),
                }
                for row in (validated_rows or [])
            ]
            try:
                write_result = self._repository.import_personnel_rows(rows=prepared_rows, sync_bound_users=True)
            except TypeError as exc:
                if "sync_bound_users" not in str(exc):
                    raise
                write_result = self._repository.import_personnel_rows(rows=prepared_rows)
                self._sync_imported_personnel_departments(rows=prepared_rows)
            created = int(write_result.get("created") or 0)
            updated = int(write_result.get("updated") or 0)
            details = list(write_result.get("details") or [])
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": "批量导入失败", "code": "IMPORT_ERROR"}

        logger.info(
            "personnel_import_applied",
            extra={"event": "personnel_import_applied", "created_count": created, "updated_count": updated},
        )
        return {
            "success": True,
            "message": "人员导入完成",
            "data": {
                "summary": {
                    "total": created + updated,
                    "created": created,
                    "updated": updated,
                    "failed": 0,
                },
                "details": details,
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

        headers = [*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS]
        rows = [
            ["T2024001", "张三", "ABC123", "active", "计算机学院", "软件工程系", "智能软件实验室", "化学学院"],
            ["T2024002", "李四", "XYZ789", "disabled", "化学学院", "材料系", "高分子实验室", "材料系"],
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
