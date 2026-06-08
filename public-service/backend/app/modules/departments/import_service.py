from __future__ import annotations

import csv
import io
import time
from typing import Any

from fastapi.responses import Response

from app.core.import_columns import resolve_column_aliases
from app.core.spreadsheet import build_xlsx, load_rows
from app.modules.departments.repository import DepartmentRepository
from app.modules.departments.service import _is_db_unavailable_error


REQUIRED_COLUMN_ALIASES = {
    "primary_department_name": ("一级部门名称", "primary_department_name"),
    "primary_status": ("一级状态", "primary_status"),
    "secondary_department_name": ("二级部门名称", "secondary_department_name"),
    "secondary_status": ("二级状态", "secondary_status"),
}
OPTIONAL_COLUMN_ALIASES = {
    "tertiary_department_name": ("三级部门名称", "tertiary_department_name"),
    "tertiary_status": ("三级状态", "tertiary_status"),
}
TEMPLATE_HEADERS = ["一级部门名称", "一级状态", "二级部门名称", "二级状态", "三级部门名称", "三级状态"]
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

        columns, missing = resolve_column_aliases(
            rows["columns"],
            REQUIRED_COLUMN_ALIASES,
            OPTIONAL_COLUMN_ALIASES,
        )
        if missing:
            return {
                "success": False,
                "error": f"缺少必要列: {', '.join(missing)}",
                "code": "VALIDATION_ERROR",
            }

        primary_name_col = columns["primary_department_name"]
        primary_status_col = columns["primary_status"]
        secondary_name_col = columns["secondary_department_name"]
        secondary_status_col = columns["secondary_status"]
        tertiary_name_col = columns.get("tertiary_department_name")
        tertiary_status_col = columns.get("tertiary_status")

        details: list[dict[str, Any]] = []
        success_count = 0
        failed_count = 0
        skipped_count = 0
        primary_status_seen: dict[str, str] = {}
        pair_status_seen: dict[tuple[str, str], tuple[str, str]] = {}
        row_status_seen: dict[tuple[str, str, str], tuple[str, str, str]] = {}

        for index, row in enumerate(rows["items"]):
            try:
                line_no = index + 2
                primary_name = self._clean_text(row.get(primary_name_col))
                primary_status = self._clean_text(row.get(primary_status_col)).lower()
                secondary_name = self._clean_text(row.get(secondary_name_col))
                secondary_status = self._clean_text(row.get(secondary_status_col)).lower()
                tertiary_name = self._clean_text(row.get(tertiary_name_col)) if tertiary_name_col else ""
                tertiary_status = self._clean_text(row.get(tertiary_status_col)).lower() if tertiary_status_col else ""

                detail = {
                    "row": line_no,
                    "primary_department_name": primary_name,
                    "primary_status": primary_status,
                    "secondary_department_name": secondary_name,
                    "secondary_status": secondary_status,
                    "tertiary_department_name": tertiary_name,
                    "tertiary_status": tertiary_status,
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
                if bool(tertiary_name) ^ bool(tertiary_status):
                    failed_count += 1
                    details.append({**detail, "status": "failed", "reason": "三级部门名称和状态必须同时填写"})
                    continue
                if tertiary_status and tertiary_status not in VALID_STATUSES:
                    failed_count += 1
                    details.append({**detail, "status": "failed", "reason": "三级部门状态必须是 active 或 disabled"})
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
                elif previous_pair_status != pair_status_key:
                    failed_count += 1
                    details.append({**detail, "status": "failed", "reason": "同一部门组合在文件中存在冲突"})
                    continue

                row_key = (primary_name, secondary_name, tertiary_name)
                row_status_key = (primary_status, secondary_status, tertiary_status)
                previous_row_status = row_status_seen.get(row_key)
                if previous_row_status is None:
                    row_status_seen[row_key] = row_status_key
                elif previous_row_status == row_status_key:
                    skipped_count += 1
                    details.append({**detail, "status": "skipped", "reason": "导入文件中存在重复行"})
                    continue
                else:
                    failed_count += 1
                    details.append({**detail, "status": "failed", "reason": "同一部门组合在文件中存在冲突"})
                    continue

                skip_row, primary_id, secondary_id, tertiary_id = self._is_existing_unchanged_path(
                    primary_name=primary_name,
                    primary_status=primary_status,
                    secondary_name=secondary_name,
                    secondary_status=secondary_status,
                    tertiary_name=tertiary_name,
                    tertiary_status=tertiary_status,
                )
                if skip_row:
                    skipped_count += 1
                    details.append(
                        {
                            **detail,
                            "status": "skipped",
                            "reason": "部门已存在且未变化",
                            "primary_department_id": primary_id,
                            "secondary_department_id": secondary_id,
                            "tertiary_department_id": tertiary_id,
                        }
                    )
                    continue

                primary_id = self._upsert_primary(name=primary_name, status=primary_status)
                secondary_id = self._upsert_secondary(
                    primary_department_id=primary_id,
                    name=secondary_name,
                    status=secondary_status,
                )
                tertiary_id = None
                if tertiary_name and tertiary_status:
                    tertiary_id = self._upsert_tertiary(
                        secondary_department_id=secondary_id,
                        name=tertiary_name,
                        status=tertiary_status,
                    )
                success_count += 1
                details.append(
                    {
                        **detail,
                        "status": "success",
                        "primary_department_id": primary_id,
                        "secondary_department_id": secondary_id,
                        "tertiary_department_id": tertiary_id,
                    }
                )
            except Exception as exc:
                if _is_db_unavailable_error(exc):
                    return {"success": False, "error": str(exc), "code": "DB_UNAVAILABLE"}
                failed_count += 1
                details.append(
                    {
                        "row": index + 2,
                        "primary_department_name": self._clean_text(row.get(primary_name_col)),
                        "primary_status": self._clean_text(row.get(primary_status_col)).lower(),
                        "secondary_department_name": self._clean_text(row.get(secondary_name_col)),
                        "secondary_status": self._clean_text(row.get(secondary_status_col)).lower(),
                        "tertiary_department_name": self._clean_text(row.get(tertiary_name_col)) if tertiary_name_col else "",
                        "tertiary_status": self._clean_text(row.get(tertiary_status_col)).lower() if tertiary_status_col else "",
                        "status": "failed",
                        "reason": str(exc) or "导入失败",
                    }
                )
                continue

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

        headers = TEMPLATE_HEADERS
        rows = [
            ["计算机学院", "active", "软件工程系", "active", "人工智能实验室", "active"],
            ["计算机学院", "active", "人工智能系", "disabled", "", ""],
            ["化学学院", "disabled", "材料系", "active", "高分子实验室", "disabled"],
        ]

        if fmt == "csv":
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerow(headers)
            writer.writerows(rows)
            return Response(
                content=buffer.getvalue().encode("utf-8-sig"),
                media_type="text/csv",
                headers={"Content-Disposition": 'attachment; filename="department_import_template.csv"'},
            )

        return Response(
            content=build_xlsx(headers=headers, rows=rows, sheet_name="部门导入"),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="department_import_template.xlsx"'},
        )

    def _is_existing_unchanged_path(
        self,
        *,
        primary_name: str,
        primary_status: str,
        secondary_name: str,
        secondary_status: str,
        tertiary_name: str,
        tertiary_status: str,
    ) -> tuple[bool, int | None, int | None, int | None]:
        primary = self._repository.get_primary_by_name(primary_name)
        if not primary or self._clean_text(primary.get("status")).lower() != primary_status:
            return False, None, None, None

        primary_id = int(primary["id"])
        secondary = self._repository.get_secondary_by_name(
            primary_department_id=primary_id,
            name=secondary_name,
        )
        if not secondary or self._clean_text(secondary.get("status")).lower() != secondary_status:
            return False, primary_id, None, None

        secondary_id = int(secondary["id"])
        if not tertiary_name and not tertiary_status:
            return True, primary_id, secondary_id, None

        tertiary = self._repository.get_tertiary_by_name(
            secondary_department_id=secondary_id,
            name=tertiary_name,
        )
        if not tertiary or self._clean_text(tertiary.get("status")).lower() != tertiary_status:
            return False, primary_id, secondary_id, None

        return True, primary_id, secondary_id, int(tertiary["id"])

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

    def _upsert_tertiary(self, *, secondary_department_id: int, name: str, status: str) -> int:
        tertiary = self._repository.get_tertiary_by_name(secondary_department_id=secondary_department_id, name=name)
        if tertiary:
            tertiary_id = int(tertiary["id"])
            if self._clean_text(tertiary.get("status")).lower() != status:
                self._repository.update_tertiary_status(tertiary_id=tertiary_id, status=status)
            return tertiary_id

        tertiary_id = int(self._repository.create_tertiary(secondary_department_id=secondary_department_id, name=name))
        if tertiary_id <= 0:
            raise RuntimeError("三级部门创建失败")
        if status != "active":
            self._repository.update_tertiary_status(tertiary_id=tertiary_id, status=status)
        return tertiary_id


department_import_service = DepartmentImportService()
