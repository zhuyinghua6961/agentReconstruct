"""Pure DOI normalization helpers for thinking ask flows."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote


_DOI_START_WITHOUT_SEPARATOR_RE = re.compile(r"(10\.\d{1,9})(?=[A-Za-z])", re.IGNORECASE)
_DOI_MERGED_SECOND_PREFIX_RE = re.compile(
    r"(?P<first>10\.\d{1,9}/[-._;()/:A-Z0-9]+?)(?P<sep>[)\]])(?P<registrant>\d{4,9})\.(?P<suffix>[A-Z][-._;()/:A-Z0-9]*)",
    re.IGNORECASE,
)
_DOI_EXTRACT_RE = re.compile(
    r"10\.\d{1,9}/[-._;()/:A-Z0-9]+?(?=(?:[\]\)\s,;:]*)10\.\d{1,9}/|[\]\)\s,;:]*$)",
    re.IGNORECASE,
)


def _repair_missing_separator(text: str) -> str:
    return _DOI_START_WITHOUT_SEPARATOR_RE.sub(r"\1/", str(text or ""))


def _repair_merged_second_prefix(text: str) -> str:
    repaired = str(text or "")
    previous = None
    while previous != repaired:
        previous = repaired
        repaired = _DOI_MERGED_SECOND_PREFIX_RE.sub(
            lambda match: (
                f"{match.group('first')}{match.group('sep')}"
                f"10.{match.group('registrant')}/{match.group('suffix')}"
            ),
            repaired,
        )
    return repaired


def normalize_doi(value: str) -> str:
    text = str(value or "").strip()
    filename_like_source = False
    previous = None
    while previous != text:
        previous = text
        text = unquote(text).strip()
    text = text.replace("\\", "/")
    text = _repair_missing_separator(text)
    text = re.sub(r"^doi:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^[(/\\s]+|[)\],;:.\\s]+$", "", text)
    if "papers/" in text:
        text = text.split("papers/", 1)[-1]
        filename_like_source = text.lower().endswith(".pdf")
    elif (
        text.lower().endswith(".pdf")
        and (
            os.path.isabs(text)
            or text.startswith("./")
            or text.startswith("../")
            or bool(re.match(r"^[A-Za-z]:[\\/]", text))
        )
    ):
        text = Path(text).name or text
        filename_like_source = True
    if text.lower().endswith(".pdf"):
        text = text[:-4]
    if "_" in text and "/" not in text and text.startswith("10.") and not filename_like_source:
        text = text.replace("_", "/", 1)
    return text.strip()


def extract_dois(value: str) -> list[str]:
    text = _repair_merged_second_prefix(normalize_doi(value))
    if not text:
        return []
    results: list[str] = []
    seen: set[str] = set()
    matches = [match.group(0) for match in _DOI_EXTRACT_RE.finditer(text)]
    candidates = matches or [text]
    for candidate in candidates:
        normalized = normalize_doi(candidate)
        if not normalized.startswith("10.") or "/" not in normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        results.append(normalized)
    return results
