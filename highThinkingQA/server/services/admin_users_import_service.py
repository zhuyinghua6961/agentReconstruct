"""Admin batch import helpers."""

# Deprecated: retained only for the retired highThinkingQA admin HTTP surface.


from __future__ import annotations

import csv
import io
import zipfile
from typing import Any
from xml.etree import ElementTree as ET

from fastapi.responses import Response

from server.services.admin_users_service import admin_users_service

XML_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "pkg_rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}


class AdminUsersImportService:
    def import_users(self, *, file_bytes: bytes, filename: str, actor_user_id: int) -> dict[str, Any]:
        _ = actor_user_id
        filename = admin_users_service.clean_text(filename)
        if not filename:
            return {"success": False, "error": "文件名为空", "code": "FILENAME_EMPTY"}
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in {"xlsx", "csv"}:
            return {"success": False, "error": "不支持的文件格式，只支持.xlsx和.csv", "code": "INVALID_FILE_TYPE"}

        try:
            rows = self._load_rows(file_bytes=file_bytes, ext=ext)
        except ValueError as exc:
            return {"success": False, "error": str(exc), "code": "VALIDATION_ERROR"}
        except Exception:
            return {"success": False, "error": "批量导入失败", "code": "IMPORT_ERROR"}

        normalized = {str(col).strip().lower(): col for col in rows["columns"]}
        if "username" not in normalized or "password" not in normalized:
            return {"success": False, "error": "缺少必要列，至少需要 username/password", "code": "VALIDATION_ERROR"}

        username_col = normalized["username"]
        password_col = normalized["password"]
        user_type_col = normalized.get("user_type")

        details: list[dict[str, Any]] = []
        success_count = 0
        failed_count = 0
        skipped_count = 0

        for index, row in enumerate(rows["items"]):
            line_no = index + 2
            username = admin_users_service.clean_text(row.get(username_col))
            password = str(row.get(password_col) or "")
            user_type = admin_users_service.clean_text(row.get(user_type_col) if user_type_col else "common").lower() or "common"

            if not username:
                failed_count += 1
                details.append({"row": line_no, "username": "", "status": "failed", "reason": "用户名为空"})
                continue
            if username.lower().startswith("admin"):
                failed_count += 1
                details.append({"row": line_no, "username": username, "status": "failed", "reason": "不能以 admin 开头"})
                continue
            if len(username) < 3 or len(username) > 50:
                failed_count += 1
                details.append({"row": line_no, "username": username, "status": "failed", "reason": "用户名长度需在3-50之间"})
                continue
            if len(password) < 6:
                failed_count += 1
                details.append({"row": line_no, "username": username, "status": "failed", "reason": "密码长度不能少于6位"})
                continue
            if user_type not in {"common", "super"}:
                failed_count += 1
                details.append({"row": line_no, "username": username, "status": "failed", "reason": "user_type必须是common或super"})
                continue
            if admin_users_service.users.get_by_username(username):
                skipped_count += 1
                details.append({"row": line_no, "username": username, "status": "skipped", "reason": "用户名已存在"})
                continue

            password_hash = admin_users_service.hash_password(password)
            created_id = admin_users_service.users.create_user(
                username=username,
                password_hash=password_hash,
                role="user",
                user_type=2 if user_type == "super" else 3,
                is_first_login=True,
                must_set_security_questions=True,
            )
            admin_users_service.users.add_password_history(user_id=created_id, password_hash=password_hash)
            admin_users_service.users.trim_password_history(user_id=created_id, keep_limit=3)
            success_count += 1
            details.append({"row": line_no, "username": username, "status": "success", "user_id": created_id})

        total = success_count + failed_count + skipped_count
        return {
            "success": True,
            "message": "批量导入完成",
            "data": {
                "summary": {"total": total, "success": success_count, "failed": failed_count, "skipped": skipped_count},
                "details": details,
                "duration": 0,
            },
        }

    def template_response(self, *, fmt: str) -> Response | dict[str, Any]:
        fmt = admin_users_service.clean_text(fmt or "xlsx").lower()
        if fmt not in {"xlsx", "csv"}:
            return {"success": False, "error": "不支持的格式，只支持xlsx和csv", "code": "INVALID_FORMAT"}

        headers = ["username", "password", "user_type"]
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
            content=self._build_xlsx(headers=headers, rows=rows, sheet_name="用户导入"),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="user_import_template.xlsx"'},
        )

    def _load_rows(self, *, file_bytes: bytes, ext: str) -> dict[str, Any]:
        if ext == "csv":
            return self._load_csv_rows(file_bytes)
        return self._load_xlsx_rows(file_bytes)

    def _load_csv_rows(self, file_bytes: bytes) -> dict[str, Any]:
        text = self._decode_csv_bytes(file_bytes)
        reader = csv.reader(io.StringIO(text))
        rows = [list(row) for row in reader]
        return self._rows_to_items(rows)

    def _decode_csv_bytes(self, file_bytes: bytes) -> str:
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return file_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError("CSV文件编码无法识别")

    def _load_xlsx_rows(self, file_bytes: bytes) -> dict[str, Any]:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as archive:
            shared_strings = self._read_shared_strings(archive)
            sheet_name = self._resolve_first_sheet_path(archive)
            sheet_xml = ET.fromstring(archive.read(sheet_name))

        rows: list[list[str]] = []
        for row_node in sheet_xml.findall(".//main:sheetData/main:row", XML_NS):
            row_values: dict[int, str] = {}
            max_index = -1
            for cell in row_node.findall("main:c", XML_NS):
                ref = str(cell.get("r") or "")
                col_index = self._column_index_from_ref(ref)
                value = self._read_cell_value(cell=cell, shared_strings=shared_strings)
                row_values[col_index] = value
                max_index = max(max_index, col_index)
            if max_index < 0:
                continue
            row = [row_values.get(index, "") for index in range(max_index + 1)]
            if any(str(item).strip() for item in row):
                rows.append(row)
        return self._rows_to_items(rows)

    def _resolve_first_sheet_path(self, archive: zipfile.ZipFile) -> str:
        workbook_xml = ET.fromstring(archive.read("xl/workbook.xml"))
        first_sheet = workbook_xml.find(".//main:sheets/main:sheet", XML_NS)
        if first_sheet is None:
            raise ValueError("Excel文件缺少工作表")
        rel_id = first_sheet.get(f"{{{XML_NS['rel']}}}id")
        rels_xml = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        for relation in rels_xml.findall("pkg_rel:Relationship", XML_NS):
            if relation.get("Id") == rel_id:
                target = str(relation.get("Target") or "").lstrip("/")
                return target if target.startswith("xl/") else f"xl/{target}"
        raise ValueError("Excel文件工作表关系缺失")

    def _read_shared_strings(self, archive: zipfile.ZipFile) -> list[str]:
        if "xl/sharedStrings.xml" not in archive.namelist():
            return []
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        values: list[str] = []
        for node in root.findall("main:si", XML_NS):
            text_parts = [text.text or "" for text in node.findall(".//main:t", XML_NS)]
            values.append("".join(text_parts))
        return values

    def _read_cell_value(self, *, cell: ET.Element, shared_strings: list[str]) -> str:
        cell_type = str(cell.get("t") or "")
        if cell_type == "inlineStr":
            parts = [node.text or "" for node in cell.findall(".//main:t", XML_NS)]
            return "".join(parts)
        value_node = cell.find("main:v", XML_NS)
        if value_node is None or value_node.text is None:
            return ""
        raw_value = value_node.text
        if cell_type == "s":
            index = int(raw_value or 0)
            return shared_strings[index] if 0 <= index < len(shared_strings) else ""
        return str(raw_value)

    def _rows_to_items(self, rows: list[list[str]]) -> dict[str, Any]:
        if not rows:
            return {"columns": [], "items": []}
        columns = [str(item or "").strip() for item in rows[0]]
        items: list[dict[str, str]] = []
        for row in rows[1:]:
            padded = list(row) + [""] * max(0, len(columns) - len(row))
            items.append({columns[index]: str(padded[index] or "") for index in range(len(columns))})
        return {"columns": columns, "items": items}

    def _column_index_from_ref(self, ref: str) -> int:
        letters = "".join(ch for ch in ref if ch.isalpha()).upper()
        if not letters:
            return 0
        index = 0
        for char in letters:
            index = index * 26 + (ord(char) - ord("A") + 1)
        return max(0, index - 1)

    def _build_xlsx(self, *, headers: list[str], rows: list[list[str]], sheet_name: str) -> bytes:
        def _column_letters(index: int) -> str:
            value = index + 1
            letters: list[str] = []
            while value > 0:
                value, remainder = divmod(value - 1, 26)
                letters.append(chr(ord("A") + remainder))
            return "".join(reversed(letters))

        def _inline_cell(*, value: str, ref: str) -> str:
            escaped = str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            return f'<c r="{ref}" t="inlineStr"><is><t>{escaped}</t></is></c>'

        sheet_rows: list[str] = []
        all_rows = [headers] + rows
        for row_index, row in enumerate(all_rows, start=1):
            cells = "".join(_inline_cell(value=value, ref=f"{_column_letters(column_index)}{row_index}") for column_index, value in enumerate(row))
            sheet_rows.append(f'<row r="{row_index}">{cells}</row>')

        workbook_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/></sheets>'
            "</workbook>"
        ).replace("{sheet_name}", sheet_name)
        worksheet_xml = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f"<sheetData>{''.join(sheet_rows)}</sheetData>"
            "</worksheet>"
        )
        workbook_rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            "</Relationships>"
        )
        root_rels = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>"
        )
        content_types = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            "</Types>"
        )
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", content_types)
            archive.writestr("_rels/.rels", root_rels)
            archive.writestr("xl/workbook.xml", workbook_xml)
            archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
            archive.writestr("xl/worksheets/sheet1.xml", worksheet_xml)
        return output.getvalue()


admin_users_import_service = AdminUsersImportService()
