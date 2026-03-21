from __future__ import annotations

import re
from typing import Any

from app.modules.file_context.models import (
    ExecutionFilePayload,
    FileSelectionCandidate,
    NormalizedFileRow,
    OrdinalRefs,
    UsedFilePayload,
)


def to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_int_list(value: Any) -> list[int]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[int] = []
    seen: set[int] = set()
    for item in value:
        parsed = to_int(item)
        if parsed is None or parsed <= 0 or parsed in seen:
            continue
        seen.add(parsed)
        result.append(parsed)
    return result


def parse_chinese_number(token: str) -> int | None:
    text = str(token or "").strip()
    if not text:
        return None
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if text in digits:
        return digits[text]
    if text == "十":
        return 10
    if "十" in text:
        left, right = text.split("十", 1)
        if left and left not in digits:
            return None
        if right and right not in digits:
            return None
        tens = digits[left] if left else 1
        ones = digits[right] if right else 0
        return tens * 10 + ones
    return None


def parse_numeric_token(token: str) -> int | None:
    parsed = to_int(token)
    if parsed is not None:
        return parsed
    return parse_chinese_number(token)


def extract_explicit_file_refs(question: str) -> list[int]:
    text = str(question or "")
    ids: list[int] = []
    seen: set[int] = set()
    patterns = [
        re.compile(r"#\s*(\d+)"),
        re.compile(r"编号\s*(\d+)"),
        re.compile(r"(\d+)\s*号(?:文献|文件|表格|pdf|excel|csv)"),
    ]
    for pattern in patterns:
        for matched in pattern.findall(text):
            parsed = to_int(matched)
            if parsed is None or parsed <= 0 or parsed in seen:
                continue
            seen.add(parsed)
            ids.append(parsed)
    return ids


def extract_ordinal_refs(question: str) -> OrdinalRefs:
    text = str(question or "")
    direct_indexes: list[int] = []
    front_count = 0
    back_count = 0
    reverse_indexes: list[int] = []
    ambiguous_values: list[int] = []
    seen_direct: set[int] = set()
    seen_reverse: set[int] = set()
    seen_ambiguous: set[int] = set()

    direct_pattern = re.compile(r"第\s*([0-9零〇一二两三四五六七八九十]+)\s*个(?:文献|文件|表格|pdf|excel|csv)?")
    for token in direct_pattern.findall(text):
        parsed = parse_numeric_token(token)
        if parsed is None or parsed <= 0 or parsed in seen_direct:
            continue
        seen_direct.add(parsed)
        direct_indexes.append(parsed)

    front_pattern = re.compile(r"前\s*([0-9零〇一二两三四五六七八九十]+)\s*个(?:文献|文件|表格|pdf|excel|csv)?")
    for token in front_pattern.findall(text):
        parsed = parse_numeric_token(token)
        if parsed is None or parsed <= 0:
            continue
        front_count = max(front_count, parsed)

    back_pattern = re.compile(r"后\s*([0-9零〇一二两三四五六七八九十]+)\s*个(?:文献|文件|表格|pdf|excel|csv)?")
    for token in back_pattern.findall(text):
        parsed = parse_numeric_token(token)
        if parsed is None or parsed <= 0:
            continue
        back_count = max(back_count, parsed)

    reverse_pattern = re.compile(r"倒数第\s*([0-9零〇一二两三四五六七八九十]+)\s*个(?:文献|文件|表格|pdf|excel|csv)?")
    for token in reverse_pattern.findall(text):
        parsed = parse_numeric_token(token)
        if parsed is None or parsed <= 0 or parsed in seen_reverse:
            continue
        seen_reverse.add(parsed)
        reverse_indexes.append(parsed)

    ambiguous_pattern = re.compile(r"第\s*([0-9零〇一二两三四五六七八九十]+)\s*(?:篇|份)(?:文献|文件|表格)?")
    for token in ambiguous_pattern.findall(text):
        parsed = parse_numeric_token(token)
        if parsed is None or parsed <= 0 or parsed in seen_ambiguous:
            continue
        seen_ambiguous.add(parsed)
        ambiguous_values.append(parsed)

    return {
        "direct_indexes": direct_indexes,
        "front_count": int(front_count),
        "back_count": int(back_count),
        "reverse_indexes": reverse_indexes,
        "ambiguous_values": ambiguous_values,
        "has_ordinal": bool(direct_indexes or front_count or back_count or reverse_indexes),
        "has_ambiguous": bool(ambiguous_values),
    }


