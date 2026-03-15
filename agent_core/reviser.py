"""
答案修改模块（Reviser）
根据 Checker 指出的具体引用问题，定向修改答案中有问题的部分。
不做其他改动，保持答案整体结构不变。
"""

import json
import logging
from typing import Optional

from openai import OpenAI

import config
from agent_core.llm_client import chat_completion, load_prompt_template

logger = logging.getLogger(__name__)


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
    client: Optional[OpenAI] = None,
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
    issues_text = _format_issues(issues)

    template = load_prompt_template("revise.txt")
    prompt = template.format(
        question=question,
        answer=answer,
        issues=issues_text,
    )

    revised = chat_completion(
        prompt=prompt,
        client=client,
        model=config.CHECKER_MODEL,
        enable_thinking=False,
        max_tokens=8192,
        temperature=0.3,
    )

    logger.info(f"Reviser 修改完成: {len(revised)} chars")
    return revised
