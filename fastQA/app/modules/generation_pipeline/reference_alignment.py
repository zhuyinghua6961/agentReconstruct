#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reference alignment and evidence formatting for generation-driven RAG."""

import math
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Tuple

from app.modules.generation_pipeline.text_processing import extract_question_keywords_with_weights
from app.modules.generation_pipeline.feature_flags import env_int


_SENTENCE_WITH_SUFFIX_RE = re.compile(r".*?(?<=[。！？?!.；;])\s*|.+$", re.DOTALL)


def _iter_sentence_units(answer: str) -> list[tuple[str, str]]:
    units: list[tuple[str, str]] = []
    for chunk in _SENTENCE_WITH_SUFFIX_RE.findall(answer or ""):
        if chunk == "":
            continue
        match = re.match(r"(?s)(.*?)(\s*)$", chunk)
        if match:
            units.append((match.group(1), match.group(2)))
        else:
            units.append((chunk, ""))
    if not units and answer:
        units.append((answer, ""))
    return units


def extract_dois_from_results(retrieval_results: Dict[str, Any]) -> List[str]:
    """Extract unique valid DOI values from retrieval metadata."""
    metadatas = retrieval_results.get("metadatas", [])
    dois_set = set()

    for meta in metadatas:
        doi = meta.get("doi", "").strip()
        if doi and doi.startswith("10."):
            dois_set.add(doi)

    return list(dois_set)


def validate_dois_against_retrieval(
    cited_dois: List[str],
    retrieval_results: Dict[str, Any],
    logger: Any,
) -> Tuple[List[str], List[str]]:
    """Verify cited DOI values exist in retrieval metadata."""
    valid_dois = set()
    metadatas = retrieval_results.get("metadatas", []) or retrieval_results.get("all_metadatas", [])

    for meta in metadatas:
        if meta and isinstance(meta, dict):
            doi = meta.get("doi", "").strip()
            if doi and doi.startswith("10."):
                valid_dois.add(doi)

    cited_valid: List[str] = []
    cited_invalid: List[str] = []
    for doi in cited_dois:
        clean_doi = doi.strip()
        doi_variants = [
            clean_doi,
            clean_doi.replace("_", "/"),
        ]

        found = False
        for variant in doi_variants:
            if variant in valid_dois:
                cited_valid.append(variant)
                found = True
                break

        if not found:
            cited_invalid.append(clean_doi)
            logger.warning(f"   🔍 DOI验证失败: {clean_doi} 不在检索结果中（可能为编造）")

    logger.info(f"   📊 DOI验证结果: {len(cited_valid)}/{len(cited_dois)} 个DOI有效")
    return cited_valid, cited_invalid


def align_dois_with_pdf_chunks(
    answer: str,
    pdf_chunks: Dict[str, List[Dict[str, Any]]],
    emb_model: Any,
    threshold: float,
    logger: Any,
) -> str:
    """Insert DOI references by sentence-level similarity against PDF chunks."""
    if not answer or not pdf_chunks:
        return answer

    logger.info("   📎 开始PDF chunk级别DOI对齐...")

    candidate_chunks = []
    for doi, chunks in pdf_chunks.items():
        for chunk in chunks:
            text = chunk.get("text", "")
            if text and len(text) > 50:
                candidate_chunks.append(
                    {
                        "doi": doi,
                        "text": text,
                        "page": chunk.get("page", 0),
                    }
                )

    if not candidate_chunks:
        logger.info("   ⚠️ 无有效PDF chunks，跳过DOI对齐")
        return answer

    logger.info(f"   📊 共有 {len(candidate_chunks)} 个候选PDF chunks")

    sentence_units = _iter_sentence_units(answer)

    out_sentences = []
    inserted_count = 0

    for sent, suffix in sentence_units:
        original_chunk = sent + suffix
        sent_strip = sent.strip()
        if not sent_strip:
            out_sentences.append(original_chunk)
            continue

        if re.search(r"\(doi\s*=", sent, re.IGNORECASE):
            out_sentences.append(original_chunk)
            continue

        best_chunk = None
        best_score = 0.0
        for chunk_entry in candidate_chunks:
            chunk_text = chunk_entry["text"][:1000]

            if emb_model and hasattr(emb_model, "encode"):
                try:
                    sent_emb = emb_model.encode([sent_strip])[0]
                    chunk_emb = emb_model.encode([chunk_text])[0]
                    na = math.sqrt(sum(x * x for x in sent_emb))
                    nb = math.sqrt(sum(x * x for x in chunk_emb))
                    if na > 0 and nb > 0:
                        embed_sim = sum(x * y for x, y in zip(sent_emb, chunk_emb)) / (na * nb)
                    else:
                        embed_sim = SequenceMatcher(None, sent_strip, chunk_text).ratio()
                except Exception:
                    embed_sim = SequenceMatcher(None, sent_strip, chunk_text).ratio()
            else:
                embed_sim = SequenceMatcher(None, sent_strip, chunk_text).ratio()

            if embed_sim > best_score:
                best_score = embed_sim
                best_chunk = chunk_entry

        if best_chunk and best_score >= threshold:
            doi = best_chunk["doi"]
            new_sent = sent.rstrip() + f" (doi={doi})" + suffix
            out_sentences.append(new_sent)
            inserted_count += 1
            logger.debug(f"   ✅ 对齐成功: '{sent_strip[:50]}...' -> {doi} (score={best_score:.3f})")
        else:
            out_sentences.append(original_chunk)

    new_answer = "".join(out_sentences)
    logger.info(f"   ✅ DOI对齐完成: 插入了 {inserted_count} 个DOI")
    return new_answer


