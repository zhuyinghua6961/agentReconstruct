"""
直接回答模块（路径 A）
LLM 直接根据自身知识回答用户原始问题。
"""

import logging
from typing import Optional

from openai import OpenAI

import config
from agent_core.llm_client import chat_completion_stream, load_prompt_template
from agent_core.question_anchor import prepend_question_anchor

logger = logging.getLogger(__name__)


def direct_answer(
    question: str,
    client: Optional[OpenAI] = None,
    enable_thinking: Optional[bool] = None,
) -> str:
    """
    LLM 直接回答用户问题。

    Args:
        question: 用户原始问题
        client: OpenAI 客户端

    Returns:
        LLM 直接回答文本
    """
    template = load_prompt_template("direct_answer.txt")
    prompt = prepend_question_anchor(template.format(question=question), question)

    chunks = list(
        chat_completion_stream(
            prompt=prompt,
            client=client,
            model=config.LLM_MODEL,
            temperature=0.7,
            max_tokens=4096,
            enable_thinking=enable_thinking,
        )
    )
    answer = "".join(chunks)

    logger.info(f"直接回答完成: {len(answer)} chars")
    return answer
