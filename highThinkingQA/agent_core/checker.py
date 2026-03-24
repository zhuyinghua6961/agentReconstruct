"""
引用检查模块（Checker）
作为监管者，仅检查答案中的文献引用是否存在杜撰或数据不符。
不修改答案，只输出检查结果（passed + issues）。
"""

import json
import logging
import re
from typing import Optional

from openai import OpenAI

import config
from agent_core.llm_client import chat_completion, get_llm_client, load_prompt_template
from agent_core.synthesizer import format_retrieved_passages
from retriever.vector_retriever import RetrievedChunk

logger = logging.getLogger(__name__)

_CHECKER_REQUEST_TIMEOUT_SECONDS = 60.0


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
    """
    从 LLM 返回文本中解析 JSON 检查结果。
    容错处理：正则提取 JSON 块，解析失败视为 passed。
    """
    # 尝试直接解析
    text = raw.strip()
    # 去掉可能的 markdown 代码块包裹
    md_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if md_match:
        text = md_match.group(1).strip()

    # 尝试提取 JSON 对象
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


def check_answer(
    question: str,
    answer: str,
    all_retrieved_chunks: list[list[RetrievedChunk]],
    client: Optional[OpenAI] = None,
) -> tuple[bool, list[dict]]:
    """
    检查答案中的文献引用是否准确。

    仅检查两类问题：
    1. 杜撰：答案标注了 [DOI, Section] 但原文中无对应内容
    2. 数据不符：引用的数值/单位/条件与原文不一致

    Args:
        question: 用户原始问题
        answer: 待检查的答案
        all_retrieved_chunks: 检索到的文献文段
        client: OpenAI 客户端

    Returns:
        (passed, issues):
        - passed: True 表示所有引用均准确
        - issues: 问题列表，每项含 claim/citation/problem
    """
    if client is None:
        client = get_llm_client(max_retries=0)

    evidence_index = _build_evidence_index(all_retrieved_chunks)
    precheck_issues = _programmatic_precheck(answer, evidence_index)
    if precheck_issues:
        logger.info(f"Checker 程序化预检查发现 {len(precheck_issues)} 个明显引用问题")
        return False, precheck_issues

    retrieved_passages = format_retrieved_passages(all_retrieved_chunks)

    template = load_prompt_template("check.txt")
    prompt = template.format(
        question=question,
        answer=answer,
        retrieved_passages=retrieved_passages,
    )

    try:
        raw = chat_completion(
            prompt=prompt,
            client=client,
            model=config.CHECKER_MODEL,
            enable_thinking=True,
            max_tokens=4096,
            temperature=0.3,
            timeout_seconds=_CHECKER_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        if _is_timeout_error(exc):
            raise CheckerTimeoutError("checker llm request timed out") from exc
        raise

    passed, issues = _parse_check_result(raw)

    if passed:
        logger.info("Checker 检查通过：未发现引用问题")
    else:
        logger.info(f"Checker 发现 {len(issues)} 个引用问题")
        for i, issue in enumerate(issues):
            logger.debug(
                f"  问题 {i+1}: [{issue.get('citation', '?')}] "
                f"{issue.get('problem', '?')}"
            )

    return passed, issues
