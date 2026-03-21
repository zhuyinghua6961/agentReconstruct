#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DOI validation helpers for generation-driven pipeline."""

import re
from typing import Optional


def canonicalize_doi(doi: str) -> str:
    value = str(doi or "").strip()
    value = re.sub(r"[.,;:]+$", "", value)
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

    doi = str(doi).strip()
    url_patterns = [r"www\.", r"http://", r"https://", r"\.com", r"\.org", r"\.net", r"\.edu", r"\.gov"]
    for pattern in url_patterns:
        match = re.search(pattern, doi, re.IGNORECASE)
        if match:
            doi = doi[:match.start()].strip()
            break

    doi = canonicalize_doi(doi)
    if not re.match(r"^10\.\d+[/_]", doi):
        return None
    if not re.match(r"^10\.\d+[/_][A-Za-z0-9._\-/()]{2,}$", doi):
        return None
    return doi
