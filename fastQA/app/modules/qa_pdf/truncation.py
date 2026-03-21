from __future__ import annotations

import re
from typing import Any


IMPORTANT_SECTIONS = {
    'abstract': ['abstract', '摘要', 'summary'],
    'introduction': ['introduction', '引言', '背景', 'background'],
    'results': ['results', '结果', '实验结果', 'experimental results'],
    'discussion': ['discussion', '讨论', '分析'],
    'conclusion': ['conclusion', '结论', '总结', 'conclusions'],
    'methods': ['methods', '方法', 'methodology', '实验方法'],
    'materials': ['materials', '材料', '样品', '样本'],
}

MULTI_DOC_HEADER_PATTERN = re.compile(r'^\s*=+\s*文献\s*[^=\n]*=+\s*$', re.MULTILINE)


def _clip_text_with_boundary(text: str, limit: int) -> str:
    if limit <= 0:
        return ''
    if len(text) <= limit:
        return text
    floor = max(1, int(limit * 0.6))
    boundary = max(
        text.rfind('\n', floor, limit),
        text.rfind('。', floor, limit),
        text.rfind('.', floor, limit),
        text.rfind('；', floor, limit),
        text.rfind(';', floor, limit),
    )
    cut = boundary if boundary > 0 else limit
    clipped = text[:cut].rstrip()
    if len(clipped) < len(text):
        clipped += '...'
    return clipped


def _split_multi_doc_sections(pdf_content: str) -> list[tuple[str, str]]:
    matches = list(MULTI_DOC_HEADER_PATTERN.finditer(pdf_content))
    if len(matches) < 2:
        return []

    sections: list[tuple[str, str]] = []
    for idx, matched in enumerate(matches):
        start = matched.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(pdf_content)
        header = matched.group(0).strip()
        body = str(pdf_content[start:end]).strip()
        if body:
            sections.append((header, body))
    return sections


def _truncate_multi_pdf_content(pdf_content: str, *, max_chars: int, logger: Any) -> str:
    sections = _split_multi_doc_sections(pdf_content)
    if len(sections) < 2:
        return ''

    total_docs = len(sections)
    logger.info(f'📚 检测到多文献合并内容，共 {total_docs} 篇，启用均衡截断')

    reserve = min(260, max(120, int(max_chars * 0.08)))
    header_cost = sum(len(header) + 3 for header, _ in sections)
    available_for_body = max(0, max_chars - reserve - header_cost)
    if available_for_body <= 0:
        logger.warning('⚠️ 多文献截断预算不足，回退到普通截断')
        return ''

    base = max(80, available_for_body // total_docs)
    remainder = max(0, available_for_body - base * total_docs)

    selected_parts: list[str] = []
    for idx, (header, body) in enumerate(sections):
        budget = base + (1 if idx < remainder else 0)
        excerpt = _clip_text_with_boundary(body, budget)
        selected_parts.append(f'{header}\n{excerpt}')

    result = '\n\n'.join(selected_parts).strip()
    note = f'\n\n[注意：已从 {total_docs} 篇文献中按均衡配额截断，原始 {len(pdf_content)} 字符，保留 {len(result)} 字符]'
    max_body_chars = max_chars - len(note)
    if max_body_chars <= 0:
        max_body_chars = max(40, max_chars // 2)
    if len(result) > max_body_chars:
        result = _clip_text_with_boundary(result, max_body_chars)

    final_text = result + note
    logger.info(f'✅ 多文献均衡截断完成，最终长度: {len(final_text)} 字符')
    return final_text


def _locate_section_indices(paragraphs: list[str], content_lower: str) -> dict[str, int]:
    section_indices: dict[str, int] = {}
    for section_name, keywords in IMPORTANT_SECTIONS.items():
        for keyword in keywords:
            if keyword not in content_lower:
                continue
            for idx, para in enumerate(paragraphs):
                if keyword in para.lower():
                    section_indices[section_name] = idx
                    break
            if section_name in section_indices:
                break
    return section_indices


def _get_priority_and_allocation(is_summary: bool, question: str, max_chars: int) -> tuple[list[str], dict[str, float]]:
    if is_summary:
        return (
            ['abstract', 'introduction', 'results', 'discussion', 'conclusion', 'methods'],
            {
                'abstract': max_chars * 0.2,
                'introduction': max_chars * 0.2,
                'results': max_chars * 0.25,
                'discussion': max_chars * 0.15,
                'conclusion': max_chars * 0.15,
                'methods': max_chars * 0.05,
            },
        )

    question_lower = str(question or '').lower()
    if any(word in question_lower for word in ['性能', 'property', 'properties', 'capacity', 'voltage']):
        priority_order = ['results', 'discussion', 'abstract', 'introduction', 'conclusion', 'methods']
    elif any(word in question_lower for word in ['方法', '工艺', 'method', 'synthesis', 'preparation']):
        priority_order = ['methods', 'results', 'introduction', 'abstract', 'discussion', 'conclusion']
    else:
        priority_order = ['abstract', 'introduction', 'results', 'methods', 'discussion', 'conclusion']

    char_allocation = {section: max_chars * 0.15 for section in priority_order}
    if priority_order:
        char_allocation[priority_order[0]] = max_chars * 0.25
    return priority_order, char_allocation


def smart_truncate_pdf_content(
    pdf_content: str,
    max_chars: int,
    *,
    logger: Any,
    is_summary: bool = False,
    question: str = '',
) -> str:
    if len(pdf_content) <= max_chars:
        return pdf_content

    multi_doc_result = _truncate_multi_pdf_content(pdf_content, max_chars=max_chars, logger=logger)
    if multi_doc_result:
        return multi_doc_result

    logger.info(f'⚡ 开始智能截断PDF内容，原始长度: {len(pdf_content)} -> 目标: {max_chars}')
    paragraphs = pdf_content.split('\n\n')
    section_indices = _locate_section_indices(paragraphs, pdf_content.lower())
    priority_order, char_allocation = _get_priority_and_allocation(is_summary, question, max_chars)

    selected_paragraphs: list[str] = []
    total_chars = 0
    for section_name in priority_order:
        if section_name not in section_indices or total_chars >= max_chars:
            continue

        start_idx = section_indices[section_name]
        allocated_chars = int(char_allocation.get(section_name, max_chars * 0.1))
        section_content = ''
        current_idx = start_idx

        while current_idx < len(paragraphs) and len(section_content) < allocated_chars and total_chars + len(section_content) < max_chars:
            para = paragraphs[current_idx]
            if len(section_content + para) > allocated_chars:
                remaining_chars = allocated_chars - len(section_content)
                if remaining_chars > 100:
                    section_content += para[:remaining_chars] + '...'
                break
            section_content += para + '\n\n'
            current_idx += 1

        if section_content.strip():
            selected_paragraphs.append(f'【{section_name.upper()}】\n{section_content.strip()}')
            total_chars += len(section_content)

    if total_chars < max_chars * 0.8:
        remaining_chars = max_chars - total_chars
        front_content = pdf_content[:remaining_chars]
        if front_content.strip():
            selected_paragraphs.insert(0, f'【FRONT_CONTENT】\n{front_content}')

    result = '\n\n'.join(selected_paragraphs)
    if len(result) > max_chars:
        result = result[: max_chars - 100] + '...'

    result += f'\n\n[注意：PDF原文共{len(pdf_content)}字符，此处经过智能截断，仅保留最相关内容，共{len(result)}字符]'
    logger.info(f'✅ 智能截断完成，最终长度: {len(result)} 字符')
    return result
