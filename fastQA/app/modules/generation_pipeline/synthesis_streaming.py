from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Set, Tuple

from app.modules.generation_pipeline.feature_flags import env_bool, env_int
from app.modules.generation_pipeline.reference_alignment import (
    format_pdf_chunks_evidence as format_pdf_chunks_evidence_impl,
)
from app.modules.generation_pipeline.synthesis_postprocess import (
    build_references_from_pdf_chunks,
    build_top5_reference_context,
    extract_cited_dois as extract_cited_dois_with_logging,
    log_top5_coverage,
)


DOI_INLINE_PATTERN = re.compile(
    r"\(doi\s*=\s*(10\.(?:[^\s,()]+|\([^\s,()]+\))+)\)",
    re.IGNORECASE,
)

STAGE4_FACT_EXTRACTION_PROMPT = """你是一名严谨的文献事实提取专家。

任务：从下面证据文档中提取“可引用事实”。

严格要求：
1. 只提取证据里明确出现的事实，不要推断。
2. 每条事实需尽量保留关键参数/数值/单位。
3. 每条必须包含 DOI（格式 10.xxx/yyy）。
4. 仅输出 JSON 数组，不要输出解释文字。

输出格式：
[
  {{"fact": "具体表述", "doi": "10.xxx/yyy"}}
]

证据文档：
{evidence_documents}
"""

STAGE4_FACT_SYNTHESIS_PROMPT = """你是一名最终的答案润色与校验专家。

请基于以下材料生成最终答案：

1. 原始问题：{user_question}
2. 专家初稿（仅用于结构参考）：{deep_answer}
3. 可引用事实列表（仅可引用此处 DOI）：{facts_list}

要求：
1. 论断优先使用事实列表中的信息。
2. 具体结论后可添加 `(doi=xxx)`，但 DOI 必须来自事实列表。
3. 如某点无事实支撑，可保留一般性说明但不要编造 DOI。
4. 每个关键要点需包含机理解释（如何/为什么）与定量信息（数值/单位/条件），禁止空泛结论。
5. 输出 Markdown，禁止在文末单独列 DOI 列表。

{top5_references}
"""

STAGE4_STRUCTURE_ONLY_PROMPT = """你是一名基于文献证据生成答案的专家。

请基于以下材料生成最终答案：

1. 原始问题：{user_question}
2. 开头段落（可保留）：{opening_paragraph}
3. 答案结构大纲（仅作框架）：{structure_outline}
4. 支持性文献原文：{evidence_documents}

要求：
1. 按结构大纲组织答案。
2. 具体数值和结论优先来自证据文档。
3. 引用使用 `(doi=xxx)`，且必须在证据中出现。
4. 每个关键要点需包含机理解释（如何/为什么）与定量信息（数值/单位/条件），禁止空泛结论。
5. 输出 Markdown，禁止文末单独 DOI 列表。

{top5_references}
"""


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _escape_braces(text: str) -> str:
    return str(text or "").replace("{", "{{").replace("}", "}}")


def _canonicalize_doi(doi: str) -> str:
    value = str(doi or "").strip()
    value = re.sub(r"[.,;:]+$", "", value)
    if "_" in value and "/" not in value:
        value = value.replace("_", "/", 1)
    return value


def _build_doi_variants(doi: str) -> Set[str]:
    canonical = _canonicalize_doi(doi)
    if not canonical:
        return set()
    return {canonical, canonical.replace("/", "_", 1)}


def format_pdf_chunks_evidence(pdf_chunks: dict[str, list[dict[str, Any]]], user_question: str = "") -> str:
    logger = type("_NoopLogger", (), {"debug": lambda *args, **kwargs: None})()
    return format_pdf_chunks_evidence_impl(
        pdf_chunks=pdf_chunks,
        user_question=user_question,
        logger=logger,
    )


