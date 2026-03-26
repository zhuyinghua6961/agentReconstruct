from __future__ import annotations

import os
import re
from typing import Any

_SUMMARY_HEADING_RE = re.compile(r'(^|\n)#{1,6}\s*总结\s*($|\n)', re.MULTILINE)
_SENTENCE_RE = re.compile(r'[^。！？!?\n]+[。！？!?]?')
_DOI_RE = re.compile(r'\[[^\]]*10\.[^\]]+\]')


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, '1' if default else '0') or '').strip().lower()
    if raw in {'1', 'true', 'yes', 'on'}:
        return True
    if raw in {'0', 'false', 'no', 'off'}:
        return False
    return bool(default)


def summary_experiment_enabled(*, enabled: bool | None = None) -> bool:
    if enabled is not None:
        return bool(enabled)
    return _env_bool('ANSWER_SUMMARY_EXPERIMENT', False)


def build_summary_instruction(*, enabled: bool | None = None) -> str:
    if not summary_experiment_enabled(enabled=enabled):
        return ''
    return (
        '\n\nAdditional requirement: append a final `## 总结` section at the end of the answer. '
        'Use 2-4 concise sentences or 3-5 bullets. Do not introduce any new evidence or new citations in the summary; '
        'only compress conclusions already stated in the main body.'
    )


def _strip_markdown_prefix(line: str) -> str:
    value = str(line or '').strip()
    value = re.sub(r'^#{1,6}\s*', '', value)
    value = re.sub(r'^[-*+]\s+', '', value)
    value = re.sub(r'^\d+[.)、]\s*', '', value)
    value = re.sub(r'^>\s*', '', value)
    value = value.strip('` ').strip()
    return value


def _collect_sentences(answer: str) -> list[str]:
    sentences: list[str] = []
    seen: set[str] = set()
    in_code_block = False
    for raw_line in str(answer or '').splitlines():
        line = str(raw_line or '')
        if line.strip().startswith('```'):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            continue
        cleaned = _strip_markdown_prefix(line)
        if not cleaned or cleaned == '总结':
            continue
        if cleaned.startswith('|') and cleaned.endswith('|'):
            continue
        for match in _SENTENCE_RE.finditer(cleaned):
            sentence = ' '.join(match.group(0).split()).strip()
            if len(sentence) < 12:
                continue
            normalized = _DOI_RE.sub('', sentence).strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            sentences.append(sentence)
    return sentences


def apply_answer_summary_experiment(answer: str, *, enabled: bool | None = None) -> tuple[str, dict[str, Any]]:
    resolved_enabled = summary_experiment_enabled(enabled=enabled)
    text = str(answer or '').strip()
    meta: dict[str, Any] = {
        'enabled': resolved_enabled,
        'generated': False,
        'format': '',
        'length': 0,
        'has_citation': False,
        'skipped_reason': '',
    }
    if not resolved_enabled:
        meta['skipped_reason'] = 'disabled'
        return text, meta
    if not text:
        meta['skipped_reason'] = 'empty_answer'
        return text, meta
    if _SUMMARY_HEADING_RE.search(text):
        meta['generated'] = True
        meta['format'] = 'existing'
        meta['length'] = len(text)
        meta['has_citation'] = bool(_DOI_RE.search(text))
        return text, meta

    sentences = _collect_sentences(text)
    if len(text) < 160 or len(sentences) <= 2:
        meta['skipped_reason'] = 'short_answer'
        return text, meta

    selected: list[str] = []
    total_chars = 0
    for sentence in sentences:
        if len(selected) >= 3:
            break
        sentence_len = len(sentence)
        if total_chars + sentence_len > 280 and selected:
            break
        selected.append(sentence)
        total_chars += sentence_len

    if len(selected) < 2:
        meta['skipped_reason'] = 'insufficient_sentences'
        return text, meta

    summary_block = '\n'.join(f'- {item}' for item in selected)
    summarized = f'{text}\n\n## 总结\n\n{summary_block}'.strip()
    meta['generated'] = True
    meta['format'] = 'bullet_fallback'
    meta['length'] = len(summary_block)
    meta['has_citation'] = bool(_DOI_RE.search(summary_block))
    return summarized, meta
