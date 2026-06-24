"""
引用检查模块（Checker）
作为监管者，仅检查答案中的文献引用是否存在杜撰或数据不符。
不修改答案，只输出检查结果（passed + issues）。
"""

import concurrent.futures
import json
import logging
import re
import time
from typing import Any, Optional

import config
from agent_core.llm_client import chat_completion, get_llm_client, load_prompt_template
from agent_core.question_anchor import prepend_question_anchor
from agent_core.synthesizer import format_retrieved_passages
from retriever.vector_retriever import RetrievedChunk

logger = logging.getLogger(__name__)

_CHECKER_MAX_PARALLEL_SLICES = 4
_CHECKER_MAX_CHUNKS_PER_SLICE = 8
_CHECKER_MAX_PASSAGE_CHARS_PER_SLICE = 6000
_CHECKER_MAX_CHUNK_TEXT_CHARS = 900


class CheckerTimeoutError(RuntimeError):
    """Checker request exceeded its per-call timeout."""


def _is_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return "timeout" in name or "timed out" in message or "read operation timed out" in message


_BRACKET_CITATION_PATTERN = re.compile(r"\[(10\.\d{4,9}/[-._;()/:A-Z0-9]+)(?:,\s*([^\]]+))?\]", re.IGNORECASE)


def _parse_check_result(raw: str) -> tuple[bool, list[dict]]:
    """从 LLM 返回文本中解析 JSON 检查结果。"""
    text = raw.strip()
    md_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if md_match:
        text = md_match.group(1).strip()

    json_match = re.search(r"\{[\s\S]*\}", text)
    if not json_match:
        logger.warning("Checker 返回中未找到 JSON，视为 passed")
        return True, []

    try:
        result = json.loads(json_match.group())
    except json.JSONDecodeError as e:
        logger.warning(f"Checker JSON 解析失败: {e}，视为 passed")
        return True, []

    passed = result.get("passed", True)
    issues = result.get("issues", [])

    if not isinstance(passed, bool):
        passed = str(passed).lower() in ("true", "1", "yes")
    if not isinstance(issues, list):
        issues = []

    return passed, issues


def _extract_claim_excerpt(answer: str, citation_start: int) -> str:
    prefix = str(answer or "")[:citation_start].strip()
    if not prefix:
        return ""
    snippet = prefix.splitlines()[-1].strip()
    if len(snippet) <= 160:
        return snippet
    return snippet[-160:].strip()


