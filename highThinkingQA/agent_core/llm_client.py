"""LLM 客户端封装：统一封装 OpenAI-compatible ChatCompletions 调用。"""

import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Generator

import config
from agent_core.openai_compat import AsyncOpenAICompatibleChatClient, OpenAICompatibleChatClient
from agent_core.thinking import (
    LLM_STAGE_CONTROL,
    merge_extra_body,
    resolve_auth_mode,
    resolve_thinking_controls,
)
from agent_core.upstream_auth_logging import (
    log_upstream_auth_failure,
    log_upstream_auth_success_once,
)

logger = logging.getLogger(__name__)


def get_llm_client(*, max_retries: int | None = None) -> OpenAICompatibleChatClient:
    """获取 LLM API 客户端"""
    return OpenAICompatibleChatClient(
        base_url=config.LLM_BASE_URL,
        api_key=config.LLM_API_KEY,
        auth_mode=getattr(config, "LLM_AUTH_MODE", None),
        max_retries=max_retries,
    )


def get_async_llm_client(*, max_retries: int | None = None) -> AsyncOpenAICompatibleChatClient:
    """获取异步 LLM API 客户端。"""
    return AsyncOpenAICompatibleChatClient(
        base_url=config.LLM_BASE_URL,
        api_key=config.LLM_API_KEY,
        auth_mode=getattr(config, "LLM_AUTH_MODE", None),
        max_retries=max_retries,
    )


def _build_kwargs(
    messages: list,
    temperature: float,
    max_tokens: int,
    enable_thinking: Optional[bool],
    stream: bool = False,
    model: Optional[str] = None,
    stage: str = LLM_STAGE_CONTROL,
) -> dict:
    """构建 API 调用参数，统一处理 DeepSeek/OpenAI-compatible thinking 参数。"""
    requested = bool(config.LLM_THINKING_ENABLED and (True if enable_thinking is None else bool(enable_thinking)))
    controls = resolve_thinking_controls(
        is_thinking_model=config.LLM_IS_THINKING_MODEL,
        thinking_enabled=requested,
        stage=stage,
        max_tokens=max_tokens,
        stream=stream,
    )
    kwargs = {
        "model": model or config.LLM_MODEL,
        "messages": messages,
    }

    if controls.enabled:
        kwargs["max_tokens"] = controls.max_tokens
    else:
        kwargs["temperature"] = temperature
        kwargs["max_tokens"] = controls.max_tokens

    extra_body = merge_extra_body(None, controls)
    if extra_body:
        kwargs["extra_body"] = extra_body
    if controls.reasoning_effort:
        kwargs["reasoning_effort"] = controls.reasoning_effort
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
    client: Optional[Any] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    enable_thinking: Optional[bool] = None,
    model: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    stage: str = LLM_STAGE_CONTROL,
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

    kwargs = _build_kwargs(messages, temperature, max_tokens, enable_thinking, model=model, stage=stage)

    if timeout_seconds is not None:
        kwargs["timeout"] = float(timeout_seconds)

    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:
        log_upstream_auth_failure(
            logger=logger,
            service="highThinkingQA",
            endpoint="chat",
            model=str(kwargs.get("model") or ""),
            base_url=config.LLM_BASE_URL,
            api_key=config.LLM_API_KEY,
            exc=exc,
            auth_mode=resolve_auth_mode(getattr(config, "LLM_AUTH_MODE", None)),
        )
        raise
    log_upstream_auth_success_once(
        logger=logger,
        service="highThinkingQA",
        endpoint="chat",
        model=str(kwargs.get("model") or ""),
        base_url=config.LLM_BASE_URL,
        api_key=config.LLM_API_KEY,
        auth_mode=resolve_auth_mode(getattr(config, "LLM_AUTH_MODE", None)),
    )

    reasoning = _extract_reasoning(response.choices[0].message)
    if reasoning:
        logger.debug("LLM non-stream reasoning omitted chars=%s", len(reasoning))

    return response.choices[0].message.content


def chat_completion_stream(
    prompt: str,
    system_message: str = "",
    client: Optional[Any] = None,
    temperature: float = 0.7,
    max_tokens: int = 8192,
    enable_thinking: Optional[bool] = None,
    model: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    stage: str = LLM_STAGE_CONTROL,
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

    kwargs = _build_kwargs(
        messages,
        temperature,
        max_tokens,
        enable_thinking,
        stream=True,
        model=model,
        stage=stage,
    )
    if timeout_seconds is not None:
        kwargs["timeout"] = float(timeout_seconds)

    try:
        response = client.chat.completions.create(**kwargs)
    except Exception as exc:
        log_upstream_auth_failure(
            logger=logger,
            service="highThinkingQA",
            endpoint="chat",
            model=str(kwargs.get("model") or ""),
            base_url=config.LLM_BASE_URL,
            api_key=config.LLM_API_KEY,
            exc=exc,
            auth_mode=resolve_auth_mode(getattr(config, "LLM_AUTH_MODE", None)),
        )
        raise
    log_upstream_auth_success_once(
        logger=logger,
        service="highThinkingQA",
        endpoint="chat",
        model=str(kwargs.get("model") or ""),
        base_url=config.LLM_BASE_URL,
        api_key=config.LLM_API_KEY,
        auth_mode=resolve_auth_mode(getattr(config, "LLM_AUTH_MODE", None)),
    )

    reasoning_chars = 0
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
            reasoning_chars += len(reasoning)
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

    if reasoning_chars:
        logger.debug("LLM stream reasoning omitted chars=%s", reasoning_chars)


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
