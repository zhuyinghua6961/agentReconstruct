"""
综合生成模块
汇总用户原问题 + LLM 直接回答 + 检索到的文献文段，
调用 LLM 生成带引用溯源的最终答案。
支持普通模式和流式输出模式。
"""

import logging
from typing import Optional, Generator

from openai import OpenAI

from agent_core.answer_summary import build_summary_instruction
from agent_core.llm_client import (
    chat_completion,
    chat_completion_stream,
    load_prompt_template,
)
from agent_core.question_anchor import prepend_question_anchor
from retriever.vector_retriever import RetrievedChunk

logger = logging.getLogger(__name__)


def format_retrieved_passages(
    all_chunks: list[list[RetrievedChunk]],
    sub_questions: list[str] = None,
) -> str:
    """
    将检索到的文段格式化为 prompt 文本。

    Args:
        all_chunks: 每个子问题的检索结果列表
        sub_questions: 对应的子问题列表

    Returns:
        格式化后的文段文本
    """
    if not all_chunks:
        return "No relevant literature passages were retrieved."

    parts = []
    seen_texts = set()  # 去重

    for i, chunks in enumerate(all_chunks):
        if sub_questions and i < len(sub_questions):
            parts.append(f"\n=== Passages for Sub-question {i+1}: {sub_questions[i]} ===\n")

        for chunk in chunks:
            text_hash = hash(chunk.text[:200])
            if text_hash in seen_texts:
                continue
            seen_texts.add(text_hash)
            parts.append(chunk.format_for_prompt())

    return "\n\n".join(parts)


def _build_synthesis_prompt(
    question: str,
    direct_answer: str,
    all_retrieved_chunks: list[list[RetrievedChunk]],
    sub_questions: list[str] = None,
    *,
    summary_enabled: bool | None = None,
) -> str:
    """构建综合 prompt（普通和流式共用）"""
    retrieved_passages = format_retrieved_passages(
        all_retrieved_chunks, sub_questions
    )
    template = load_prompt_template("synthesize.txt")
    prompt = template.format(
        question=question,
        direct_answer=direct_answer,
        retrieved_passages=retrieved_passages,
    )
    summary_instruction = build_summary_instruction(enabled=summary_enabled)
    if summary_instruction:
        prompt = f"{prompt}{summary_instruction}"
    return prepend_question_anchor(prompt, question)


def synthesize_answer(
    question: str,
    direct_answer: str,
    all_retrieved_chunks: list[list[RetrievedChunk]],
    sub_questions: list[str] = None,
    client: Optional[OpenAI] = None,
    enable_thinking: Optional[bool] = None,
    summary_enabled: bool | None = None,
) -> str:
    """
    综合生成最终答案（非流式）。
    """
    prompt = _build_synthesis_prompt(
        question,
        direct_answer,
        all_retrieved_chunks,
        sub_questions,
        summary_enabled=summary_enabled,
    )

    answer = chat_completion(
        prompt=prompt,
        client=client,
        temperature=0.5,
        max_tokens=8192,
        enable_thinking=enable_thinking,
    )

    logger.info(f"综合回答生成完成: {len(answer)} chars")
    return answer


def synthesize_answer_stream(
    question: str,
    direct_answer: str,
    all_retrieved_chunks: list[list[RetrievedChunk]],
    sub_questions: list[str] = None,
    client: Optional[OpenAI] = None,
    enable_thinking: Optional[bool] = None,
    summary_enabled: bool | None = None,
) -> Generator[str, None, None]:
    """
    流式综合生成最终答案。
    逐块 yield 回答文本，思考过程不输出。
    """
    prompt = _build_synthesis_prompt(
        question,
        direct_answer,
        all_retrieved_chunks,
        sub_questions,
        summary_enabled=summary_enabled,
    )

    logger.info("开始流式生成综合回答...")

    yield from chat_completion_stream(
        prompt=prompt,
        client=client,
        temperature=0.5,
        max_tokens=16384,
        enable_thinking=enable_thinking,
    )

    logger.info("流式综合回答生成完成")
