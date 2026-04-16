from __future__ import annotations

import csv
import io
import time
from typing import Any

from fastapi.responses import Response

from app.core.spreadsheet import build_xlsx, load_rows
from app.modules.departments.repository import DepartmentRepository
from app.modules.departments.service import _is_db_unavailable_error


REQUIRED_COLUMNS = [
    "primary_department_name",
    "primary_status",
    "secondary_department_name",
    "secondary_status",
]
VALID_STATUSES = {"active", "disabled"}


class DepartmentImportService:
    def __init__(self, *, repository: DepartmentRepository | None = None) -> None:
        self._repository = repository or DepartmentRepository()

    @staticmethod
    def _clean_text(value: object) -> str:
        return str(value or "").strip()

    def import_departments(self, *, file_bytes: bytes, filename: str) -> dict[str, Any]:
        filename = self._clean_text(filename)
        if not filename:
            return {"success": False, "error": "文件名为空", "code": "FILENAME_EMPTY"}

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in {"xlsx", "csv"}:
            return {"success": False, "error": "不支持的文件格式，只支持.xlsx和.csv", "code": "INVALID_FILE_TYPE"}

        started = time.monotonic()
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
            return {
                "success": False,
                "error": f"缺少必要列: {', '.join(missing)}",
                "code": "VALIDATION_ERROR",
            }

        primary_name_col = normalized["primary_department_name"]
        primary_status_col = normalized["primary_status"]
        secondary_name_col = normalized["secondary_department_name"]
        secondary_status_col = normalized["secondary_status"]

        details: list[dict[str, Any]] = []
        success_count = 0
        failed_count = 0
        skipped_count = 0
        primary_status_seen: dict[str, str] = {}
        pair_status_seen: dict[tuple[str, str], tuple[str, str]] = {}

        try:
            for index, row in enumerate(rows["items"]):
                line_no = index + 2
                primary_name = self._clean_text(row.get(primary_name_col))
                primary_status = self._clean_text(row.get(primary_status_col)).lower()
                secondary_name = self._clean_text(row.get(secondary_name_col))
                secondary_status = self._clean_text(row.get(secondary_status_col)).lower()

                detail = {
                    "row": line_no,
                    "primary_department_name": primary_name,
                    "primary_status": primary_status,
                    "secondary_department_name": secondary_name,
                    "secondary_status": secondary_status,
                }

                if not primary_name or not secondary_name or not primary_status or not secondary_status:
                    failed_count += 1
                    details.append({**detail, "status": "failed", "reason": "四个字段都必须填写"})
                    continue
                if primary_status not in VALID_STATUSES:
                    failed_count += 1
                    details.append({**detail, "status": "failed", "reason": "一级部门状态必须是 active 或 disabled"})
                    continue
                if secondary_status not in VALID_STATUSES:
                    failed_count += 1
                    details.append({**detail, "status": "failed", "reason": "二级部门状态必须是 active 或 disabled"})
                    continue

                previous_primary_status = primary_status_seen.get(primary_name)
                if previous_primary_status is None:
                    primary_status_seen[primary_name] = primary_status
                elif previous_primary_status != primary_status:
                    failed_count += 1
                    details.append({**detail, "status": "failed", "reason": "同一一级部门状态不一致"})
                    continue

                pair_key = (primary_name, secondary_name)
                pair_status_key = (primary_status, secondary_status)
                previous_pair_status = pair_status_seen.get(pair_key)
                if previous_pair_status is None:
                    pair_status_seen[pair_key] = pair_status_key
                elif previous_pair_status == pair_status_key:
                    skipped_count += 1
                    details.append({**detail, "status": "skipped", "reason": "导入文件中存在重复行"})
                    continue
                else:
                    failed_count += 1
                    details.append({**detail, "status": "failed", "reason": "同一部门组合在文件中存在冲突"})
                    continue

                primary_id = self._upsert_primary(name=primary_name, status=primary_status)
                secondary_id = self._upsert_secondary(
                    primary_department_id=primary_id,
                    name=secondary_name,
                    status=secondary_status,
                )
                success_count += 1
                details.append(
                    {
                        **detail,
                        "status": "success",
                        "primary_department_id": primary_id,
                        "secondary_department_id": secondary_id,
                    }
                )
        except Exception as exc:
            if _is_db_unavailable_error(exc):
                return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
            return {"success": False, "error": "批量导入失败", "code": "IMPORT_ERROR"}

        total = success_count + failed_count + skipped_count
        return {
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
                "duration": round(time.monotonic() - started, 2),
            },
        }

    def template_response(self, *, fmt: str) -> Response | dict[str, Any]:
        fmt = self._clean_text(fmt or "xlsx").lower()
        if fmt not in {"xlsx", "csv"}:
            return {"success": False, "error": "不支持的格式，只支持xlsx和csv", "code": "INVALID_FORMAT"}

        rows = [
            ["计算机学院", "active", "软件工程系", "active"],
            ["计算机学院", "active", "人工智能系", "disabled"],
            ["化学学院", "disabled", "材料系", "active"],
        ]

        if fmt == "csv":
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerow(REQUIRED_COLUMNS)
            writer.writerows(rows)
            return Response(
                content=buffer.getvalue().encode("utf-8-sig"),
                media_type="text/csv",
                headers={"Content-Disposition": 'attachment; filename="department_import_template.csv"'},
            )

        return Response(
            content=build_xlsx(headers=REQUIRED_COLUMNS, rows=rows, sheet_name="部门导入"),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="department_import_template.xlsx"'},
        )

    def _upsert_primary(self, *, name: str, status: str) -> int:
        primary = self._repository.get_primary_by_name(name)
        if primary:
            primary_id = int(primary["id"])
            if self._clean_text(primary.get("status")).lower() != status:
                self._repository.update_primary_status(primary_id=primary_id, status=status)
            return primary_id

        primary_id = int(self._repository.create_primary(name=name))
        if status != "active":
            self._repository.update_primary_status(primary_id=primary_id, status=status)
        return primary_id

    def _upsert_secondary(self, *, primary_department_id: int, name: str, status: str) -> int:
        secondary = self._repository.get_secondary_by_name(primary_department_id=primary_department_id, name=name)
        if secondary:
            secondary_id = int(secondary["id"])
            if self._clean_text(secondary.get("status")).lower() != status:
                self._repository.update_secondary_status(secondary_id=secondary_id, status=status)
            return secondary_id

        secondary_id = int(self._repository.create_secondary(primary_department_id=primary_department_id, name=name))
        if status != "active":
            self._repository.update_secondary_status(secondary_id=secondary_id, status=status)
        return secondary_id


department_import_service = DepartmentImportService()
