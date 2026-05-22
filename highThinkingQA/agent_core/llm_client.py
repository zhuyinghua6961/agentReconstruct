"""
LLM 客户端封装
统一封装 qwen3-max 的调用逻辑，所有 Agent 模块共用。
支持思考模式（enable_thinking）和流式输出。
"""

import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional, Generator

from openai import AsyncOpenAI, OpenAI

import config

logger = logging.getLogger(__name__)


def _require_api_key(*, api_key: str, env_name: str) -> str:
    value = str(api_key or "").strip()
    if value:
        return value
    raise RuntimeError(f"{env_name} is not configured")


def get_llm_client(*, max_retries: int | None = None) -> OpenAI:
    """获取 LLM API 客户端"""
    api_key = _require_api_key(api_key=config.LLM_API_KEY, env_name="LLM_API_KEY")
    kwargs = {
        "api_key": api_key,
        "base_url": config.LLM_BASE_URL,
    }
    if max_retries is not None:
        kwargs["max_retries"] = int(max_retries)
    return OpenAI(**kwargs)


def get_async_llm_client(*, max_retries: int | None = None) -> AsyncOpenAI:
    """获取异步 LLM API 客户端。"""
    api_key = _require_api_key(api_key=config.LLM_API_KEY, env_name="LLM_API_KEY")
    kwargs = {
        "api_key": api_key,
        "base_url": config.LLM_BASE_URL,
    }
    if max_retries is not None:
        kwargs["max_retries"] = int(max_retries)
    return AsyncOpenAI(**kwargs)


def _build_kwargs(
    messages: list,
    temperature: float,
    max_tokens: int,
    enable_thinking: Optional[bool],
    stream: bool = False,
    model: Optional[str] = None,
) -> dict:
    """
    构建 API 调用参数，统一处理思考模式逻辑。

    流式思考模式下：
    - 通过 extra_body 传递 enable_thinking
    - max_tokens 自动扩大（思考 + 回答都计入 max_tokens）
    - 不主动设 temperature，使用模型默认值
    - DashScope 仅允许流式请求使用 enable_thinking，非流式请求会降级为普通调用
    """
    thinking = enable_thinking if enable_thinking is not None else config.MAIN_LLM_THINKING_ENABLED
    stream_thinking = bool(thinking and stream)

    kwargs = {
        "model": model or config.LLM_MODEL,
        "messages": messages,
    }

    if stream_thinking:
        kwargs["extra_body"] = {"enable_thinking": True}
        # 思考 token + 回答 token 都计入 max_tokens，需要扩容
        # 至少 8192，或原值 ×2，上限 32768（qwen3-max 上限）
        effective_max = max(max_tokens * 2, 8192)
        kwargs["max_tokens"] = min(effective_max, 32768)
    else:
        kwargs["temperature"] = temperature
        kwargs["max_tokens"] = max_tokens

    if stream:
        kwargs["stream"] = True

    return kwargs


def _extract_reasoning(message) -> Optional[str]:
    """从 response message 中提取思考内容（兼容不同返回格式）"""
    reasoning = getattr(message, "reasoning_content", None)
    if not reasoning and hasattr(message, "model_extra"):
        reasoning = (message.model_extra or {}).get("reasoning_content")
    return reasoning


def chat_completion(
    prompt: str,
    system_message: str = "",
    client: Optional[OpenAI] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    enable_thinking: Optional[bool] = None,
    model: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
) -> str:
    """
    调用 LLM 获取回复。

    Args:
        prompt: 用户 prompt
        system_message: 系统 prompt
        client: OpenAI 客户端
        temperature: 温度参数（思考模式下不生效，使用模型默认）
        max_tokens: 回答部分的期望 token 数（思考模式下会自动扩容）
        enable_thinking: 是否开启思考模式，None 则使用全局配置
        model: 指定模型名称，None 则使用全局配置 LLM_MODEL

    Returns:
        LLM 回复文本
    """
    if client is None:
        client = get_llm_client()

    messages = []
    if system_message:
        messages.append({"role": "system", "content": system_message})
    messages.append({"role": "user", "content": prompt})

    kwargs = _build_kwargs(messages, temperature, max_tokens, enable_thinking, model=model)

    if timeout_seconds is not None:
        kwargs["timeout"] = float(timeout_seconds)

    response = client.chat.completions.create(**kwargs)

    # 记录思考内容（调试用）
    reasoning = _extract_reasoning(response.choices[0].message)
    if reasoning:
        logger.debug(f"LLM 思考过程 ({len(reasoning)} chars): {reasoning[:200]}...")

    return response.choices[0].message.content


def chat_completion_stream(
    prompt: str,
    system_message: str = "",
    client: Optional[OpenAI] = None,
    temperature: float = 0.7,
    max_tokens: int = 8192,
    enable_thinking: Optional[bool] = None,
    model: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
) -> Generator[str, None, None]:
    """
    流式调用 LLM，逐块 yield 回复文本。
    仅 yield 最终回答内容，思考过程记录到 debug 日志。

    Args:
        prompt: 用户 prompt
        system_message: 系统 prompt
        client: OpenAI 客户端
        temperature: 温度参数
        max_tokens: 回答部分的期望 token 数
        enable_thinking: 是否开启思考模式
        model: 指定模型名称，None 则使用全局配置 LLM_MODEL

    Yields:
        回复文本片段
    """
    if client is None:
        client = get_llm_client()

    messages = []
    if system_message:
        messages.append({"role": "system", "content": system_message})
    messages.append({"role": "user", "content": prompt})

    kwargs = _build_kwargs(messages, temperature, max_tokens, enable_thinking, stream=True, model=model)
    if timeout_seconds is not None:
        kwargs["timeout"] = float(timeout_seconds)

    response = client.chat.completions.create(**kwargs)

    reasoning_chunks = []
    started_at = time.time()
    first_reasoning_logged = False
    first_content_logged = False

    for chunk in response:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta

        # 收集思考内容（不输出给用户）
        reasoning = getattr(delta, "reasoning_content", None)
        if not reasoning and hasattr(delta, "model_extra"):
            reasoning = (delta.model_extra or {}).get("reasoning_content")
        if reasoning:
            reasoning_chunks.append(reasoning)
            if not first_reasoning_logged:
                first_reasoning_logged = True
                logger.info("LLM stream first_reasoning_chunk elapsed=%.3fs chars=%s", time.time() - started_at, len(reasoning))
            continue

        # 输出最终回答内容
        if delta.content:
            if not first_content_logged:
                first_content_logged = True
                logger.info("LLM stream first_content_chunk elapsed=%.3fs chars=%s", time.time() - started_at, len(delta.content))
            yield delta.content

    # 记录完整思考过程
    if reasoning_chunks:
        full_reasoning = "".join(reasoning_chunks)
        logger.debug(f"LLM 思考过程 ({len(full_reasoning)} chars): {full_reasoning[:300]}...")


def load_prompt_template(template_name: str) -> str:
    """
    加载 prompt 模板文件。

    Args:
        template_name: 模板文件名（不含路径，如 "decompose.txt"）

    Returns:
        模板文本
    """
    path = Path(config.PROMPTS_DIR) / template_name
    return _load_prompt_template_cached(str(path.resolve()))


@lru_cache(maxsize=32)
def _load_prompt_template_cached(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()
