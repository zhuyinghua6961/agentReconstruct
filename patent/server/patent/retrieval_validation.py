from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+")
_METRIC_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mAh/g|mah/g|g/cm3|g/cm³|C-rate|C|℃|°C|%)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PatentValidationResult:
    selected: list[dict[str, Any]]
    filtered: list[dict[str, Any]]
    diagnostics: dict[str, Any]


def _tokens(value: str) -> set[str]:
    return {item.lower() for item in _TOKEN_RE.findall(str(value or "")) if item.strip()}


def validate_patent_stage2_candidates(
    *,
    candidates: list[dict[str, Any]],
    user_question: str,
    claim_text: str,
    min_results: int,
) -> PatentValidationResult:
    question_tokens = _tokens(f"{user_question} {claim_text}")
    metrics = {item.lower().replace(" ", "").replace("cm³", "cm3") for item in _METRIC_RE.findall(f"{user_question} {claim_text}")}
    selected: list[dict[str, Any]] = []
    filtered: list[dict[str, Any]] = []
    for candidate in list(candidates or []):
        metadata = dict(candidate.get("metadata") or {})
        if metadata.get("exact_id_match"):
            selected.append(candidate)
            continue
        document = str(candidate.get("document") or "")
        document_tokens = _tokens(document)
        overlap = len(question_tokens & document_tokens)
        normalized_document = document.lower().replace(" ", "").replace("cm³", "cm3")
        metric_match = bool(metrics and any(metric in normalized_document for metric in metrics))
        if metric_match or overlap >= 2:
            selected.append(candidate)
        else:
            filtered.append(candidate)

    fallback = False
    if len(selected) < max(0, int(min_results)) and candidates:
        fallback = True
        selected = list(candidates)[: max(1, int(min_results))]
        selected_keys = {id(item) for item in selected}
        filtered = [item for item in list(candidates) if id(item) not in selected_keys]

    return PatentValidationResult(
        selected=selected,
        filtered=filtered,
        diagnostics={
            "enabled": True,
            "validated_count": len(selected),
            "filtered_count": len(filtered),
            "validation_fallback": fallback,
        },
    )
