from app.integrations.llm.shared_http_pool import FastQASharedUpstreamHttpPool, SharedHttpPoolConfig
from app.integrations.llm.hot_lane_pool import ChatHotLane, ChatHotLanePool
from app.integrations.llm.rerank_session_pool import RerankSessionLane, RerankSessionPool
from app.integrations.llm.upstream_gate import SharedStage2UpstreamGate, Stage2UpstreamGateCancelled
from app.integrations.llm.openai_compat import (
    OpenAICompatChatAdapter,
    OpenAICompatClient,
    build_chat_adapter,
    build_chat_completions_client,
    extract_openai_compatible_text,
    normalize_messages,
    normalize_openai_compatible_endpoint,
)
from app.integrations.llm.thinking import (
    LLM_STAGE_CONTROL,
    LLM_STAGE_DOCUMENT_SUMMARY,
    LLM_STAGE_STAGE4_FINAL_ANSWER,
    LLM_STAGE_TRANSLATION,
    ThinkingControls,
    apply_openai_compatible_thinking,
    auth_headers,
    local_sdk_api_key,
    merge_extra_body,
    resolve_thinking_controls,
)


def is_upstream_pool_timeout(exc: BaseException | None) -> bool:
    if exc is None:
        return False
    try:
        import httpx

        return isinstance(exc, httpx.PoolTimeout)
    except Exception:
        return exc.__class__.__name__ == "PoolTimeout"


def raise_if_upstream_pool_timeout(exc: BaseException | None) -> None:
    if is_upstream_pool_timeout(exc):
        raise exc


def should_use_dashscope_native(*, api_key: str | None, base_url: str | None, transport: str | None = None) -> bool:
    import os

    transport_value = str(transport or os.getenv("LLM_TRANSPORT", "") or "").strip().lower()
    if transport_value in {"dashscope_native", "dashscope", "native"}:
        return True
    if transport_value in {"openai", "openai_sdk", "compatible", "chatopenai", "langchain"}:
        return False
    key = str(api_key or "").strip()
    url = str(base_url or "").strip().lower()
    return bool(key) and "dashscope.aliyuncs.com" in url


__all__ = [
    "FastQASharedUpstreamHttpPool",
    "ChatHotLane",
    "ChatHotLanePool",
    "RerankSessionLane",
    "RerankSessionPool",
    "SharedStage2UpstreamGate",
    "Stage2UpstreamGateCancelled",
    "OpenAICompatChatAdapter",
    "OpenAICompatClient",
    "SharedHttpPoolConfig",
    "build_chat_adapter",
    "build_chat_completions_client",
    "extract_openai_compatible_text",
    "normalize_messages",
    "normalize_openai_compatible_endpoint",
    "LLM_STAGE_CONTROL",
    "LLM_STAGE_DOCUMENT_SUMMARY",
    "LLM_STAGE_STAGE4_FINAL_ANSWER",
    "LLM_STAGE_TRANSLATION",
    "ThinkingControls",
    "apply_openai_compatible_thinking",
    "auth_headers",
    "local_sdk_api_key",
    "merge_extra_body",
    "resolve_thinking_controls",
    "is_upstream_pool_timeout",
    "raise_if_upstream_pool_timeout",
    "should_use_dashscope_native",
]
