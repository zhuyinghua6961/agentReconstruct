from __future__ import annotations

import importlib


def _isolate_config_root(monkeypatch, tmp_path):
    resource_root = tmp_path / "resource"
    config_root = resource_root / "config" / "services" / "highThinkingQA"
    shared_root = resource_root / "config" / "shared"
    config_root.mkdir(parents=True, exist_ok=True)
    shared_root.mkdir(parents=True, exist_ok=True)
    empty_env_file = tmp_path / "empty.env"
    empty_env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv("RESOURCE_ROOT", str(resource_root))
    monkeypatch.setenv("HIGHTHINKINGQA_SERVICE_CONFIG_ROOT", str(config_root))
    monkeypatch.setenv("HIGHTHINKINGQA_ENV_FILES", str(empty_env_file))
    monkeypatch.delenv("HIGHTHINKINGQA_ENV_FILE", raising=False)
    monkeypatch.delenv("SERVICE_ENV_FILE", raising=False)
    monkeypatch.delenv("SERVICE_ENV_FILES", raising=False)


def test_config_raises_thinking_service_concurrency_defaults(monkeypatch):
    monkeypatch.delenv("ASK_STREAM_MAX_CONCURRENT", raising=False)
    monkeypatch.delenv("ASK_EXECUTOR_MAX_WORKERS", raising=False)

    import config

    reloaded = importlib.reload(config)

    assert reloaded.ASK_STREAM_MAX_CONCURRENT == 20
    assert reloaded.ASK_EXECUTOR_MAX_WORKERS == 20


def test_config_hardcodes_chat_persistence_enabled(monkeypatch):
    monkeypatch.setenv("CHAT_PERSIST_ENABLED", "0")
    monkeypatch.setenv("CHAT_PERSIST_ASYNC", "0")

    import config

    reloaded = importlib.reload(config)

    assert reloaded.HTTP_SETTINGS.chat_persist_enabled is True
    assert reloaded.HTTP_SETTINGS.chat_persist_async is True


def test_config_prefers_highthinkingqa_embedding_namespace(monkeypatch, tmp_path):
    _isolate_config_root(monkeypatch, tmp_path)
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_API_KEY", "ht-key")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_AUTH_MODE", "x-api-key")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_BASE_URL", "https://ht.example/v1")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_MODEL", "ht-embedding")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_DIMENSIONS", "3072")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_BATCH_SIZE", "8")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_API_RPM", "900")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_API_TPM", "456789")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_CONCURRENCY", "3")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_MAX_CONCURRENT_REQUESTS", "5")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_MAX_INPUT_TOKENS", "6000")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_MAX_RETRIES", "6")
    monkeypatch.setenv("HIGHTHINKINGQA_EMBEDDING_QUEUE_SIZE", "111")
    monkeypatch.setenv("EMBEDDING_API_KEY", "shared-key")
    monkeypatch.setenv("EMBEDDING_AUTH_MODE", "authorization")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://shared.example/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "shared-embedding")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "2048")
    monkeypatch.setenv("EMBED_BATCH_SIZE", "10")
    monkeypatch.setenv("EMBED_API_RPM", "1800")
    monkeypatch.setenv("EMBED_API_TPM", "1200000")
    monkeypatch.setenv("EMBED_CONCURRENCY", "2")
    monkeypatch.setenv("EMBED_MAX_CONCURRENT_REQUESTS", "4")
    monkeypatch.setenv("EMBED_MAX_INPUT_TOKENS", "8000")
    monkeypatch.setenv("EMBED_MAX_RETRIES", "5")
    monkeypatch.setenv("EMBED_QUEUE_SIZE", "200")

    import config

    reloaded = importlib.reload(config)

    assert reloaded.EMBEDDING_API_KEY == "ht-key"
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_AUTH_MODE == "x-api-key"
    assert reloaded.EMBEDDING_BASE_URL == "https://ht.example/v1"
    assert reloaded.EMBEDDING_MODEL == "ht-embedding"
    assert reloaded.EMBEDDING_DIMENSIONS == 3072
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_BATCH_SIZE == 8
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_API_RPM == 900
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_API_TPM == 456789
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_CONCURRENCY == 3
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_MAX_CONCURRENT_REQUESTS == 5
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_MAX_INPUT_TOKENS == 6000
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_MAX_RETRIES == 6
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_QUEUE_SIZE == 111


