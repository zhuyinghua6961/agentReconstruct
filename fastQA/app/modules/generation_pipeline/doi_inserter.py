#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Programmatic DOI insertion service for generation-driven RAG answers."""

import math
import re
import time
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, Optional


def _cosine_similarity(a, b) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


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


def programmatic_insert_dois(
    *,
    agent: Any,
    answer: str,
    retrieval_results: Dict[str, Any],
    similarity_threshold: Optional[float],
    question: Optional[str],
    validate_and_fix_doi_fn: Callable[[str], Optional[str]],
    aligner_cls: Any,
    logger: Any,
) -> str:
    """Insert DOI references by sentence-to-evidence alignment with audit."""
    if not getattr(agent, "enable_programmatic_doi_insertion", True):
        logger.info("ℹ️ 程序化DOI插入已禁用（runtime），直接返回原答案")
        return answer

    started_at = time.perf_counter()
    logger.info("ℹ️ 程序化对齐：尝试将答案句子与检索证据对齐并插入 DOI")
    if similarity_threshold is None:
        similarity_threshold = getattr(agent, "insert_similarity_threshold", 0.45)

    seq_weight = getattr(agent, "seq_similarity_weight", 0.6)
    vector_weight = getattr(agent, "vector_similarity_weight", 0.4)
    max_seq_chars = getattr(agent, "max_seq_compare_chars", 1000)
    use_sentence_emb = getattr(agent, "use_sentence_embeddings", True)
    embedding_weight = getattr(agent, "embedding_similarity_weight", 0.6)
    if use_sentence_emb:
        ew = float(embedding_weight)
        vw = float(getattr(agent, "vector_similarity_weight", 0.4))
        total = ew + vw if (ew + vw) > 0 else 1.0
        embedding_weight = ew / total
        vector_weight = vw / total

    if not answer or not retrieval_results:
        return answer

    emb_model = getattr(getattr(agent, "literature_expert", None), "embedding_model", None)
    embedding_enabled = bool(use_sentence_emb and emb_model and hasattr(emb_model, "encode"))
    sentence_embedding_cache: dict[str, Any] = {}
    doc_embedding_cache: dict[str, Any] = {}
    pdf_sentence_embedding_cache: dict[str, Any] = {}
    pdf_sentences_cache: dict[str, Optional[list[str]]] = {}
    cache_stats = {
        "sentence_hits": 0,
        "sentence_misses": 0,
        "doc_hits": 0,
        "doc_misses": 0,
        "pdf_hits": 0,
        "pdf_misses": 0,
        "pdf_sentence_hits": 0,
        "pdf_sentence_misses": 0,
    }

    def _cache_get_embedding(text: str, cache: dict[str, Any], *, kind: str):
        payload = str(text or "")
        if not embedding_enabled or not payload:
            return None
        cached = cache.get(payload)
        if cached is not None:
            cache_stats[f"{kind}_hits"] += 1
            return cached
        try:
            vector = emb_model.encode([payload])[0]
        except Exception:
            return None
        cache_stats[f"{kind}_misses"] += 1
        cache[payload] = vector
        return vector

    def _get_sentence_embedding(text: str):
        return _cache_get_embedding(text[:max_seq_chars], sentence_embedding_cache, kind="sentence")

    def _get_doc_embedding(text: str):
        return _cache_get_embedding(text[:max_seq_chars], doc_embedding_cache, kind="doc")

    def _get_pdf_sentence_embedding(text: str):
        return _cache_get_embedding(text[:max_seq_chars], pdf_sentence_embedding_cache, kind="pdf_sentence")

    def _get_pdf_sentences(doi: str) -> Optional[list[str]]:
        if doi in pdf_sentences_cache:
            cache_stats["pdf_hits"] += 1
            return pdf_sentences_cache[doi]
        try:
            sentences = agent._load_pdf_sentences(doi)
        except Exception:
            sentences = None
        cache_stats["pdf_misses"] += 1
        pdf_sentences_cache[doi] = list(sentences) if sentences else None
        return pdf_sentences_cache[doi]

    metadatas = retrieval_results.get("all_metadatas", retrieval_results.get("metadatas", [])) or []
    documents = retrieval_results.get("all_documents", retrieval_results.get("documents", [])) or []
    distances = retrieval_results.get("all_distances", retrieval_results.get("distances", [])) or []

    candidate_docs = []
    for meta, doc, dist in zip(metadatas, documents, distances):
        doi_raw = (meta or {}).get("doi", "") or ""
        doi_raw = doi_raw.strip()
        if not doi_raw or not doi_raw.startswith("10."):
            continue

        doi_clean = validate_and_fix_doi_fn(doi_raw)
        if not doi_clean:
            continue

        doc_text = doc or ""
        try:
            if dist is None:
                vector_sim = 0.0
            else:
                dist_val = float(dist)
                vector_sim = 1.0 if dist_val <= 0 else math.exp(-dist_val)
        except Exception:
            vector_sim = 0.0

        candidate_docs.append(
            {
                "doi": doi_clean,
                "text": doc_text,
                "vector_sim": vector_sim,
            }
        )

    if not candidate_docs:
        logger.info("ℹ️ 无可用带 DOI 的检索片段，跳过程序化插入")
        return answer

    if getattr(agent, "use_new_aligner", False) and aligner_cls:
        try:
            sentence_units = _iter_sentence_units(answer)
            sentences = [body for body, _suffix in sentence_units]
            aligner = aligner_cls(
                literature_expert=agent.literature_expert,
                seq_weight=seq_weight,
                vector_weight=vector_weight,
                embedding_weight=embedding_weight,
                max_seq_chars=max_seq_chars,
            )
            alignments = aligner.align(sentences, retrieval_results, similarity_threshold=similarity_threshold)

            out_sentences = []
            align_map = {a["sentence_idx"]: a for a in alignments}
            used_dois = set()
            for idx, (sent, suffix) in enumerate(sentence_units):
                s = sent + suffix
                if idx in align_map:
                    doi_to_insert = align_map[idx]["doi"]
                    used_dois.add(doi_to_insert)
                    s = sent.rstrip() + f" (doi={doi_to_insert})" + suffix
                out_sentences.append(s)

            new_answer = "".join(out_sentences)
            retrieval_results.setdefault("_alignment_audit", []).extend(alignments)

            if getattr(agent, "strict_mode", False):
                try:
                    new_answer = agent._strict_verify_answer(new_answer, retrieval_results, question=question)
                except Exception as e:
                    logger.warning(f"⚠️ 新 aligner 后的严格验证失败: {e}")

            try:
                agent._write_alignment_audit(
                    question=question,
                    original_answer=answer,
                    audit_list=alignments,
                    retrieval_results=retrieval_results,
                )
            except Exception:
                pass

            logger.info(f"   ✅ 新 aligner: 插入 {len(used_dois)} 个 DOI")
            return new_answer
        except Exception as e:
            logger.warning(f"⚠️ 新 aligner 执行失败，回退到旧逻辑: {e}")

    sentence_units = _iter_sentence_units(answer)
    effective_sentences = [body.strip() for body, _suffix in sentence_units if str(body or "").strip()]
    logger.info(
        "ℹ️ 程序化DOI插入开始 candidate_docs=%s answer_sentences=%s embedding_enabled=%s",
        len(candidate_docs),
        len(effective_sentences),
        embedding_enabled,
    )

    out_sentences = []
    used_dois = set()
    verified_pass_count = 0
    verified_fail_count = 0

    for sent, suffix in sentence_units:
        original_chunk = sent + suffix
        sent_strip = sent.strip()
        if not sent_strip:
            out_sentences.append(original_chunk)
            continue

        cleaned_sent = sent_strip
        doi_found = False
        doi_pattern = r"\(doi\s*=\s*(10\.[^()]*?)\s*(?:\·\s*查看原文\s*●)?\s*\)"
        while re.search(doi_pattern, cleaned_sent, re.IGNORECASE):
            match = re.search(doi_pattern, cleaned_sent, re.IGNORECASE)
            if not match:
                break
            doi_val = match.group(1).strip()
            valid_doi = validate_and_fix_doi_fn(doi_val)
            if valid_doi:
                used_dois.add(valid_doi)
                doi_found = True
                logger.warning(f"   ⚠️ 检测到LLM生成的错误DOI格式，已清理: {match.group(0)}")
            cleaned_sent = cleaned_sent.replace(match.group(0), "").strip()

        if not cleaned_sent or re.match(r"^[^\w\u4e00-\u9fff]*$", cleaned_sent):
            if doi_found:
                out_sentences.append(original_chunk)
                continue
            out_sentences.append(original_chunk)
            continue

        sent_strip = cleaned_sent
        sent_emb = _get_sentence_embedding(sent_strip)
        best_doc = None
        best_score = 0.0

        for doc_entry in candidate_docs:
            doc_text = str(doc_entry.get("text", "") or "")[:max_seq_chars]
            embed_sim = None
            if sent_emb is not None:
                doc_emb = _get_doc_embedding(doc_text)
                if doc_emb is not None:
                    embed_sim = _cosine_similarity(sent_emb, doc_emb)

            if embed_sim is not None:
                combined_score = embedding_weight * embed_sim + vector_weight * doc_entry.get("vector_sim", 0.0)
            else:
                try:
                    seq_ratio = SequenceMatcher(None, sent_strip, doc_text).ratio()
                except Exception:
                    seq_ratio = 0.0
                combined_score = seq_weight * seq_ratio + vector_weight * doc_entry.get("vector_sim", 0.0)

            if combined_score > best_score:
                best_score = combined_score
                best_doc = doc_entry

        if best_doc and best_score >= similarity_threshold:
            doi_to_insert = best_doc["doi"]
            verify_pass = False
            verify_details: Dict[str, Any] = {}
            try:
                doc_text = str(best_doc.get("text", "") or "")[:max_seq_chars]
                embed_sim = None
                seq_ratio = None
                vec_sim = None

                if sent_emb is not None:
                    doc_emb = _get_doc_embedding(doc_text)
                    if doc_emb is not None:
                        embed_sim = _cosine_similarity(sent_emb, doc_emb)
                        verify_details["embed_sim"] = float(embed_sim)

                try:
                    seq_ratio = SequenceMatcher(None, sent_strip, doc_text).ratio()
                    verify_details["seq_ratio"] = float(seq_ratio)
                except Exception:
                    seq_ratio = None

                try:
                    vec_sim = float(best_doc.get("vector_sim", 0.0))
                    verify_details["vector_sim"] = vec_sim
                except Exception:
                    vec_sim = None

                pdf_sents = _get_pdf_sentences(doi_to_insert)
                pdf_verified = False
                if pdf_sents:
                    best_pdf_seq = 0.0
                    best_pdf_idx = None
                    for j, p in enumerate(pdf_sents[:200]):
                        try:
                            r = SequenceMatcher(None, sent_strip, p[:max_seq_chars]).ratio()
                        except Exception:
                            r = 0.0
                        if r > best_pdf_seq:
                            best_pdf_seq = r
                            best_pdf_idx = j
                            if best_pdf_seq >= getattr(agent, "insert_seq_verify_threshold", 0.60):
                                break
                    verify_details["best_pdf_seq"] = float(best_pdf_seq)
                    if best_pdf_seq >= getattr(agent, "insert_seq_verify_threshold", 0.60):
                        pdf_verified = True
                        verify_details["matched_pdf_sentence"] = pdf_sents[best_pdf_idx][:300]
                    elif sent_emb is not None:
                        try:
                            best_pdf_emb = 0.0
                            best_pdf_idx2 = None
                            for j, p in enumerate(pdf_sents[:50]):
                                p_emb = _get_pdf_sentence_embedding(p)
                                if p_emb is None:
                                    continue
                                score = _cosine_similarity(sent_emb, p_emb)
                                if score > best_pdf_emb:
                                    best_pdf_emb = score
                                    best_pdf_idx2 = j
                            verify_details["best_pdf_emb"] = float(best_pdf_emb)
                            if best_pdf_emb >= getattr(agent, "insert_embed_verify_threshold", 0.60):
                                pdf_verified = True
                                verify_details["matched_pdf_sentence"] = pdf_sents[best_pdf_idx2][:300]
                        except Exception:
                            pass

                if getattr(agent, "require_pdf_evidence_for_doi", False):
                    verify_pass = bool(pdf_verified)
                else:
                    if embed_sim is not None and embed_sim >= getattr(agent, "insert_embed_verify_threshold", 0.60):
                        verify_pass = True
                    if seq_ratio is not None and seq_ratio >= getattr(agent, "insert_seq_verify_threshold", 0.60):
                        verify_pass = True
                    if vec_sim is not None and vec_sim >= getattr(agent, "insert_vector_verify_threshold", 0.60):
                        verify_pass = True
                    if pdf_verified:
                        verify_pass = True
            except Exception as e:
                logger.warning(f"⚠️ 插入前验证出错: {e}")

            if verify_pass:
                used_dois.add(doi_to_insert)
                verified_pass_count += 1
                new_sent = sent.rstrip() + f" (doi={doi_to_insert})" + suffix
                out_sentences.append(new_sent)
                logger.info(
                    f"   ✅ 句子对齐并验证通过：'{sent_strip[:80]}...' -> doi={doi_to_insert} "
                    f"(score={best_score:.3f}) verify={verify_details}"
                )
                audit = retrieval_results.setdefault("_alignment_audit", [])
                audit.append(
                    {
                        "sentence_preview": sent_strip[:200],
                        "doi": doi_to_insert,
                        "score": float(best_score),
                        "verify_pass": True,
                        "verify_details": verify_details,
                    }
                )
            else:
                verified_fail_count += 1
                out_sentences.append(original_chunk)
                logger.info(
                    f"   ⚠️ 句子对齐但验证未通过，跳过插入：'{sent_strip[:80]}...' -> doi={doi_to_insert} "
                    f"(score={best_score:.3f}) verify={verify_details}"
                )
                audit = retrieval_results.setdefault("_alignment_audit", [])
                audit.append(
                    {
                        "sentence_preview": sent_strip[:200],
                        "doi": doi_to_insert,
                        "score": float(best_score),
                        "verify_pass": False,
                        "verify_details": verify_details,
                    }
                )
        else:
            out_sentences.append(original_chunk)

    new_answer = "".join(out_sentences)
    elapsed = time.perf_counter() - started_at
    logger.info(
        "✅ 程序化对齐完成 inserted=%s verify_pass=%s verify_fail=%s elapsed=%.3fs pdf_loads=%s pdf_cache_hits=%s sent_emb(hit=%s miss=%s) doc_emb(hit=%s miss=%s) pdf_sent_emb(hit=%s miss=%s)",
        len(used_dois),
        verified_pass_count,
        verified_fail_count,
        elapsed,
        cache_stats["pdf_misses"],
        cache_stats["pdf_hits"],
        cache_stats["sentence_hits"],
        cache_stats["sentence_misses"],
        cache_stats["doc_hits"],
        cache_stats["doc_misses"],
        cache_stats["pdf_sentence_hits"],
        cache_stats["pdf_sentence_misses"],
    )

    if getattr(agent, "strict_mode", False):
        try:
            new_answer = agent._strict_verify_answer(new_answer, retrieval_results, question=question)
        except Exception as e:
            logger.warning(f"⚠️ 严格验证失败: {e}")

    try:
        audit_list = retrieval_results.get("_alignment_audit", [])
        if audit_list:
            agent._write_alignment_audit(
                question=question,
                original_answer=answer,
                audit_list=audit_list,
                retrieval_results=retrieval_results,
            )
    except Exception as e:
        logger.warning(f"⚠️ 写入对齐审计日志失败: {e}")

    return new_answer
