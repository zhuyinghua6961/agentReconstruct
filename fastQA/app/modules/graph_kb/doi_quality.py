from __future__ import annotations

import re
from dataclasses import dataclass


_STRICT_DOI_RE = re.compile(r"^10\.\d{1,9}/\S+$", re.IGNORECASE)
_GLUED_SUFFIX_RE = re.compile(r"(Received|Cite|Journal|Abstract|Keywords)$", re.IGNORECASE)


@dataclass(frozen=True)
class DoiQuality:
    doi: str
    status: str
    reason: str = ""


def classify_doi_quality(value: str) -> DoiQuality:
    doi = str(value or "").strip().strip(".,;:，。；：")
    if not doi:
        return DoiQuality(doi="", status="invalid", reason="empty")
    if "/" not in doi or not doi.lower().startswith("10."):
        return DoiQuality(doi=doi, status="invalid", reason="not_doi")
    if not _STRICT_DOI_RE.match(doi):
        return DoiQuality(doi=doi, status="invalid", reason="invalid_shape")
    if doi.endswith(("-", "/", "_", ".")):
        return DoiQuality(doi=doi, status="suspicious", reason="truncated_suffix")
    if _GLUED_SUFFIX_RE.search(doi):
        return DoiQuality(doi=doi, status="suspicious", reason="glued_suffix")
    return DoiQuality(doi=doi, status="valid")
