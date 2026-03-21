#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit logging and strict DOI verification helpers."""

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from app.modules.generation_pipeline.doi_validation import canonicalize_doi


def write_alignment_audit(
    *,
    question: Optional[str],
    original_answer: str,
    audit_list: List[Dict[str, Any]],
    retrieval_results: Optional[Dict[str, Any]],
    logger: Any,
) -> None:
    """Write alignment audit records as JSONL."""
    try:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_file = log_dir / f"alignment_audit_{date_str}.jsonl"

        record = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "question": question,
            "answer_preview": (original_answer or "")[:1000],
            "audit": audit_list,
            "retrieval_summary": {
                "retrieved_count": int(
                    retrieval_results.get("total_retrieved", len(retrieval_results.get("documents", [])))
                )
                if retrieval_results
                else None,
                "unique_count": int(retrieval_results.get("unique_count", 0)) if retrieval_results else None,
            },
        }

        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.info(f"   ℹ️ 已写入对齐审计日志: {log_file}")
    except Exception as e:
        logger.warning(f"⚠️ 写入对齐审计日志失败: {e}")


def strict_verify_answer(
    *,
    agent: Any,
    answer: str,
    retrieval_results: Dict[str, Any],
    question: Optional[str],
    validate_and_fix_doi_fn,
    aligner_cls: Any,
    logger: Any,
) -> str:
    """Strictly verify DOI claims and annotate/remove unsupported ones."""
    try:
        answer_dois = re.findall(r"\(doi\s*=\s*(10\.[^\)\s]+)\)", answer, re.IGNORECASE)
        if not answer_dois:
            return answer

        audit = retrieval_results.get("_alignment_audit", []) or []
        verified = set(a["doi"] for a in audit if a.get("doi"))

        if not verified and aligner_cls:
            try:
                sentences = re.split(r"(?<=[。！？?!.；;])\s*", answer)
                aligner = aligner_cls(
                    literature_expert=agent.literature_expert,
                    seq_weight=getattr(agent, "seq_similarity_weight", 0.6),
                    vector_weight=getattr(agent, "vector_similarity_weight", 0.4),
                    embedding_weight=getattr(agent, "embedding_similarity_weight", 0.6),
                    max_seq_chars=getattr(agent, "max_seq_compare_chars", 1000),
                )
                alignments = aligner.align(
                    sentences,
                    retrieval_results,
                    similarity_threshold=getattr(agent, "insert_similarity_threshold", 0.45),
                )
                verified = set(a["doi"] for a in alignments if a.get("doi"))
                retrieval_results.setdefault("_alignment_audit", []).extend(alignments)
                try:
                    write_alignment_audit(
                        question=question,
                        original_answer=answer,
                        audit_list=alignments,
                        retrieval_results=retrieval_results,
                        logger=logger,
                    )
                except Exception:
                    pass
            except Exception:
                verified = set()

        modified = answer
        canonical_verified = {canonicalize_doi(item) for item in verified if canonicalize_doi(item)}
        for doi in set(answer_dois):
            canonical_answer_doi = canonicalize_doi(doi)
            if canonical_answer_doi in canonical_verified or doi in verified:
                continue

            doi_found_in_metadata = any(
                canonicalize_doi(validate_and_fix_doi_fn(meta.get("doi", "")) or "") == canonical_answer_doi
                for meta in retrieval_results.get("all_metadatas", retrieval_results.get("metadatas", []))
                if meta and meta.get("doi")
            )
            if doi_found_in_metadata:
                logger.info(f"   ✅ DOI {doi} 在检索结果中找到，保留")
                continue

            if getattr(agent, "strict_action", "annotate") == "remove":
                modified = re.sub(
                    r"\s*\(doi\s*=\s*" + re.escape(doi) + r"\)",
                    "",
                    modified,
                    flags=re.IGNORECASE,
                )
                logger.info(f"   🔒 严格模式：移除未验证 DOI {doi}")
            else:
                modified = re.sub(
                    r"\(doi\s*=\s*" + re.escape(doi) + r"\)",
                    r"(doi=" + doi + r" — 未找到原文证据)",
                    modified,
                    flags=re.IGNORECASE,
                )
                logger.info(f"   🔒 严格模式：标注未验证 DOI {doi}")

        return modified
    except Exception as e:
        logger.warning(f"⚠️ 严格验证出错: {e}")
        return answer
