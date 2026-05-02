"""
子问题预回答模块
LLM 并行预回答每个子问题（A1-A5），
预回答将与子问题拼接后用于向量库检索。
"""

import asyncio
import logging
from typing import AsyncGenerator, Optional

from openai import AsyncOpenAI, OpenAI

import config
from agent_core.llm_client import get_async_llm_client, load_prompt_template

logger = logging.getLogger(__name__)


def _build_sub_answer_kwargs(prompt: str) -> dict:
    """构建子问题预回答的 API 调用参数（关闭思考模式以加快速度）"""
    return {
        "model": config.SUB_ANSWER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "extra_body": {"enable_thinking": False},
        "temperature": 0.5,
        "max_tokens": 1024,
    }


def pre_answer_sub_question(
    sub_question: str,
    client: Optional[OpenAI] = None,
) -> str:
    """
    LLM 预回答单个子问题。

    Args:
        sub_question: 子问题
        client: OpenAI 客户端

    Returns:
        预回答文本
    """
    if client is None:
        from agent_core.llm_client import get_llm_client
        client = get_llm_client()

    template = load_prompt_template("sub_answer.txt")
    prompt = template.format(sub_question=sub_question)

    kwargs = _build_sub_answer_kwargs(prompt)
    response = client.chat.completions.create(**kwargs)

    return response.choices[0].message.content


async def _async_pre_answer(
    sub_question: str,
    async_client: AsyncOpenAI,
) -> str:
    """异步预回答单个子问题"""
    template = load_prompt_template("sub_answer.txt")
    prompt = template.format(sub_question=sub_question)

    kwargs = _build_sub_answer_kwargs(prompt)
    response = await async_client.chat.completions.create(**kwargs)

    return response.choices[0].message.content


async def pre_answer_all_async(
    sub_questions: list[str],
    async_client: Optional[AsyncOpenAI] = None,
) -> list[str]:
    """
    异步并行预回答所有子问题。

    Args:
        sub_questions: 子问题列表

    Returns:
        预回答列表，与子问题一一对应
    """
    if async_client is None:
        async_client = get_async_llm_client()

    tasks = [
        _async_pre_answer(sq, async_client)
        for sq in sub_questions
    ]

    answers = await asyncio.gather(*tasks, return_exceptions=True)

    # 处理可能的异常
    results = []
    for i, answer in enumerate(answers):
        if isinstance(answer, Exception):
            logger.error(f"子问题 Q{i+1} 预回答失败: {answer}")
            results.append("")
        else:
            results.append(answer)

    logger.info(f"子问题预回答完成: {len(results)} 个")
    return results


async def iter_pre_answers_async(
    sub_questions: list[str],
    async_client: Optional[AsyncOpenAI] = None,
) -> AsyncGenerator[tuple[int, str], None]:
    """
    按完成顺序返回子问题预回答结果。

    Yields:
        (原始索引, 预回答文本)
    """
    if async_client is None:
        async_client = get_async_llm_client()

    async def _indexed_answer(index: int, sub_question: str) -> tuple[int, str]:
        try:
            answer = await _async_pre_answer(sub_question, async_client)
            return index, answer
        except Exception as exc:
            logger.error(f"子问题 Q{index+1} 预回答失败: {exc}")
            return index, ""

    tasks = [
        asyncio.create_task(_indexed_answer(index, sub_question))
        for index, sub_question in enumerate(sub_questions)
    ]

    try:
        for future in asyncio.as_completed(tasks):
            yield await future
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()


def pre_answer_all(
    sub_questions: list[str],
    async_client: Optional[AsyncOpenAI] = None,
) -> list[str]:
    """
    并行预回答所有子问题（同步入口）。

    Args:
        sub_questions: 子问题列表

    Returns:
        预回答列表
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # 已在异步上下文中，创建新的事件循环线程
        import concurrent.futures
        executor = concurrent.futures.ThreadPoolExecutor()
        try:
            future = executor.submit(
                asyncio.run, pre_answer_all_async(sub_questions, async_client=async_client)
            )
            return future.result()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
    else:
        return asyncio.run(pre_answer_all_async(sub_questions, async_client=async_client))
