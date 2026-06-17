from __future__ import annotations

import re
from typing import Any


_PAGE_MARKER_PATTERN = re.compile(r"^\s*---\s*第\s*(\d+)\s*页\s*---\s*$", re.MULTILINE)
_INTERNAL_PAGE_MARKER_PATTERN = re.compile(r"^\s*\[\[PAGE:(\d+)\]\]\s*$", re.MULTILINE)
_METADATA_TITLE_PATTERN = re.compile(r"^\s*标题:\s*(.+?)\s*$", re.MULTILINE)
_METADATA_AUTHOR_PATTERN = re.compile(r"^\s*作者:\s*(.+?)\s*$", re.MULTILINE)
_SEPARATOR_LINE_PATTERN = re.compile(r"^\s*=+\s*$", re.MULTILINE)
_REFERENCE_SECTION_PATTERN = re.compile(
    r"(?im)^\s*(references|bibliography|参考文献|参考书目|引用文献|文献列表)\s*[：:]*\s*$"
)
_SENTENCE_END_CHARS = ".!?;:\"')]}。；：」』）】"
_JOURNAL_HEADER_PATTERN = re.compile(
    r"(?im)^\s*journal of\b.*$|^\s*www\.[a-z0-9.-]+\.[a-z]{2,}\s*$"
)
_STANDALONE_PAGE_NUMBER_PATTERN = re.compile(r"^\s*\d{1,4}\s*$")
_DUPLICATE_HEADING_PATTERN = re.compile(r"(?m)^(#{1,6}\s+.+)$\n+\1\s*$")
_HEADING_LINE_PATTERN = re.compile(r"^(#{1,6}\s+.+)\s*$")


def _dedupe_segment_boundary_headings(segments: list[str]) -> list[str]:
    cleaned: list[str] = []
    previous_heading = ""
    for segment in segments:
        normalized_segment = str(segment or "").strip()
        if not normalized_segment:
            continue
        lines = normalized_segment.split("\n")
        first_heading = ""
        body_start = 0
        for index, line in enumerate(lines):
            matched = _HEADING_LINE_PATTERN.match(line.strip())
            if matched:
                first_heading = matched.group(1).strip()
                body_start = index + 1
                while body_start < len(lines) and not lines[body_start].strip():
                    body_start += 1
                break
        if first_heading and first_heading == previous_heading:
            normalized_segment = "\n".join(lines[body_start:]).strip()
        elif first_heading:
            previous_heading = first_heading
        if normalized_segment:
            cleaned.append(normalized_segment)
    return cleaned


def _normalize_newlines(text: str) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _extract_metadata(full_text: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    title_match = _METADATA_TITLE_PATTERN.search(full_text)
    if title_match:
        meta["title"] = title_match.group(1).strip()
    author_match = _METADATA_AUTHOR_PATTERN.search(full_text)
    if author_match:
        meta["authors"] = author_match.group(1).strip()
    return meta


def _strip_metadata_header(full_text: str) -> str:
    lines = _normalize_newlines(full_text).split("\n")
    body_start = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            body_start = index + 1
            continue
        if stripped.startswith("标题:") or stripped.startswith("作者:"):
            body_start = index + 1
            continue
        if _SEPARATOR_LINE_PATTERN.fullmatch(stripped):
            body_start = index + 1
            continue
        break
    return "\n".join(lines[body_start:]).strip()


def _normalize_page_markers(text: str) -> str:
    def _replace_page_marker(match: re.Match[str]) -> str:
        return f"[[PAGE:{match.group(1)}]]"

    return _PAGE_MARKER_PATTERN.sub(_replace_page_marker, text)


def _merge_hyphenated_line_breaks(text: str) -> str:
    lines = text.split("\n")
    merged: list[str] = []
    index = 0
    while index < len(lines):
        current = lines[index]
        if (
            index + 1 < len(lines)
            and current.endswith("-")
            and current[:-1].strip()
            and lines[index + 1]
            and lines[index + 1][:1].islower()
        ):
            merged.append(current[:-1] + lines[index + 1].lstrip())
            index += 2
            continue
        merged.append(current)
        index += 1
    return "\n".join(merged)


def _merge_soft_line_breaks(text: str) -> str:
    paragraphs = re.split(r"\n\s*\n", text)
    fixed_paragraphs: list[str] = []
    for paragraph in paragraphs:
        lines = [line.strip() for line in paragraph.split("\n") if line.strip()]
        if not lines:
            continue
        merged_lines: list[str] = []
        for line in lines:
            if not merged_lines:
                merged_lines.append(line)
                continue
            previous = merged_lines[-1]
            if previous.endswith(_SENTENCE_END_CHARS):
                merged_lines.append(line)
                continue
            if line[:1].islower() or line[:1].isdigit() or line.startswith("("):
                merged_lines[-1] = f"{previous} {line}"
            else:
                merged_lines.append(line)
        fixed_paragraphs.append("\n".join(merged_lines))
    return "\n\n".join(fixed_paragraphs)


def _truncate_references_section(text: str) -> str:
    match = _REFERENCE_SECTION_PATTERN.search(text)
    if not match:
        return text
    return text[: match.start()].rstrip()


def prepare_body_for_document_translation(full_text: str) -> tuple[str, dict[str, Any]]:
    normalized = _normalize_newlines(full_text)
    meta = _extract_metadata(normalized)
    body = _strip_metadata_header(normalized)
    body = _normalize_page_markers(body)
    body = _truncate_references_section(body)
    body = _merge_hyphenated_line_breaks(body)
    body = _merge_soft_line_breaks(body)
    body = _truncate_references_section(body)
    return body.strip(), meta


def _dedupe_adjacent_headings(text: str) -> str:
    previous = None
    while previous != text:
        previous = text
        text = _DUPLICATE_HEADING_PATTERN.sub(r"\1", text)
    return text


def _normalize_markdown_spacing(text: str) -> str:
    cleaned = re.sub(r"\n{3,}", "\n\n", text.strip())
    lines = cleaned.split("\n")
    filtered: list[str] = []
    for line in lines:
        stripped = line.strip()
        if _STANDALONE_PAGE_NUMBER_PATTERN.fullmatch(stripped):
            continue
        if _JOURNAL_HEADER_PATTERN.fullmatch(stripped):
            continue
        filtered.append(line.rstrip())
    cleaned = "\n".join(filtered)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def assemble_document_translation_markdown(
    segments: list[str],
    *,
    meta: dict[str, Any] | None = None,
    document_id: str = "",
) -> str:
    body_parts = _dedupe_segment_boundary_headings([str(item or "").strip() for item in segments if str(item or "").strip()])
    body = _dedupe_adjacent_headings("\n\n".join(body_parts))
    body = _normalize_markdown_spacing(body)

    meta_dict = dict(meta or {})
    title = str(meta_dict.get("title") or meta_dict.get("document_title") or "").strip()
    authors = str(meta_dict.get("authors") or "").strip()
    doi = str(meta_dict.get("doi") or document_id or "").strip()

    header_lines: list[str] = []
    if title:
        header_lines.append(f"# {title}")
    elif doi:
        header_lines.append(f"# {doi}")

    meta_bits: list[str] = []
    if authors:
        meta_bits.append(f"作者：{authors}")
    if doi:
        meta_bits.append(f"DOI：{doi}")
    if meta_bits:
        header_lines.append(f"> {' | '.join(meta_bits)}")
    if header_lines:
        header_lines.append("---")

    if not header_lines:
        return body
    if not body:
        return "\n\n".join(header_lines).strip()
    return "\n\n".join([*header_lines, body]).strip()