def _normalize_section_name(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _extract_cited_references(answer: str) -> list[dict[str, str]]:
    references: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in _BRACKET_CITATION_PATTERN.finditer(str(answer or "")):
        doi = str(match.group(1) or "").strip().lower()
        if not doi:
            continue
        section = _normalize_section_name(str(match.group(2) or ""))
        dedupe_key = (doi, section)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        references.append({"doi": doi, "section": section})
    return references


def _build_evidence_index(all_retrieved_chunks: list[list[RetrievedChunk]]) -> dict[str, dict[str, object]]:
    evidence: dict[str, dict[str, object]] = {}
    for chunks in all_retrieved_chunks:
        for chunk in chunks:
            doi = str(getattr(chunk, "doi", "") or "").strip()
            if not doi:
                continue
            key = doi.lower()
            entry = evidence.setdefault(
                key,
                {
                    "doi": doi,
                    "sections": set(),
                },
            )
            section_name = str(getattr(chunk, "section_name", "") or "").strip()
            if section_name:
                entry["sections"].add(section_name)
    return evidence


def _filter_chunks_by_cited_references(
    all_retrieved_chunks: list[list[RetrievedChunk]],
    cited_references: list[dict[str, str]],
) -> tuple[list[list[RetrievedChunk]], int, int]:
    if not cited_references:
        return [], 0, 0

    sections_by_doi: dict[str, set[str]] = {}
    for item in cited_references:
        doi = str(item.get("doi") or "").strip().lower()
        if not doi:
            continue
        section = _normalize_section_name(item.get("section") or "")
        sections_by_doi.setdefault(doi, set())
        if section:
            sections_by_doi[doi].add(section)

    filtered: list[list[RetrievedChunk]] = []
    doi_scoped_chunks = 0
    section_scoped_chunks = 0

    for chunks in all_retrieved_chunks:
        doi_selected = [
            chunk for chunk in chunks
            if str(getattr(chunk, "doi", "") or "").strip().lower() in sections_by_doi
        ]
        if not doi_selected:
            continue
        doi_scoped_chunks += len(doi_selected)

        section_selected: list[RetrievedChunk] = []
        for chunk in doi_selected:
            doi = str(getattr(chunk, "doi", "") or "").strip().lower()
            wanted_sections = sections_by_doi.get(doi) or set()
            if not wanted_sections:
                section_selected.append(chunk)
                continue
            chunk_section = _normalize_section_name(getattr(chunk, "section_name", "") or "")
            if chunk_section in wanted_sections:
                section_selected.append(chunk)

        if section_selected:
            section_scoped_chunks += len(section_selected)
            filtered.append(section_selected)
        else:
            filtered.append(doi_selected)

    return filtered, doi_scoped_chunks, section_scoped_chunks


def _extract_citation_slices(answer: str) -> list[dict[str, object]]:
    slices: list[dict[str, object]] = []
    seen_ranges: set[tuple[int, int]] = set()
    content = str(answer or "")

    for match in _BRACKET_CITATION_PATTERN.finditer(content):
        start, end = match.span()
        block_start = content.rfind("\n\n", 0, start)
        block_start = 0 if block_start < 0 else block_start + 2
        block_end = content.find("\n\n", end)
        block_end = len(content) if block_end < 0 else block_end
        block_range = (block_start, block_end)
        if block_range in seen_ranges:
            continue
        seen_ranges.add(block_range)
        block_text = content[block_start:block_end].strip()
        if not block_text:
            continue
        refs = _extract_cited_references(block_text)
        if not refs:
            continue
        slices.append(
            {
                "answer_block": block_text,
                "references": refs,
            }
        )

    return slices


def _truncate_chunk_text(text: str) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= _CHECKER_MAX_CHUNK_TEXT_CHARS:
        return normalized
    suffix = " ...[truncated for checker]"
    budget = max(1, _CHECKER_MAX_CHUNK_TEXT_CHARS - len(suffix))
    return f"{normalized[:budget].rstrip()}{suffix}"


def _limit_checker_chunks(filtered_chunks: list[list[RetrievedChunk]]) -> tuple[list[list[RetrievedChunk]], dict[str, int]]:
    original_chunk_count = sum(len(chunks) for chunks in filtered_chunks)
    original_text_chars = sum(len(str(getattr(chunk, "text", "") or "")) for chunks in filtered_chunks for chunk in chunks)

    limited: list[list[RetrievedChunk]] = []
    kept_chunk_count = 0
    kept_text_chars = 0

    for chunks in filtered_chunks:
        if kept_chunk_count >= _CHECKER_MAX_CHUNKS_PER_SLICE or kept_text_chars >= _CHECKER_MAX_PASSAGE_CHARS_PER_SLICE:
            break
        limited_group: list[RetrievedChunk] = []
        for chunk in chunks:
            if kept_chunk_count >= _CHECKER_MAX_CHUNKS_PER_SLICE or kept_text_chars >= _CHECKER_MAX_PASSAGE_CHARS_PER_SLICE:
                break
            text_value = _truncate_chunk_text(getattr(chunk, "text", "") or "")
            if not text_value:
                continue
            remaining_chars = _CHECKER_MAX_PASSAGE_CHARS_PER_SLICE - kept_text_chars
            if remaining_chars <= 0:
                break
            if len(text_value) > remaining_chars:
                if kept_chunk_count > 0:
                    break
                text_value = _truncate_chunk_text(text_value[:remaining_chars])
            limited_group.append(
                RetrievedChunk(
                    text=text_value,
                    doi=str(getattr(chunk, "doi", "") or ""),
                    title=str(getattr(chunk, "title", "") or ""),
                    section_name=str(getattr(chunk, "section_name", "") or ""),
                    chunk_index=int(getattr(chunk, "chunk_index", 0) or 0),
                    distance=float(getattr(chunk, "distance", 0.0) or 0.0),
                )
            )
            kept_chunk_count += 1
            kept_text_chars += len(text_value)
        if limited_group:
            limited.append(limited_group)

    return limited, {
        "original_chunk_count": original_chunk_count,
        "kept_chunk_count": kept_chunk_count,
        "original_text_chars": original_text_chars,
        "kept_text_chars": kept_text_chars,
    }


def _programmatic_precheck(answer: str, evidence_index: dict[str, dict[str, object]]) -> list[dict]:
    issues: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for match in _BRACKET_CITATION_PATTERN.finditer(str(answer or "")):
        doi = str(match.group(1) or "").strip()
        if not doi:
            continue
        citation = match.group(0)
        key = doi.lower()
        dedupe_key = (key, citation)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        if key in evidence_index:
            continue

        issues.append(
            {
                "claim": _extract_claim_excerpt(str(answer or ""), match.start()),
                "citation": citation,
                "problem": "fabrication: cited DOI is not present in retrieved literature passages",
            }
        )

    return issues


def _run_checker_slice(
    *,
    question: str,
    answer_block: str,
    filtered_chunks: list[list[RetrievedChunk]],
    client: Any,
) -> tuple[bool, list[dict], dict[str, int]]:
    retrieved_passages = format_retrieved_passages(filtered_chunks)
    template = load_prompt_template("check.txt")
    prompt = prepend_question_anchor(
        template.format(
            question=question,
            answer=answer_block,
            retrieved_passages=retrieved_passages,
        ),
        question,
    )
    raw = chat_completion(
        prompt=prompt,
        client=client,
        model=config.LLM_MODEL,
        enable_thinking=False,
        max_tokens=4096,
        temperature=0.3,
        timeout_seconds=config.LLM_HTTP_READ_TIMEOUT_SECONDS,
    )
    passed, issues = _parse_check_result(raw)
    meta = {
        "prompt_chars": len(prompt),
        "answer_chars": len(answer_block),
        "chunk_count": sum(len(chunks) for chunks in filtered_chunks),
    }
    return passed, issues, meta


def _merge_checker_issues(issue_lists: list[list[dict]]) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for issues in issue_lists:
        for issue in issues:
            citation = str(issue.get("citation") or "").strip()
            claim = str(issue.get("claim") or "").strip()
            problem = str(issue.get("problem") or "").strip()
            key = (citation, claim, problem)
            if key in seen:
                continue
            seen.add(key)
            merged.append(issue)
    return merged


def check_answer(
    question: str,
    answer: str,
    all_retrieved_chunks: list[list[RetrievedChunk]],
    client: Optional[Any] = None,
) -> tuple[bool, list[dict]]:
    """检查答案中的文献引用是否准确。"""
    if client is None:
        client = get_llm_client(max_retries=0)

    evidence_index = _build_evidence_index(all_retrieved_chunks)
    precheck_issues = _programmatic_precheck(answer, evidence_index)
    if precheck_issues:
        logger.info("Checker 程序化预检查发现 %s 个明显引用问题", len(precheck_issues))
        return False, precheck_issues

    cited_references = _extract_cited_references(answer)
    if not cited_references:
        logger.info("Checker 未发现显式 DOI 引用，跳过 LLM 审计")
        return True, []

    citation_slices = _extract_citation_slices(answer)
    if not citation_slices:
        logger.info("Checker 未提取到有效引用块，跳过 LLM 审计")
        return True, []

    original_chunk_count = sum(len(chunks) for chunks in all_retrieved_chunks)
    slice_jobs: list[dict[str, object]] = []
    total_doi_scoped_chunks = 0
    total_section_scoped_chunks = 0
    total_filtered_chunks = 0

    for item in citation_slices:
        filtered_chunks, doi_scoped_chunks, section_scoped_chunks = _filter_chunks_by_cited_references(
            all_retrieved_chunks,
            list(item["references"]),
        )
        limited_chunks, limit_meta = _limit_checker_chunks(filtered_chunks)
        filtered_chunk_count = int(limit_meta["kept_chunk_count"])
        total_doi_scoped_chunks += doi_scoped_chunks
        total_section_scoped_chunks += section_scoped_chunks
        total_filtered_chunks += filtered_chunk_count
        slice_jobs.append(
            {
                "answer_block": item["answer_block"],
                "references": list(item["references"]),
                "filtered_chunks": limited_chunks,
                "limit_meta": limit_meta,
            }
        )

    original_text_chars = sum(len(str(getattr(chunk, "text", "") or "")) for chunks in all_retrieved_chunks for chunk in chunks)
    limited_text_chars = sum(int((job.get("limit_meta") or {}).get("kept_text_chars") or 0) for job in slice_jobs)
    logger.info(
        "Checker parallel slices=%s cited_refs=%s original_chunks=%s doi_scoped_chunks=%s section_scoped_chunks=%s filtered_chunks=%s original_text_chars=%s limited_text_chars=%s",
        len(slice_jobs),
        len(cited_references),
        original_chunk_count,
        total_doi_scoped_chunks,
        total_section_scoped_chunks,
        total_filtered_chunks,
        original_text_chars,
        limited_text_chars,
    )

    llm_started_at = time.time()
    issue_lists: list[list[dict]] = []
    slice_prompt_chars: list[int] = []
    max_workers = max(1, min(_CHECKER_MAX_PARALLEL_SLICES, len(slice_jobs)))
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    try:
        futures = [
            executor.submit(
                _run_checker_slice,
                question=question,
                answer_block=str(job["answer_block"]),
                filtered_chunks=list(job["filtered_chunks"]),
                client=client,
            )
            for job in slice_jobs
        ]
        for future in futures:
            try:
                passed, issues, meta = future.result()
            except Exception as exc:
                if _is_timeout_error(exc):
                    for pending in futures:
                        pending.cancel()
                    raise CheckerTimeoutError("checker llm request timed out") from exc
                raise
            issue_lists.append(issues if not passed else [])
            slice_prompt_chars.append(int(meta.get("prompt_chars") or 0))
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        logger.info("Checker llm elapsed=%.3fs", time.time() - llm_started_at)

    merged_issues = _merge_checker_issues(issue_lists)
    logger.info(
        "Checker parallel result slices=%s merged_issues=%s max_prompt_chars=%s total_prompt_chars=%s",
        len(slice_jobs),
        len(merged_issues),
        max(slice_prompt_chars) if slice_prompt_chars else 0,
        sum(slice_prompt_chars),
    )

    if not merged_issues:
        logger.info("Checker 检查通过：未发现引用问题")
        return True, []

    logger.info("Checker 发现 %s 个引用问题", len(merged_issues))
    for i, issue in enumerate(merged_issues):
        logger.debug(
            "  问题 %s: [%s] %s",
            i + 1,
            issue.get("citation", "?"),
            issue.get("problem", "?"),
        )
    return False, merged_issues
