"""Merge Stage2 vector-retrieval snippets into the PDF/MD evidence map.

Stage2 already ranks passages against claim-specific queries; embedding Stage3 often
starts from the PDF front matter. Prepending retrieval hits keeps retrieval-aligned
text inside the Stage3.5 rerank pool."""

from __future__ import annotations

import math
from typing import Any, Dict, List

from app.modules.generation_pipeline.feature_flags import env_bool, env_int


def _doi_identity_key(doi: str) -> str:
    """Normalize DOI shapes (10.xxx/j vs 10.xxx_j) for equality."""
    d = str(doi or "").strip().lower()
    if not d.startswith("10."):
        return ""
    if "_" in d and "/" not in d:
        d = d.replace("_", "/", 1)
    return d


def resolve_doi_bucket(meta_doi: str, dois_ordered: List[str]) -> str | None:
    """Map metadata DOI string onto the gated DOI key used elsewhere in the pipeline."""
    mid = _doi_identity_key(meta_doi)
    if not mid:
        return None
    for hint in dois_ordered:
        if _doi_identity_key(hint) == mid:
            return hint
    return None


def _distance_sort_key(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float("inf")
    if math.isnan(value):
        return float("inf")
    return value


def extract_stage2_retrieval_chunks_by_doi(
    *,
    retrieval_results: Dict[str, Any] | None,
    dois_ordered: List[str],
    max_chunks_total: int | None = None,
    max_chunks_per_doi: int | None = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Pick top Stage2 document hits per gated DOI (distance ascending).

    Uses flattened ``documents`` / ``metadatas`` / ``distances`` from Stage2 aggregation.
    """
    if not retrieval_results or not dois_ordered:
        return {}

    resolved_total = (
        env_int("QA_STAGE2_RETRIEVAL_EVIDENCE_MAX_TOTAL", 40, minimum=1, maximum=200)
        if max_chunks_total is None
        else max(1, min(int(max_chunks_total), 200))
    )
    resolved_per_doi = (
        env_int("QA_STAGE2_RETRIEVAL_EVIDENCE_MAX_PER_DOI", 4, minimum=1, maximum=30)
        if max_chunks_per_doi is None
        else max(1, min(int(max_chunks_per_doi), 30))
    )

    docs = list(retrieval_results.get("documents") or [])
    metas = list(retrieval_results.get("metadatas") or [])
    dists = list(retrieval_results.get("distances") or [])

    rows: List[tuple[float, str, str, Dict[str, Any]]] = []
    limit = min(len(docs), len(metas))
    for idx in range(limit):
        text = str(docs[idx] or "").strip()
        meta = metas[idx] if isinstance(metas[idx], dict) else {}
        bucket = resolve_doi_bucket(str(meta.get("doi") or meta.get("DOI") or meta.get("source_doi") or ""), dois_ordered)
        if not text or bucket is None:
            continue
        dist = dists[idx] if idx < len(dists) else None
        rows.append((_distance_sort_key(dist), text, bucket, meta))

    rows.sort(key=lambda item: item[0])

    per_doi_counts: Dict[str, int] = {}
    total = 0
    out: Dict[str, List[Dict[str, Any]]] = {}
    seen_keys: set[tuple[str, str]] = set()

    seq = 0
    for _dist, text, bucket, meta in rows:
        prefix = text[:200]
        dedupe_key = (bucket, prefix)
        if not prefix or dedupe_key in seen_keys:
            continue
        if per_doi_counts.get(bucket, 0) >= resolved_per_doi:
            continue
        if total >= resolved_total:
            break

        seen_keys.add(dedupe_key)
        per_doi_counts[bucket] = per_doi_counts.get(bucket, 0) + 1
        total += 1

        page_raw = meta.get("page")
        try:
            page = int(page_raw) if page_raw is not None and str(page_raw).strip() != "" else 0
        except (TypeError, ValueError):
            page = 0

        seq += 1
        chunk = {
            "doi": bucket,
            "text": text,
            "page": page,
            "chunk_id": f"s2_{seq}",
            "chunk_type": "stage2_retrieval",
            "source": "stage2_retrieval",
            "stage2_distance": float(_dist) if _dist != float("inf") else None,
        }
        out.setdefault(bucket, []).append(chunk)

    return out


def merge_stage2_chunks_into_pdf_chunks(
    *,
    pdf_chunks: Dict[str, List[Dict[str, Any]]],
    stage2_by_doi: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Prepend Stage2 snippets before MD/PDF chunks; dedupe by first 200 chars (Stage2 wins)."""
    if not stage2_by_doi:
        return dict(pdf_chunks or {})

    merged: Dict[str, List[Dict[str, Any]]] = {}
    all_keys = set(pdf_chunks or {}) | set(stage2_by_doi)
    for doi in all_keys:
        seen: set[str] = set()
        bucket: List[Dict[str, Any]] = []
        for chunk in list(stage2_by_doi.get(doi, [])) + list((pdf_chunks or {}).get(doi, [])):
            if not isinstance(chunk, dict):
                continue
            text_key = str(chunk.get("text") or "").strip()[:200]
            if not text_key or text_key in seen:
                continue
            seen.add(text_key)
            bucket.append(dict(chunk))
        if bucket:
            merged[str(doi)] = bucket
    return merged


def maybe_merge_stage2_retrieval_evidence(
    *,
    retrieval_results: Dict[str, Any] | None,
    dois_ordered: List[str],
    pdf_chunks: Dict[str, List[Dict[str, Any]]],
    logger: Any,
    enabled: bool | None = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """If enabled, merge Stage2 retrieval snippets into the evidence chunk map."""
    resolved = env_bool("QA_STAGE2_RETRIEVAL_EVIDENCE_MERGE_ENABLED", True) if enabled is None else bool(enabled)
    if not resolved:
        return dict(pdf_chunks or {})

    stage2_map = extract_stage2_retrieval_chunks_by_doi(
        retrieval_results=retrieval_results,
        dois_ordered=dois_ordered,
    )
    if not stage2_map:
        return dict(pdf_chunks or {})

    merged = merge_stage2_chunks_into_pdf_chunks(pdf_chunks=pdf_chunks, stage2_by_doi=stage2_map)
    try:
        s2_total = sum(len(v) for v in stage2_map.values())
        logger.info(
            "stage2 retrieval evidence merged stage2_chunks=%s dois=%s merged_chunk_total=%s",
            s2_total,
            len(stage2_map),
            sum(len(v) for v in merged.values()),
        )
    except Exception:
        pass
    return merged


__all__ = [
    "extract_stage2_retrieval_chunks_by_doi",
    "maybe_merge_stage2_retrieval_evidence",
    "merge_stage2_chunks_into_pdf_chunks",
    "resolve_doi_bucket",
]
