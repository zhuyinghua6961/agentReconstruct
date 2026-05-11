"""Optional DOI provenance logging for the thinking agent.

Set ``HIGHTHINKINGQA_DOI_DIAGNOSTICS=1`` (or ``true``/``yes``/``on``) to emit
per-request INFO lines comparing:

- **R**: DOIs attached to retrieved chunks (vector metadata)
- **P**: DOIs found in pre-answer text (direct + sub answers)
- **F**: DOIs found in draft or final answer text (same scan as ``_extract_references``)

Interpretation:

- Non-empty ``F_minus_R``: answer contains DOI tokens not present on retrieved chunks
  (synthesis / reviser hallucination, or pre-answer leakage copied into the answer).
- ``unknown_overlap_pre``: subset of ``F_minus_R`` that also appeared in **P** —
  supports pre-answer as the source of those tokens.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from server.utils.doi import extract_dois_from_answer_text, normalize_doi


def doi_diagnostics_enabled() -> bool:
    raw = str(os.getenv("HIGHTHINKINGQA_DOI_DIAGNOSTICS", "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def build_preanswer_blob(direct_answer: str, sub_answers: list[str]) -> str:
    parts = [str(direct_answer or "")]
    parts.extend(str(a or "") for a in (sub_answers or []))
    return "\n".join(parts)


def retrieved_doi_keys(all_chunks: list[list[Any]]) -> set[str]:
    keys: set[str] = set()
    for group in all_chunks or []:
        for chunk in group or []:
            doi = normalize_doi(getattr(chunk, "doi", "") or "")
            if doi and "/" in doi:
                keys.add(doi.lower())
    return keys


def log_doi_trace(
    logger: logging.Logger,
    *,
    trace_prefix: str,
    phase: str,
    answer_text: str,
    pre_blob: str,
    all_chunks: list[list[Any]],
) -> None:
    r = retrieved_doi_keys(all_chunks)
    p_keys = {d.lower() for d in extract_dois_from_answer_text(pre_blob)}
    f_list = extract_dois_from_answer_text(answer_text)
    unknown = [d for d in f_list if d.lower() not in r]
    in_pre_and_answer = sorted({d for d in f_list if d.lower() in p_keys}, key=str.lower)
    unknown_overlap_pre = sorted({d for d in unknown if d.lower() in p_keys}, key=str.lower)
    logger.info(
        "%sDOI_TRACE phase=%s R=%s P_dois=%s F=%s F_minus_R=%s F_minus_R_sample=%s "
        "F_intersect_pre=%s unknown_F_minus_R_overlap_pre=%s",
        trace_prefix,
        phase,
        len(r),
        len(p_keys),
        len(f_list),
        len(unknown),
        unknown[:15],
        in_pre_and_answer[:15],
        unknown_overlap_pre[:15],
    )
