#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DOI validation helpers for generation-driven pipeline."""

import re
from typing import Optional


_DOI_START_WITHOUT_SEPARATOR_RE = re.compile(r"(10\.\d{1,9})(?=[A-Za-z])", re.IGNORECASE)
_DOI_EXTRACT_RE = re.compile(
    r"10\.\d{1,9}[/_][A-Za-z0-9._;()/:-]+?(?=(?:10\.\d{1,9}[/_])|$)",
    re.IGNORECASE,
)


def _prepare_doi_text(value: str) -> str:
    text = str(value or "").strip()
    text = _DOI_START_WITHOUT_SEPARATOR_RE.sub(r"\1/", text)
    url_patterns = [r"www\.", r"http://", r"https://", r"\.com", r"\.org", r"\.net", r"\.edu", r"\.gov"]
    for pattern in url_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            text = text[:match.start()].strip()
            break
    return text


def _trim_unbalanced_trailing_parens(value: str) -> str:
    text = str(value or "")
    while text.endswith(")") and text.count("(") < text.count(")"):
        text = text[:-1]
    return text


def canonicalize_doi(doi: str) -> str:
    value = _prepare_doi_text(doi)
    value = re.sub(r"[.,;:]+$", "", value)
    value = _trim_unbalanced_trailing_parens(value)
    if value.startswith("10.") and "_" in value and "/" not in value:
        value = value.replace("_", "/", 1)
    return value


def build_doi_variants(doi: str) -> list[str]:
    canonical = canonicalize_doi(doi)
    if not canonical:
        return []
    variants = [canonical]
    underscore = canonical.replace("/", "_", 1)
    if underscore not in variants:
        variants.append(underscore)
    return variants


def validate_and_fix_doi(doi: str) -> Optional[str]:
    """轻量 DOI 验证与修复，兼容 10.xxx/yyy 与 10.xxx_yyy 两种资源形态。"""
    if not doi:
        return None

    doi = canonicalize_doi(doi)
    if not re.match(r"^10\.\d+[/_]", doi):
        return None
    if not re.match(r"^10\.\d+[/_][A-Za-z0-9._\-/()]{1,}$", doi):
        return None
    return doi


def extract_valid_dois(value: str) -> list[str]:
    prepared = _prepare_doi_text(value)
    if not prepared:
        return []
    results: list[str] = []
    seen: set[str] = set()
    matches = [match.group(0) for match in _DOI_EXTRACT_RE.finditer(prepared)]
    candidates = matches or [prepared]
    for candidate in candidates:
        fixed = validate_and_fix_doi(candidate)
        if not fixed:
            continue
        if fixed.lower() in seen:
            continue
        seen.add(fixed.lower())
        results.append(fixed)
    return results