def format_pdf_chunks_evidence(
    pdf_chunks: Dict[str, List[Dict[str, Any]]],
    user_question: str,
    logger: Any,
) -> str:
    """Format top-ranked PDF chunk evidence by keyword relevance."""
    if not pdf_chunks:
        return "未找到PDF原文证据。"

    chunks_per_doi = env_int(
        "QA_STAGE4_EVIDENCE_CHUNKS_PER_DOI",
        env_int("EVIDENCE_CHUNKS_PER_DOI", 3, minimum=1, maximum=20),
        minimum=1,
        maximum=20,
    )
    chunk_max_chars = env_int(
        "QA_STAGE4_EVIDENCE_CHUNK_MAX_CHARS",
        env_int("EVIDENCE_CHUNK_MAX_CHARS", 1000, minimum=200, maximum=5000),
        minimum=200,
        maximum=5000,
    )

    keywords_with_weights = extract_question_keywords_with_weights(user_question)
    logger.debug(f"   🔑 从问题中提取的关键词: {list(keywords_with_weights.keys())}")

    doi_scores = []
    for doi, chunks in pdf_chunks.items():
        if not chunks:
            continue

        score = 0.0
        matched_keywords = set()
        core_match_count = 0

        for chunk in chunks:
            text = chunk.get("text", "").lower()
            for kw, weight in keywords_with_weights.items():
                if kw.lower() in text:
                    matched_keywords.add(kw)
                    if weight >= 3.0:
                        score += weight
                        core_match_count += 1
                    else:
                        score += weight * 0.5

        doi_scores.append(
            {
                "doi": doi,
                "chunks": chunks,
                "score": score,
                "matched_keywords": matched_keywords,
                "core_match_count": core_match_count,
            }
        )

    doi_scores.sort(key=lambda item: (item["score"], item["core_match_count"]), reverse=True)

    logger.debug("   📊 DOI相关性排序（Top 10）:")
    for i, info in enumerate(doi_scores[:10]):
        logger.debug(
            f"      {i + 1}. {info['doi']}: 得分={info['score']:.1f}, "
            f"核心匹配={info['core_match_count']}, 匹配词={list(info['matched_keywords'])[:3]}"
        )

    max_relevant_doi = 10
    relevant_dois = doi_scores[:max_relevant_doi]
    filtered_dois = doi_scores[max_relevant_doi:]
    logger.debug(f"   ✅ 保留 {len(relevant_dois)} 个高相关性DOI（过滤 {len(filtered_dois)} 个低相关性DOI）")

    lines = ["## 支持性文献原文（来自PDF溯源，按相关性排序）", ""]
    lines.append(f"**用户问题**: {user_question}")
    lines.append(
        f"**⚠️ 重要提示**: 系统已从 {len(pdf_chunks)} 个DOI中筛选出 "
        f"{len(relevant_dois)} 个与问题最相关的文献证据（按相关性得分排序）。"
    )
    lines.append("")

    if keywords_with_weights:
        sorted_kw = sorted(keywords_with_weights.items(), key=lambda item: item[1], reverse=True)
        kw_display = " | ".join([f"{k}(权重:{v:.1f})" for k, v in sorted_kw])
        lines.append(f"**关键词权重**: {kw_display}")

    core_keywords = [k for k, v in keywords_with_weights.items() if v >= 3.0]
    if core_keywords:
        lines.append(f"**核心关键词**: {', '.join(core_keywords)}")
    lines.append("")

    for idx, doi_info in enumerate(relevant_dois, 1):
        doi = doi_info["doi"]
        chunks = doi_info["chunks"]
        matched_keywords = doi_info["matched_keywords"]

        relevance_label = (
            "⭐⭐⭐ 极高相关"
            if doi_info["score"] >= 20
            else "⭐⭐ 高相关"
            if doi_info["score"] >= 10
            else "⭐ 中相关"
        )
        lines.append(f"### 文献{idx}: {relevance_label} (doi={doi})")
        lines.append(f"**相关性得分**: {doi_info['score']:.1f} | **核心匹配数**: {doi_info['core_match_count']}")

        if matched_keywords:
            sorted_matched = sorted(
                matched_keywords,
                key=lambda item: keywords_with_weights.get(item, 0),
                reverse=True,
            )
            matched_display = ", ".join(sorted_matched[:5])
            if len(sorted_matched) > 5:
                matched_display += f" ... (+{len(sorted_matched) - 5}个)"
            lines.append(f"**匹配关键词**: {matched_display}")
        lines.append("")

        for i, chunk in enumerate(chunks[:chunks_per_doi], 1):
            text = chunk.get("text", "")
            page = chunk.get("page", 0)
            if len(text) > chunk_max_chars:
                text = text[:chunk_max_chars] + "..."

            lines.append(f"**片段{i}（第{page}页）**:")
            lines.append(text)
            lines.append("")

    if filtered_dois:
        filtered_count = len(filtered_dois)
        lines.append("---")
        lines.append(f"**其他说明**: 另有 {filtered_count} 个低相关性DOI未被展示（如需查看全部文献，可单独请求）")

    lines.append("")
    lines.append("---")
    lines.append("**DOI引用规则**:")
    lines.append("- 每个片段后面请标注对应的DOI，格式为：`(doi=DOI编号)`")
    lines.append("- 例如：`这是研究结论 (doi=10.1002/adfm.202001263)`")
    lines.append("- 只对有文献支撑的内容添加DOI引用")
    lines.append("- 基本常识和通用知识不需要DOI引用")
    lines.append("- 优先引用**高相关性**的文献（⭐⭐标记的）")

    return "\n".join(lines)