def extract_cited_dois(final_answer: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in DOI_INLINE_PATTERN.finditer(str(final_answer or "")):
        doi = _canonicalize_doi(match.group(1))
        if not doi or doi in seen:
            continue
        seen.add(doi)
        found.append(doi)
    return found


def _validate_answer_dois_with_pdf_chunks(
    *,
    answer: str,
    pdf_chunks: Dict[str, List[Dict[str, Any]]],
) -> Tuple[str, List[str], List[str]]:
    if not answer:
        return answer, [], []
    used_dois = sorted(set(m.group(1).strip() for m in DOI_INLINE_PATTERN.finditer(answer)))
    if not used_dois:
        return answer, [], []
    raw_keys = {str(k or "").strip() for k in (pdf_chunks or {}).keys() if str(k or "").strip()}
    canonical_keys = {_canonicalize_doi(k) for k in raw_keys if _canonicalize_doi(k)}
    valid: List[str] = []
    invalid: List[str] = []
    for doi in used_dois:
        variants = _build_doi_variants(doi)
        canonical = _canonicalize_doi(doi)
        matched = any(v in raw_keys for v in variants) or (canonical in canonical_keys if canonical else False)
        if matched:
            valid.append(doi)
        else:
            invalid.append(doi)
    cleaned = answer
    for doi in invalid:
        cleaned = re.sub(r"\s*\(doi\s*=\s*" + re.escape(doi) + r"\)", "", cleaned, flags=re.IGNORECASE)
    return cleaned, valid, invalid


def _extract_citable_facts_from_evidence(
    *,
    evidence_documents: str,
    client: Any,
    model: str,
    logger: Any,
) -> List[Dict[str, str]]:
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是严谨的事实提取器，只输出 JSON 数组。"},
                {"role": "user", "content": STAGE4_FACT_EXTRACTION_PROMPT.format(evidence_documents=evidence_documents)},
            ],
            temperature=0.1,
            max_tokens=1200,
            stream=False,
        )
        raw = str(response.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.warning("Stage4 two-stage fact extraction failed: %s", exc)
        return []
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except Exception:
        return []
    facts: List[Dict[str, str]] = []
    if not isinstance(data, list):
        return facts
    for item in data:
        if not isinstance(item, dict):
            continue
        fact = str(item.get("fact") or "").strip()
        doi = _canonicalize_doi(str(item.get("doi") or "").strip())
        if fact and doi.startswith("10."):
            facts.append({"fact": fact, "doi": doi})
    return facts


def _format_facts_for_prompt(facts: List[Dict[str, str]]) -> str:
    if not facts:
        return "（无）"
    return "\n".join(f"{idx}. {item['fact']} (doi={item['doi']})" for idx, item in enumerate(facts[:120], 1))


def _summarize_conversation_context_for_log(conversation_context: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(conversation_context, dict):
        return {
            "present": False,
            "turns": 0,
            "summary_present": False,
            "short_summary_present": False,
            "open_threads": 0,
            "memory_facts": 0,
        }

    turns = conversation_context.get("recent_turns_for_llm")
    summary = conversation_context.get("summary_for_llm")
    normalized_turns = [item for item in turns if isinstance(item, dict)] if isinstance(turns, list) else []
    normalized_summary = summary if isinstance(summary, dict) else {}
    open_threads = normalized_summary.get("open_threads") if isinstance(normalized_summary.get("open_threads"), list) else []
    memory_facts = normalized_summary.get("memory_facts") if isinstance(normalized_summary.get("memory_facts"), list) else []
    short_summary = " ".join(str(normalized_summary.get("short_summary") or "").split()).strip()
    return {
        "present": True,
        "turns": len(normalized_turns),
        "summary_present": bool(normalized_summary),
        "short_summary_present": bool(short_summary),
        "open_threads": len([item for item in open_threads if str(item).strip()]),
        "memory_facts": len([item for item in memory_facts if str(item).strip()]),
    }


def _format_conversation_context_for_stage4(conversation_context: dict[str, Any] | None) -> str:
    if not isinstance(conversation_context, dict):
        return ""

    parts: list[str] = []
    summary = conversation_context.get("summary_for_llm")
    if isinstance(summary, dict):
        short_summary = " ".join(str(summary.get("short_summary") or "").split()).strip()
        if short_summary:
            parts.append(f"会话摘要：{short_summary}")
        open_threads = [str(item).strip() for item in list(summary.get("open_threads") or []) if str(item).strip()]
        if open_threads:
            parts.append(f"待继续话题：{'；'.join(open_threads)}")
        memory_facts = [str(item).strip() for item in list(summary.get("memory_facts") or []) if str(item).strip()]
        if memory_facts:
            parts.append(f"已知事实：{'；'.join(memory_facts)}")

    turns = conversation_context.get("recent_turns_for_llm")
    if isinstance(turns, list):
        rendered_turns: list[str] = []
        for item in turns:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = " ".join(str(item.get("content") or "").split()).strip()
            if not content:
                continue
            role_label = "用户" if role == "user" else "助手"
            rendered_turns.append(f"{role_label}: {content}")
        if rendered_turns:
            parts.append("最近对话：\n" + "\n".join(rendered_turns))

    return "\n\n".join(parts).strip()


def _extract_structure_from_deep_answer(deep_answer: str) -> Tuple[str, str]:
    text = str(deep_answer or "").strip()
    if not text:
        return "（无）", ""
    lines = [line.rstrip() for line in text.splitlines()]
    opening: List[str] = []
    outline: List[str] = []
    seen_heading = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        is_heading = bool(re.match(r"^#{2,6}\s+", stripped)) or bool(re.match(r"^\d+[.)、]\s+", stripped))
        if is_heading:
            seen_heading = True
            normalized = re.sub(r"^#{2,6}\s*", "", stripped)
            normalized = re.sub(r"^\d+[.)、]\s*", "", normalized).strip()
            if normalized:
                outline.append(normalized)
            continue
        if not seen_heading and len(opening) < 4:
            opening.append(stripped)
    opening_paragraph = "\n".join(opening).strip() if opening else "（无）"
    if not outline:
        return opening_paragraph, ""
    outline_text = "\n".join(f"{idx}. {title}" for idx, title in enumerate(outline[:12], 1))
    return opening_paragraph, outline_text


def iter_stage4_synthesis_with_pdf_chunks(
    *,
    user_question: str,
    deep_answer: str,
    pdf_chunks: dict[str, list[dict[str, Any]]],
    retrieval_results: dict[str, Any] | None,
    stage2_prompt: str,
    client: Any,
    model: str,
    safe_dict_cls: Any | None = None,
    escape_braces_fn: Callable[[str], str] | None = None,
    format_pdf_chunks_evidence_fn: Callable[[dict[str, list[dict[str, Any]]], str], str] | None = None,
    build_top5_reference_context_fn: Callable[..., Any] | None = None,
    extract_cited_dois_fn: Callable[..., Any] | None = None,
    log_top5_coverage_fn: Callable[..., None] | None = None,
    build_references_from_pdf_chunks_fn: Callable[..., list[dict[str, Any]]] | None = None,
    programmatic_insert_dois_fn: Callable[..., str] | None = None,
    align_dois_with_pdf_chunks_fn: Callable[..., str] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    conversation_context: dict[str, Any] | None = None,
    logger: Any,
) -> Any:
    def _cancelled() -> bool:
        if should_cancel is None:
            return False
        try:
            return bool(should_cancel())
        except Exception:
            return False

    if _cancelled():
        yield {"success": False, "cancelled": True, "error": "cancelled"}
        return

    safe_dict_cls = safe_dict_cls or _SafeDict
    escape_braces_fn = escape_braces_fn or _escape_braces
    format_pdf_chunks_evidence_fn = format_pdf_chunks_evidence_fn or format_pdf_chunks_evidence
    build_top5_reference_context_fn = build_top5_reference_context_fn or build_top5_reference_context
    extract_cited_dois_fn = extract_cited_dois_fn or extract_cited_dois_with_logging
    log_top5_coverage_fn = log_top5_coverage_fn or log_top5_coverage
    build_references_from_pdf_chunks_fn = build_references_from_pdf_chunks_fn or build_references_from_pdf_chunks

    evidence_documents = format_pdf_chunks_evidence_fn(pdf_chunks, user_question)
    if not evidence_documents:
        logger.warning("stage4 synthesis skipped because evidence_documents is empty")
        yield {"success": False, "error": "no_pdf_chunks"}
        return

    logger.info(
        "stage4 synthesis start question_chars=%s deep_answer_chars=%s pdf_source_count=%s evidence_chars=%s retrieval_metadata_count=%s",
        len(str(user_question or "")),
        len(str(deep_answer or "")),
        len(pdf_chunks),
        len(evidence_documents),
        len(list((retrieval_results or {}).get("metadatas") or [])),
    )

    try:
        stage4_topk = env_int("QA_STAGE4_REFERENCE_TOPK", 5, minimum=3, maximum=20)
        stage4_min_citations = env_int("QA_STAGE4_MIN_CITATIONS", 10, minimum=1, maximum=20)
        if stage4_min_citations > stage4_topk:
            stage4_min_citations = stage4_topk
        stage4_element_guard = env_bool("QA_STAGE4_ELEMENT_GUARD", True)
        stage4_citation_verify = env_bool(
            "QA_STAGE4_CITATION_VERIFY_AFTER_SYNTHESIS",
            env_bool("CITATION_VERIFY_AFTER_SYNTHESIS", True),
        )
        use_two_stage = env_bool(
            "QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED",
            env_bool("TWO_STAGE_SYNTHESIS", False),
        )
        use_structure_only = env_bool(
            "QA_STAGE4_STRUCTURE_ONLY_MODE",
            env_bool("USE_STRUCTURE_ONLY_SYNTHESIS", False),
        )

        top5_with_scores, top5_reference_list = build_top5_reference_context_fn(
            retrieval_results=retrieval_results,
            logger=logger,
            topk=stage4_topk,
            min_citations=stage4_min_citations,
            element_guard=stage4_element_guard,
            user_question=user_question,
            pdf_chunks=pdf_chunks,
        )
        logger.info(
            "stage4 reference policy topk=%s min_citations=%s element_guard=%s citation_verify=%s two_stage=%s structure_only=%s top_ref_count=%s top_ref_sample=%s",
            stage4_topk,
            stage4_min_citations,
            stage4_element_guard,
            stage4_citation_verify,
            use_two_stage,
            use_structure_only,
            len(top5_with_scores),
            [doi for doi, _score in top5_with_scores[:5]],
        )

        safe_kwargs = safe_dict_cls(
            user_question=escape_braces_fn(user_question),
            deep_answer=escape_braces_fn(deep_answer),
            evidence_documents=escape_braces_fn(evidence_documents),
            top5_references=escape_braces_fn(top5_reference_list),
        )
        prompt = ""
        prompt_mode = "legacy_stage2_prompt"
        if use_two_stage:
            facts = _extract_citable_facts_from_evidence(
                evidence_documents=evidence_documents,
                client=client,
                model=model,
                logger=logger,
            )
            if facts:
                prompt_mode = "two_stage_fact_synthesis"
                prompt = STAGE4_FACT_SYNTHESIS_PROMPT.format_map(
                    safe_dict_cls(
                        user_question=escape_braces_fn(user_question),
                        deep_answer=escape_braces_fn(deep_answer),
                        facts_list=escape_braces_fn(_format_facts_for_prompt(facts)),
                        top5_references=escape_braces_fn(top5_reference_list),
                    )
                )
        if not prompt and use_structure_only and not use_two_stage:
            opening_paragraph, structure_outline = _extract_structure_from_deep_answer(deep_answer)
            if structure_outline:
                prompt_mode = "structure_only"
                prompt = STAGE4_STRUCTURE_ONLY_PROMPT.format_map(
                    safe_dict_cls(
                        user_question=escape_braces_fn(user_question),
                        opening_paragraph=escape_braces_fn(opening_paragraph),
                        structure_outline=escape_braces_fn(structure_outline),
                        evidence_documents=escape_braces_fn(evidence_documents),
                        top5_references=escape_braces_fn(top5_reference_list),
                    )
                )
        if not prompt:
            prompt = stage2_prompt.format_map(safe_kwargs)

        conversation_context_block = _format_conversation_context_for_stage4(conversation_context)
        if conversation_context_block:
            context_log = _summarize_conversation_context_for_log(conversation_context)
            logger.info(
                "stage4 conversation context attached turns=%s summary_present=%s short_summary_present=%s open_threads=%s memory_facts=%s",
                context_log["turns"],
                context_log["summary_present"],
                context_log["short_summary_present"],
                context_log["open_threads"],
                context_log["memory_facts"],
            )
            prompt = (
                "以下是当前会话上下文，仅用于承接当前问题与上文指代，不能覆盖文献证据：\n"
                f"{conversation_context_block}\n\n"
                f"{prompt}"
            )

        logger.info(
            "stage4 prompt prepared mode=%s prompt_chars=%s top_reference_list_chars=%s",
            prompt_mode,
            len(prompt),
            len(top5_reference_list),
        )

        system_prompt = f"""你是一位严谨的材料科学文献分析专家，擅长将专业知识与文献证据有机结合。

## 任务要求：
1. 根据提供的PDF原文证据生成答案
2. 在答案的相关句子末尾插入DOI引用，而不是在答案最后列出
3. 必须至少引用{stage4_min_citations}篇不同的文献（最多{stage4_topk}篇）
4. 每句话只需要插入1个最相关的DOI引用
5. 每个核心要点必须包含机理解释与定量信息
6. 不要输出步骤描述到最终答案

## 引用规则：
- 正确：句子内容 + 空格 + `(doi=xxx)`
- 错误：在答案最后统一列出所有DOI
- 错误：一句话引用多个DOI
"""

        stream = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=4000,
            stream=True,
        )
        final_chunks: list[str] = []
        for chunk in stream:
            if _cancelled():
                yield {"success": False, "cancelled": True, "error": "cancelled"}
                return
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", None)
            if not content:
                continue
            text = str(content)
            final_chunks.append(text)
            yield text

        final_answer = "".join(final_chunks).strip()
        logger.info(
            "stage4 llm stream completed chunk_count=%s answer_chars=%s",
            len(final_chunks),
            len(final_answer),
        )

        def _refresh_cited_dois(answer: str) -> tuple[list[str], set[str]]:
            cited_dois_result = extract_cited_dois_fn(final_answer=answer, logger=logger)
            if isinstance(cited_dois_result, tuple) and len(cited_dois_result) == 2:
                return list(cited_dois_result[0] or []), set(cited_dois_result[1] or set())
            cited_dois = list(cited_dois_result or [])
            return cited_dois, set(cited_dois)

        def _validate_answer(answer: str, *, suffix: str = "") -> str:
            if not stage4_citation_verify or not pdf_chunks:
                return answer
            cleaned_answer, _valid_dois, invalid_dois = _validate_answer_dois_with_pdf_chunks(
                answer=answer,
                pdf_chunks=pdf_chunks,
            )
            if invalid_dois:
                logger.warning("Stage4 removed invalid DOI references%s: %s", suffix, invalid_dois)
            return cleaned_answer

        final_answer = _validate_answer(final_answer)
        cited_dois, cited_dois_set = _refresh_cited_dois(final_answer)
        logger.info(
            "stage4 cited DOI summary before repair count=%s dois=%s",
            len(cited_dois),
            cited_dois[:10],
        )

        if (
            retrieval_results is not None
            and programmatic_insert_dois_fn is not None
            and len(cited_dois) < stage4_min_citations
        ):
            logger.info(
                "stage4 programmatic DOI repair triggered cited_before=%s min_required=%s",
                len(cited_dois),
                stage4_min_citations,
            )
            try:
                repaired_answer = str(
                    programmatic_insert_dois_fn(
                        answer=final_answer,
                        retrieval_results=retrieval_results,
                        similarity_threshold=None,
                        question=user_question,
                    ) or ""
                ).strip()
                if repaired_answer and repaired_answer != final_answer:
                    final_answer = _validate_answer(repaired_answer, suffix=" after programmatic insertion")
                    cited_dois, cited_dois_set = _refresh_cited_dois(final_answer)
                logger.info(
                    "stage4 programmatic DOI repair finished changed=%s cited_after=%s dois=%s",
                    bool(repaired_answer and repaired_answer != final_answer),
                    len(cited_dois),
                    cited_dois[:10],
                )
            except Exception as exc:
                logger.warning("Stage4 programmatic DOI insertion failed: %s", exc)

        if not cited_dois and pdf_chunks and align_dois_with_pdf_chunks_fn is not None:
            logger.info("stage4 fallback DOI alignment triggered because cited_dois is empty")
            try:
                aligned_answer = str(
                    align_dois_with_pdf_chunks_fn(
                        final_answer,
                        pdf_chunks,
                        user_question=user_question,
                    ) or ""
                ).strip()
                if aligned_answer and aligned_answer != final_answer:
                    final_answer = _validate_answer(aligned_answer, suffix=" after fallback alignment")
                    cited_dois, cited_dois_set = _refresh_cited_dois(final_answer)
                logger.info(
                    "stage4 fallback DOI alignment finished changed=%s cited_after=%s dois=%s",
                    bool(aligned_answer and aligned_answer != final_answer),
                    len(cited_dois),
                    cited_dois[:10],
                )
            except Exception as exc:
                logger.warning("Stage4 DOI fallback alignment failed: %s", exc)

        try:
            log_top5_coverage_fn(cited_dois_set=cited_dois_set, top5_with_scores=top5_with_scores, logger=logger)
        except Exception as exc:
            logger.warning("Stage4 top-k coverage logging failed: %s", exc)

        try:
            references = build_references_from_pdf_chunks_fn(cited_dois=cited_dois, pdf_chunks=pdf_chunks)
        except Exception as exc:
            logger.warning("Stage4 reference building failed: %s", exc)
            references = []
        logger.info(
            "stage4 references built count=%s sample=%s",
            len(references),
            [item.get("doi") for item in references[:10]],
        )

        logger.info(
            "stage4 synthesis succeeded final_answer_chars=%s cited_doi_count=%s references=%s",
            len(final_answer),
            len(cited_dois),
            len(references),
        )
        yield {
            "success": True,
            "final_answer": final_answer,
            "references": references,
            "cited_dois": cited_dois,
            "source_count": len(pdf_chunks),
        }
    except Exception as exc:
        logger.error("stage4 synthesis failed: %s", exc, exc_info=True)
        yield {"success": False, "error": str(exc)}


__all__ = [
    "build_references_from_pdf_chunks",
    "extract_cited_dois",
    "format_pdf_chunks_evidence",
    "iter_stage4_synthesis_with_pdf_chunks",
]