def test_config_ignores_legacy_embedding_and_llm_aliases(monkeypatch, tmp_path):
    _isolate_config_root(monkeypatch, tmp_path)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_IS_THINKING_MODEL", raising=False)
    monkeypatch.delenv("LLM_THINKING_ENABLED", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_EMBEDDING_AUTH_MODE", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_EMBEDDING_DIMENSIONS", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_EMBEDDING_BATCH_SIZE", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_EMBEDDING_API_RPM", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_EMBEDDING_API_TPM", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_EMBEDDING_CONCURRENCY", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_EMBEDDING_MAX_CONCURRENT_REQUESTS", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_EMBEDDING_MAX_INPUT_TOKENS", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_EMBEDDING_MAX_RETRIES", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_EMBEDDING_QUEUE_SIZE", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "openai-model")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dash-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dash.example/v1")
    monkeypatch.setenv("DASHSCOPE_MODEL", "dash-model")
    monkeypatch.setenv("EMBEDDING_API_KEY", "shared-key")
    monkeypatch.setenv("EMBEDDING_AUTH_MODE", "authorization")
    monkeypatch.setenv("EMBEDDING_BASE_URL", "https://shared.example/v1")
    monkeypatch.setenv("EMBEDDING_MODEL", "shared-embedding")
    monkeypatch.setenv("EMBEDDING_DIMENSIONS", "999")
    monkeypatch.setenv("EMBED_BATCH_SIZE", "99")
    monkeypatch.setenv("EMBED_API_RPM", "99")
    monkeypatch.setenv("EMBED_API_TPM", "99")
    monkeypatch.setenv("EMBED_CONCURRENCY", "99")
    monkeypatch.setenv("EMBED_MAX_CONCURRENT_REQUESTS", "99")
    monkeypatch.setenv("EMBED_MAX_INPUT_TOKENS", "99")
    monkeypatch.setenv("EMBED_MAX_RETRIES", "99")
    monkeypatch.setenv("EMBED_QUEUE_SIZE", "99")
    monkeypatch.setenv("OCR_BASE_URL", "https://ocr.example/v1")
    monkeypatch.setenv("OCR_MODEL", "ocr-model")
    monkeypatch.setenv("OCR_API_KEY", "ocr-key")
    monkeypatch.setenv("OCR_CONCURRENCY", "99")
    monkeypatch.setenv("OCR_MAX_CONCURRENT_REQUESTS", "99")
    monkeypatch.setenv("OCR_PAGES_PER_BATCH", "99")
    monkeypatch.setenv("OCR_MAX_RETRIES", "99")
    monkeypatch.setenv("OCR_RETRY_BASE", "99")

    import config

    reloaded = importlib.reload(config)

    assert reloaded.LLM_API_KEY == ""
    assert reloaded.LLM_BASE_URL == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert reloaded.LLM_MODEL == "qwen3-max"
    assert reloaded.EMBEDDING_API_KEY == ""
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_AUTH_MODE == "bearer"
    assert reloaded.EMBEDDING_BASE_URL == "http://127.0.0.1:8014/v1"
    assert reloaded.EMBEDDING_MODEL == "qwen3-embedding-8b"
    assert reloaded.EMBEDDING_DIMENSIONS == 4096
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_BATCH_SIZE == 10
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_API_RPM == 1800
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_API_TPM == 1_200_000
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_CONCURRENCY == 2
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_MAX_CONCURRENT_REQUESTS == 4
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_MAX_INPUT_TOKENS == 8000
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_MAX_RETRIES == 5
    assert reloaded.HIGHTHINKINGQA_EMBEDDING_QUEUE_SIZE == 200
    assert reloaded.VLM_API_KEY == ""
    assert reloaded.VLM_BASE_URL == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert reloaded.VLM_MODEL == "qwen-vl-ocr-2025-11-20"
    assert reloaded.VLM_CONCURRENCY == 40
    assert reloaded.VLM_MAX_CONCURRENT_REQUESTS == 40
    assert reloaded.VLM_PAGES_PER_BATCH == 3
    assert reloaded.VLM_MAX_RETRIES == 5
    assert reloaded.VLM_RETRY_BASE == 3
    assert reloaded.LLM_IS_THINKING_MODEL is False
    assert reloaded.LLM_THINKING_ENABLED is False
    assert reloaded.MAIN_LLM_THINKING_ENABLED is False


def test_config_reads_simplified_llm_thinking_flags(monkeypatch, tmp_path):
    _isolate_config_root(monkeypatch, tmp_path)
    monkeypatch.setenv("LLM_IS_THINKING_MODEL", "true")
    monkeypatch.setenv("LLM_THINKING_ENABLED", "true")

    import config

    reloaded = importlib.reload(config)

    assert reloaded.LLM_IS_THINKING_MODEL is True
    assert reloaded.LLM_THINKING_ENABLED is True
    assert reloaded.MAIN_LLM_THINKING_ENABLED is True
    assert reloaded.DIRECT_STAGE_THINKING_ENABLED is False
    assert reloaded.DECOMPOSE_STAGE_THINKING_ENABLED is False
