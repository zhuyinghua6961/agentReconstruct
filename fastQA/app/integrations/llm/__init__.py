from app.integrations.llm.openai_compat import (
    OpenAICompatChatAdapter,
    OpenAICompatClient,
    build_chat_adapter,
    build_chat_completions_client,
    extract_openai_compatible_text,
    normalize_messages,
    normalize_openai_compatible_endpoint,
)

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
    "OpenAICompatChatAdapter",
    "OpenAICompatClient",
    "build_chat_adapter",
    "build_chat_completions_client",
    "extract_openai_compatible_text",
    "normalize_messages",
    "normalize_openai_compatible_endpoint",
    "should_use_dashscope_native",
]
