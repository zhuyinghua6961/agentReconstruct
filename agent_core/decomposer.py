"""
查询分解模块
LLM 将用户问题拆分为 5 个独立子问题。
"""

import json
import logging
import re
from typing import Optional

from openai import OpenAI

import config
from agent_core.llm_client import chat_completion, load_prompt_template

logger = logging.getLogger(__name__)


def decompose_question(
    question: str,
    client: Optional[OpenAI] = None,
    num_sub_questions: Optional[int] = None,
    enable_thinking: Optional[bool] = None,
) -> list[str]:
    """
    将用户问题分解为 5 个子问题。

    Args:
        question: 用户原始问题
        client: OpenAI 客户端

    Returns:
        5 个子问题的列表
    """
    template = load_prompt_template("decompose.txt")
    prompt = template.format(question=question)

    target_count = int(num_sub_questions) if num_sub_questions is not None else int(config.NUM_SUB_QUESTIONS)
    if target_count <= 0:
        target_count = 1

    response = chat_completion(
        prompt=prompt,
        client=client,
        model=config.DECOMPOSE_MODEL,
        temperature=0.3,  # 低温度保证输出格式稳定
        max_tokens=2048,
        enable_thinking=enable_thinking,
    )

    # 解析 JSON 数组
    sub_questions = _parse_sub_questions(response)

    # 确保恰好 5 个子问题
    if len(sub_questions) < target_count:
        # 不够的话补充通用子问题
        while len(sub_questions) < target_count:
            sub_questions.append(
                f"What are the latest research findings related to: {question}?"
            )
    elif len(sub_questions) > target_count:
        sub_questions = sub_questions[:target_count]

    logger.info(f"查询分解完成: {len(sub_questions)} 个子问题")
    for i, sq in enumerate(sub_questions):
        logger.debug(f"  Q{i+1}: {sq}")

    return sub_questions


def _parse_sub_questions(response: str) -> list[str]:
    """解析 LLM 返回的子问题 JSON 数组"""
    # 尝试直接解析 JSON
    try:
        # 提取 JSON 数组部分（可能包含在 markdown 代码块中）
        json_match = re.search(r"\[.*?\]", response, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            if isinstance(result, list) and all(isinstance(q, str) for q in result):
                return result
    except (json.JSONDecodeError, TypeError):
        pass

    # JSON 解析失败，尝试按行解析
    lines = response.strip().split("\n")
    questions = []
    for line in lines:
        line = line.strip()
        # 去除编号前缀如 "1.", "1)", "- " 等
        line = re.sub(r"^[\d]+[.)]\s*", "", line)
        line = re.sub(r"^[-*]\s*", "", line)
        line = line.strip('"\'')
        if line and len(line) > 10:  # 过滤太短的行
            questions.append(line)

    return questions