def contains_any(text: str, keywords: list[str]) -> bool:
    lowered = str(text or "").lower()
    return any(keyword in lowered for keyword in keywords)


def detect_plural_file_reference(question: str) -> bool:
    return contains_any(
        question,
        [
            "这些文献",
            "这些文档",
            "这些文件",
            "这些表格",
            "这两个文档",
            "这两个文件",
            "这两篇文献",
            "这两份文档",
            "全部文献",
            "全部文件",
            "全部表格",
            "所有文献",
            "所有文档",
            "所有文件",
            "所有表格",
            "上述文献",
            "上述文档",
            "上述文件",
            "all files",
            "all papers",
            "documents",
        ],
    )


def detect_singular_file_reference(question: str) -> bool:
    return contains_any(
        question,
        [
            "这篇文献",
            "这个文档",
            "这份文档",
            "这篇文章",
            "这个文件",
            "这份文件",
            "这份文献",
            "这份表格",
            "这个表格",
            "该文献",
            "该文件",
            "该表格",
            "this paper",
            "this document",
            "this file",
            "this table",
        ],
    )


def detect_latest_reference(question: str) -> bool:
    return contains_any(
        question,
        [
            "最新上传",
            "刚上传",
            "刚才上传",
            "latest uploaded",
            "latest file",
        ],
    )


def detect_file_intent(question: str, *, explicit_refs: list[int]) -> bool:
    if explicit_refs:
        return True
    return contains_any(
        question,
        [
            "上传",
            "文献",
            "文档",
            "文件",
            "pdf",
            "excel",
            "csv",
            "表格",
            "工作表",
            "sheet",
            "paper",
            "file",
            "table",
        ],
    )


def detect_mixed_intent(question: str) -> bool:
    text = str(question or "").lower()
    if contains_any(
        text,
        [
            "结合知识库",
            "结合外部知识",
            "结合预加载文献",
            "结合数据库",
            "知识库补充",
            "再结合文献库",
            "并用知识库验证",
            "外部资料",
            "外部证据",
            "knowledge base",
        ],
    ):
        return True

    has_kb_token = ("知识库" in text) or ("knowledge base" in text) or (" kb " in f" {text} ")
    if not has_kb_token:
        return False
    return contains_any(
        text,
        [
            "结合",
            "并",
            "同时",
            "一起",
            "参考",
            "补充",
            "验证",
            "讲解",
            "解释",
            "分析",
        ],
    )


def normalize_file_row(row: dict[str, Any]) -> NormalizedFileRow:
    file_id = to_int(row.get("id"))
    if file_id is None:
        file_id = to_int(row.get("file_id"))
    file_no = to_int(row.get("file_no"))
    display_no = to_int(row.get("display_no"))
    return {
        "file_id": int(file_id or 0),
        "file_no": int(file_no or 0),
        "display_no": int(display_no or 0),
        "file_type": str(row.get("file_type") or "").strip().lower(),
        "file_name": str(row.get("file_name") or "").strip(),
        "file_status": str(row.get("file_status") or "active").strip().lower() or "active",
        "local_path": str(row.get("local_path") or "").strip(),
        "storage_ref": str(row.get("storage_ref") or "").strip(),
        "parse_status": str(row.get("parse_status") or "").strip().lower(),
        "index_status": str(row.get("index_status") or "").strip().lower(),
        "processing_stage": str(row.get("processing_stage") or "").strip().lower(),
        "last_error": str(row.get("last_error") or "").strip(),
        "deleted_at": row.get("deleted_at"),
        "deleted_by": row.get("deleted_by"),
        "file_meta": row.get("file_meta") if isinstance(row.get("file_meta"), dict) else {},
    }


def build_used_file_payload(*, file_row: NormalizedFileRow, selected_reason: str) -> UsedFilePayload:
    return {
        "file_id": int(file_row.get("file_id") or 0),
        "file_no": int(file_row.get("file_no") or 0),
        "display_no": int(file_row.get("display_no") or 0),
        "file_type": str(file_row.get("file_type") or ""),
        "file_name": str(file_row.get("file_name") or ""),
        "selected_reason": selected_reason,
        "source": "conversation_files",
        "parse_status": str(file_row.get("parse_status") or ""),
        "index_status": str(file_row.get("index_status") or ""),
        "processing_stage": str(file_row.get("processing_stage") or ""),
        "last_error": str(file_row.get("last_error") or ""),
    }


