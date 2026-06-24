"""
答案修改模块（Reviser）
根据 Checker 指出的具体引用问题，定向修改答案中有问题的部分。
不做其他改动，保持答案整体结构不变。
"""

import json
import logging
from typing import Any, Optional

import config
from agent_core.llm_client import chat_completion, get_llm_client, load_prompt_template
from agent_core.question_anchor import prepend_question_anchor

logger = logging.getLogger(__name__)


class ReviserTimeoutError(RuntimeError):
    """Reviser request exceeded its per-call timeout."""


def _is_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return "timeout" in name or "timed out" in message or "read operation timed out" in message



def _format_issues(issues: list[dict]) -> str:
    """将 issues 列表格式化为可读文本，供 Reviser 参考。"""
    parts = []
    for i, issue in enumerate(issues, 1):
        claim = issue.get("claim", "N/A")
        citation = issue.get("citation", "N/A")
        problem = issue.get("problem", "N/A")
        parts.append(
            f"Issue {i}:\n"
            f"  Claim in answer: \"{claim}\"\n"
            f"  Citation: {citation}\n"
            f"  Problem: {problem}"
        )
    return "\n\n".join(parts)


def revise_answer(
    question: str,
    answer: str,
    issues: list[dict],
    client: Optional[Any] = None,
) -> str:
    """
    根据 Checker 指出的问题修改答案。

    仅针对 issues 列表中的具体问题做定向修复：
    - 杜撰的引用：删除引用或改为通识表述
    - 数据不符：纠正为原文实际数据

    Args:
        question: 用户原始问题
        answer: 当前有问题的答案
        issues: Checker 返回的问题列表
        client: OpenAI 客户端

    Returns:
        修正后的完整答案
    """
    if client is None:
        client = get_llm_client(max_retries=0)

    issues_text = _format_issues(issues)

    template = load_prompt_template("revise.txt")
    prompt = prepend_question_anchor(
        template.format(
            question=question,
            answer=answer,
            issues=issues_text,
        ),
        question,
    )

    try:
        revised = chat_completion(
            prompt=prompt,
            client=client,
            model=config.LLM_MODEL,
            enable_thinking=False,
            max_tokens=8192,
            temperature=0.3,
            timeout_seconds=config.LLM_HTTP_READ_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        if _is_timeout_error(exc):
            raise ReviserTimeoutError("reviser llm request timed out") from exc
        raise

    logger.info(f"Reviser 修改完成: {len(revised)} chars")
    return revised
