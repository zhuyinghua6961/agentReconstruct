"""
子问题预回答模块
LLM 并行预回答每个子问题（A1-A5），
预回答将与子问题拼接后用于向量库检索。
"""

import asyncio
import logging
from typing import Any, AsyncGenerator, Optional

import config
from agent_core.llm_client import get_async_llm_client, load_prompt_template
from agent_core.question_anchor import prepend_question_anchor
from agent_core.thinking import LLM_STAGE_CONTROL, merge_extra_body, resolve_thinking_controls

logger = logging.getLogger(__name__)


def _build_sub_answer_kwargs(prompt: str) -> dict:
    """构建子问题预回答的 API 调用参数（关闭思考模式以加快速度）"""
    controls = resolve_thinking_controls(
        is_thinking_model=config.LLM_IS_THINKING_MODEL,
        thinking_enabled=False,
        stage=LLM_STAGE_CONTROL,
        max_tokens=1024,
        stream=False,
    )
    kwargs = {
        "model": config.LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.5,
        "max_tokens": controls.max_tokens,
    }
    extra_body = merge_extra_body(None, controls)
    if extra_body:
        kwargs["extra_body"] = extra_body
    return kwargs


def pre_answer_sub_question(
    sub_question: str,
    client: Optional[Any] = None,
    *,
    original_question: str | None = None,
) -> str:
    """
    LLM 预回答单个子问题。

    Args:
        sub_question: 子问题
        original_question: 用户原始问题（置于 prompt 顶部锚点，减轻偏题；缺省则仅锚子问题）
        client: OpenAI 客户端

    Returns:
        预回答文本
    """
    if client is None:
        from agent_core.llm_client import get_llm_client
        client = get_llm_client()

    template = load_prompt_template("sub_answer.txt")
    body = template.format(sub_question=sub_question)
    prompt = prepend_question_anchor(body, str(original_question or "").strip() or sub_question)

    kwargs = _build_sub_answer_kwargs(prompt)
    response = client.chat.completions.create(**kwargs)

    return response.choices[0].message.content


async def _async_pre_answer(
    sub_question: str,
    async_client: Any,
    *,
    original_question: str | None = None,
) -> str:
    """异步预回答单个子问题"""
    template = load_prompt_template("sub_answer.txt")
    body = template.format(sub_question=sub_question)
    prompt = prepend_question_anchor(body, str(original_question or "").strip() or sub_question)

    kwargs = _build_sub_answer_kwargs(prompt)
    response = await async_client.chat.completions.create(**kwargs)

    return response.choices[0].message.content


async def pre_answer_all_async(
    sub_questions: list[str],
    async_client: Optional[Any] = None,
    *,
    original_question: str | None = None,
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
        _async_pre_answer(sq, async_client, original_question=original_question)
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
    async_client: Optional[Any] = None,
    *,
    original_question: str | None = None,
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
            answer = await _async_pre_answer(sub_question, async_client, original_question=original_question)
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
    async_client: Optional[Any] = None,
    *,
    original_question: str | None = None,
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
                asyncio.run, pre_answer_all_async(sub_questions, async_client=async_client, original_question=original_question)
            )
            return future.result()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
    else:
        return asyncio.run(pre_answer_all_async(sub_questions, async_client=async_client, original_question=original_question))