def build_execution_file_payload(*, file_row: NormalizedFileRow, selected_reason: str) -> ExecutionFilePayload:
    payload = build_used_file_payload(file_row=file_row, selected_reason=selected_reason)
    payload["local_path"] = str(file_row.get("local_path") or "")
    payload["storage_ref"] = str(file_row.get("storage_ref") or "")
    payload["file_meta"] = file_row.get("file_meta") if isinstance(file_row.get("file_meta"), dict) else {}
    return payload


def sort_files(rows: list[NormalizedFileRow]) -> list[NormalizedFileRow]:
    def sort_key(item: NormalizedFileRow) -> tuple[int, int]:
        file_no = to_int(item.get("file_no"))
        file_id = to_int(item.get("file_id"))
        no_value = file_no if file_no is not None and file_no > 0 else 10**9
        id_value = file_id if file_id is not None and file_id > 0 else 0
        return no_value, id_value

    return sorted(rows, key=sort_key)


def resolve_refs_to_file_ids(
    refs: list[int],
    *,
    file_no_map: dict[int, NormalizedFileRow],
    file_id_map: dict[int, NormalizedFileRow],
) -> list[int]:
    result: list[int] = []
    seen: set[int] = set()
    for value in refs:
        target = file_no_map.get(value) or file_id_map.get(value)
        if not target:
            continue
        file_id = int(target.get("file_id") or 0)
        if file_id <= 0 or file_id in seen:
            continue
        seen.add(file_id)
        result.append(file_id)
    return result


def resolve_ordinal_refs_to_file_ids(
    *,
    ordinal_refs: OrdinalRefs,
    sorted_files: list[NormalizedFileRow],
) -> list[int]:
    if not sorted_files:
        return []

    picked: list[int] = []
    seen: set[int] = set()

    def add_row(row: NormalizedFileRow) -> None:
        file_id = int(row.get("file_id") or 0)
        if file_id <= 0 or file_id in seen:
            return
        seen.add(file_id)
        picked.append(file_id)

    for index in ordinal_refs.get("direct_indexes") or []:
        pos = int(index) - 1
        if 0 <= pos < len(sorted_files):
            add_row(sorted_files[pos])

    front_count = int(ordinal_refs.get("front_count") or 0)
    if front_count > 0:
        for row in sorted_files[:front_count]:
            add_row(row)

    back_count = int(ordinal_refs.get("back_count") or 0)
    if back_count > 0:
        for row in sorted_files[max(0, len(sorted_files) - back_count) :]:
            add_row(row)

    for reverse_index in ordinal_refs.get("reverse_indexes") or []:
        pos = len(sorted_files) - int(reverse_index)
        if 0 <= pos < len(sorted_files):
            add_row(sorted_files[pos])

    return picked


def build_clarify_candidates(rows: list[NormalizedFileRow], *, max_items: int = 8) -> list[FileSelectionCandidate]:
    items: list[FileSelectionCandidate] = []
    for row in rows[:max_items]:
        items.append(
            {
                "file_id": int(row.get("file_id") or 0),
                "file_no": int(row.get("file_no") or 0),
                "display_no": int(row.get("display_no") or 0),
                "file_type": str(row.get("file_type") or ""),
                "file_name": str(row.get("file_name") or ""),
            }
        )
    return items


def build_clarification_message(
    candidates: list[FileSelectionCandidate],
    *,
    include_order_hint: bool = False,
) -> str:
    lines = ["当前对话中命中了多个文件，请明确要操作的文件编号："]
    for item in candidates:
        display_no = int(item.get("display_no") or 0)
        file_no = int(item.get("file_no") or 0)
        file_id = int(item.get("file_id") or 0)
        label = f"#{display_no}" if display_no > 0 else (f"#{file_no}" if file_no > 0 else f"(id={file_id})")
        lines.append(f"- {label} [{item.get('file_type')}] {item.get('file_name')}")
    if include_order_hint and candidates:
        order_rows: list[str] = []
        for idx, item in enumerate(candidates, start=1):
            display_no = int(item.get("display_no") or 0)
            file_no = int(item.get("file_no") or 0)
            file_id = int(item.get("file_id") or 0)
            label = f"#{display_no}" if display_no > 0 else (f"#{file_no}" if file_no > 0 else f"(id={file_id})")
            order_rows.append(f"第{idx}个: {label}")
        lines.append("当前顺序映射: " + "，".join(order_rows))
    lines.append("例如：请总结 #1 和 #4。")
    return "\n".join(lines)
