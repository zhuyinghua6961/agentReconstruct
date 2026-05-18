from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

import app.core.env_loader as env_loader


REPO_ROOT = Path(__file__).resolve().parents[2]


def _env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].removeprefix("export ").strip()
        keys.add(key)
    return keys


def _reload_config_module():
    import app.core.config as config

    reloaded = importlib.reload(config)
    reloaded.get_settings.cache_clear()
    return reloaded


def test_iter_workspace_env_files_uses_service_config_root(tmp_path, monkeypatch):
    config_root = tmp_path / "config"
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "config.shared.env").write_text("OPENAI_API_KEY=test\n", encoding="utf-8")

    monkeypatch.setenv("FASTQA_SERVICE_CONFIG_ROOT", str(config_root))
    monkeypatch.delenv("FASTQA_ENV_FILE", raising=False)
    monkeypatch.delenv("FASTQA_ENV_FILES", raising=False)
    monkeypatch.delenv("SERVICE_ENV_FILE", raising=False)
    monkeypatch.delenv("SERVICE_ENV_FILES", raising=False)

    reloaded = importlib.reload(env_loader)

    result = reloaded.iter_workspace_env_files()

    assert result[:4] == reloaded.ENV_FILE_CANDIDATES
    assert result[4:10] == reloaded._iter_resource_shared_env_files()
    assert result[10:14] == (
        (config_root / "config.shared.env").resolve(),
        (config_root / "config.secret.env").resolve(),
        (config_root / ".env").resolve(),
        (config_root / "config.env").resolve(),
    )


def test_iter_workspace_env_files_includes_resource_shared_before_service_files(tmp_path, monkeypatch):
    resource_root = tmp_path / "resource"
    shared_root = resource_root / "config" / "shared"
    config_root = resource_root / "config" / "services" / "fastQA"
    shared_root.mkdir(parents=True)
    config_root.mkdir(parents=True)
    for name in ("infrastructure.shared.env", "model-endpoints.shared.env", "infrastructure.secret.env"):
        (shared_root / name).write_text(f"{name}=1\n", encoding="utf-8")
    for name in ("config.env", "config.shared.env", "config.secret.env", ".env"):
        (config_root / name).write_text(f"{name}=1\n", encoding="utf-8")

    monkeypatch.setenv("RESOURCE_ROOT", str(resource_root))
    monkeypatch.delenv("FASTQA_SERVICE_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("SERVICE_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("FASTQA_ENV_FILE", raising=False)
    monkeypatch.delenv("FASTQA_ENV_FILES", raising=False)
    monkeypatch.delenv("SERVICE_ENV_FILE", raising=False)
    monkeypatch.delenv("SERVICE_ENV_FILES", raising=False)

    reloaded = importlib.reload(env_loader)

    result = reloaded.iter_workspace_env_files()

    assert result == (
        *reloaded.ENV_FILE_CANDIDATES,
        (shared_root / "infrastructure.shared.env").resolve(),
        (shared_root / "model-endpoints.shared.env").resolve(),
        (shared_root / "infrastructure.secret.env").resolve(),
        (shared_root / "model-endpoints.secret.env").resolve(),
        (shared_root / "graph.shared.env").resolve(),
        (shared_root / "graph.secret.env").resolve(),
        (config_root / "config.shared.env").resolve(),
        (config_root / "config.secret.env").resolve(),
        (config_root / ".env").resolve(),
        (config_root / "config.env").resolve(),
    )


def test_iter_workspace_env_files_orders_legacy_shared_and_service_layers(tmp_path, monkeypatch):
    workspace_dir = tmp_path / "workspace"
    resource_root = workspace_dir / "resource"
    legacy_shared = workspace_dir / "config.shared.env"
    legacy_secret = workspace_dir / "config.secret.env"
    shared_root = resource_root / "config" / "shared"
    service_root = resource_root / "config" / "services" / "fastQA"
    shared_root.mkdir(parents=True)
    service_root.mkdir(parents=True)
    legacy_shared.write_text("LEGACY_SHARED=1\n", encoding="utf-8")
    legacy_secret.write_text("LEGACY_SECRET=1\n", encoding="utf-8")
    for name in (
        "infrastructure.shared.env",
        "model-endpoints.shared.env",
        "infrastructure.secret.env",
        "model-endpoints.secret.env",
        "graph.shared.env",
        "graph.secret.env",
    ):
        (shared_root / name).write_text(f"{name}=1\n", encoding="utf-8")
    for name in ("config.shared.env", "config.secret.env", ".env", "config.env"):
        (service_root / name).write_text(f"{name}=1\n", encoding="utf-8")

    monkeypatch.setenv("RESOURCE_ROOT", str(resource_root))
    monkeypatch.delenv("FASTQA_SERVICE_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("SERVICE_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("FASTQA_ENV_FILE", raising=False)
    monkeypatch.delenv("FASTQA_ENV_FILES", raising=False)
    monkeypatch.delenv("SERVICE_ENV_FILE", raising=False)
    monkeypatch.delenv("SERVICE_ENV_FILES", raising=False)
    monkeypatch.setattr(env_loader, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(env_loader, "SHARED_ENV_FILE", legacy_shared.resolve())
    monkeypatch.setattr(env_loader, "SECRET_ENV_FILE", legacy_secret.resolve())
    monkeypatch.setattr(env_loader, "ENV_FILE_CANDIDATES", (legacy_shared.resolve(), legacy_secret.resolve()))

    result = env_loader.iter_workspace_env_files()

    assert result == (
        legacy_shared.resolve(),
        legacy_secret.resolve(),
        (shared_root / "infrastructure.shared.env").resolve(),
        (shared_root / "model-endpoints.shared.env").resolve(),
        (shared_root / "infrastructure.secret.env").resolve(),
        (shared_root / "model-endpoints.secret.env").resolve(),
        (shared_root / "graph.shared.env").resolve(),
        (shared_root / "graph.secret.env").resolve(),
        (service_root / "config.shared.env").resolve(),
        (service_root / "config.secret.env").resolve(),
        (service_root / ".env").resolve(),
        (service_root / "config.env").resolve(),
    )


def test_load_workspace_env_preserves_process_env_and_service_config_wins(tmp_path, monkeypatch):
    workspace_dir = tmp_path / "workspace"
    resource_root = workspace_dir / "resource"
    legacy_secret = workspace_dir / "config.secret.env"
    graph_shared = resource_root / "config" / "shared" / "graph.shared.env"
    service_config = resource_root / "config" / "services" / "fastQA" / "config.env"
    graph_shared.parent.mkdir(parents=True)
    service_config.parent.mkdir(parents=True)
    legacy_secret.write_text("FASTQA_GRAPH_KB_TIMEOUT_MS=111\n", encoding="utf-8")
    graph_shared.write_text("FASTQA_GRAPH_KB_TIMEOUT_MS=222\n", encoding="utf-8")
    service_config.write_text("FASTQA_GRAPH_KB_TIMEOUT_MS=333\n", encoding="utf-8")

    monkeypatch.setenv("RESOURCE_ROOT", str(resource_root))
    monkeypatch.delenv("FASTQA_GRAPH_KB_TIMEOUT_MS", raising=False)
    monkeypatch.setattr(env_loader, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(env_loader, "ENV_FILE_CANDIDATES", (legacy_secret.resolve(),))

    env_loader.load_workspace_env(override_existing=False)

    assert os.environ["FASTQA_GRAPH_KB_TIMEOUT_MS"] == "333"

    monkeypatch.setenv("FASTQA_GRAPH_KB_TIMEOUT_MS", "444")
    env_loader.load_workspace_env(override_existing=False)

    assert os.environ["FASTQA_GRAPH_KB_TIMEOUT_MS"] == "444"


def test_settings_resolve_fastqa_port_from_shared_infrastructure(tmp_path, monkeypatch):
    resource_root = tmp_path / "resource"
    shared_root = resource_root / "config" / "shared"
    service_root = resource_root / "config" / "services" / "fastQA"
    shared_root.mkdir(parents=True)
    service_root.mkdir(parents=True)
    (shared_root / "infrastructure.shared.env").write_text(
        "FASTQA_HOST=127.0.0.1\nFASTQA_PORT=18008\n",
        encoding="utf-8",
    )
    for name in ("FASTQA_HOST", "FASTQA_PORT", "FASTQA_FASTAPI_PORT", "FASTAPI_HOST", "FASTAPI_PORT", "BACKEND_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RESOURCE_ROOT", str(resource_root))

    config = _reload_config_module()
    settings = config.get_settings()

    assert settings.host == "127.0.0.1"
    assert settings.port == 18008


def test_config_derives_service_roots_from_resource_root(tmp_path, monkeypatch):
    resource_root = (tmp_path / "resource").resolve()
    (resource_root / "assets" / "prompts").mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("RESOURCE_ROOT", str(resource_root))
    monkeypatch.delenv("FASTQA_SERVICE_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("FASTQA_SERVICE_STATE_ROOT", raising=False)
    monkeypatch.delenv("FASTQA_SERVICE_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("FASTQA_SERVICE_ASSET_ROOT", raising=False)
    monkeypatch.delenv("SERVICE_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("SERVICE_STATE_ROOT", raising=False)
    monkeypatch.delenv("SERVICE_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("SERVICE_ASSET_ROOT", raising=False)
    monkeypatch.delenv("VECTOR_DB_PATH", raising=False)
    monkeypatch.delenv("VECTOR_DB_SUMMARY_PATH", raising=False)
    monkeypatch.delenv("VECTOR_DB_PDF_PATH", raising=False)
    monkeypatch.delenv("VECTOR_DB_COMMUNITY_PATH", raising=False)
    monkeypatch.delenv("VECTOR_DB_MD_PATH", raising=False)
    monkeypatch.delenv("TOPIC_INDEX_PATH", raising=False)
    monkeypatch.delenv("PAPERS_DIR", raising=False)
    monkeypatch.delenv("PDF_CHUNKS_DIR", raising=False)
    monkeypatch.delenv("JSON_DIR", raising=False)
    monkeypatch.delenv("JSON_NORMALIZED_DIR", raising=False)
    monkeypatch.delenv("JSON_SUMMARY_DIR", raising=False)
    monkeypatch.delenv("TRANSLATION_CACHE_DIR", raising=False)
    monkeypatch.delenv("CHAT_JSON_BASE_DIR", raising=False)
    monkeypatch.delenv("MATERIAL_AGENT_PROMPTS_DIR", raising=False)
    monkeypatch.delenv("FASTQA_LOGS_DIR", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("CONVERSATION_EXECUTION_AUTHORITY_TARGET", raising=False)
    monkeypatch.delenv("CONVERSATION_ASSISTANT_WRITE_TARGET", raising=False)
    monkeypatch.delenv("CONVERSATION_OVERLAY_ENABLED", raising=False)
    monkeypatch.delenv("CONVERSATION_USER_WRITE_TARGET", raising=False)
    monkeypatch.delenv("CONVERSATION_CONTEXT_READ_TARGET", raising=False)

    import app.core.config as config

    reloaded = importlib.reload(config)
    reloaded.get_settings.cache_clear()

    assert reloaded.RESOURCE_ROOT == resource_root
    assert Path(reloaded.SERVICE_CONFIG_ROOT) == resource_root / "config/services/fastQA"
    assert Path(reloaded.SERVICE_STATE_ROOT) == resource_root / "state/dev/fastQA"
    assert Path(reloaded.SERVICE_RUNTIME_ROOT) == resource_root / "runtime/dev/fastQA"
    assert Path(reloaded.SERVICE_ASSET_ROOT) == resource_root / "assets"

    settings = reloaded.get_settings()
    assert settings.vector_db_path == (resource_root / "state/dev/fastQA/vector_database").resolve()
    assert settings.papers_dir == (resource_root / "state/dev/fastQA/papers").resolve()
    assert settings.prompts_dir == (resource_root / "assets/prompts").resolve()
    assert settings.logs_dir == (resource_root / "runtime/dev/fastQA/logs").resolve()


def test_settings_resolves_resource_relative_vector_paths_from_workspace(monkeypatch):
    monkeypatch.setenv("VECTOR_DB_PATH", "resource/fastqa/vector_database")
    monkeypatch.setenv("VECTOR_DB_MD_PATH", "resource/fastqa/vector_database_md")

    import app.core.config as config

    reloaded = importlib.reload(config)
    reloaded.get_settings.cache_clear()
    settings = reloaded.get_settings()

    assert settings.vector_db_path == (reloaded.WORKSPACE_DIR / "resource/fastqa/vector_database").resolve()
    assert settings.vector_db_md_path == (reloaded.WORKSPACE_DIR / "resource/fastqa/vector_database_md").resolve()


def test_resolve_resource_root_autodetects_workspace_resource(tmp_path, monkeypatch):
    workspace_dir = tmp_path / "workspace"
    resource_root = workspace_dir / "resource"
    resource_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.delenv("RESOURCE_ROOT", raising=False)
    monkeypatch.setattr(env_loader, "WORKSPACE_DIR", workspace_dir)

    assert env_loader.resolve_resource_root() == resource_root.resolve()


def test_iter_workspace_env_files_falls_back_to_workspace_when_resource_config_missing(tmp_path, monkeypatch):
    workspace_dir = tmp_path / "workspace"
    resource_root = workspace_dir / "resource"
    monkeypatch.delenv("RESOURCE_ROOT", raising=False)
    monkeypatch.delenv("FASTQA_SERVICE_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("SERVICE_CONFIG_ROOT", raising=False)
    monkeypatch.setattr(env_loader, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(env_loader, "LEGACY_ENV_FILE", (workspace_dir / "config.env").resolve())
    monkeypatch.setattr(env_loader, "SHARED_ENV_FILE", (workspace_dir / "config.shared.env").resolve())
    monkeypatch.setattr(env_loader, "SECRET_ENV_FILE", (workspace_dir / "config.secret.env").resolve())
    monkeypatch.setattr(env_loader, "DOTENV_FILE", (workspace_dir / ".env").resolve())
    monkeypatch.setattr(
        env_loader,
        "ENV_FILE_CANDIDATES",
        (
            (workspace_dir / "config.env").resolve(),
            (workspace_dir / "config.shared.env").resolve(),
            (workspace_dir / "config.secret.env").resolve(),
            (workspace_dir / ".env").resolve(),
        ),
    )

    assert env_loader.iter_workspace_env_files() == env_loader.ENV_FILE_CANDIDATES


def test_config_conversation_rollout_flags_keep_execution_authority_coupled(monkeypatch):
    monkeypatch.setenv("CONVERSATION_EXECUTION_AUTHORITY_TARGET", "public_service")
    monkeypatch.setenv("CONVERSATION_ASSISTANT_WRITE_TARGET", "legacy")
    monkeypatch.setenv("CONVERSATION_OVERLAY_ENABLED", "1")
    monkeypatch.delenv("CONVERSATION_USER_WRITE_TARGET", raising=False)
    monkeypatch.delenv("CONVERSATION_CONTEXT_READ_TARGET", raising=False)

    config = _reload_config_module()
    settings = config.get_settings()

    assert settings.conversation_execution_authority_target == "public_service"
    assert settings.conversation_execution_user_write_target == "public_service"
    assert settings.conversation_execution_context_read_target == "public_service"
    assert settings.conversation_assistant_write_target == "legacy"
    assert settings.conversation_overlay_enabled is True


def test_config_chat_persist_enabled_defaults_to_true(monkeypatch):
    monkeypatch.delenv("CHAT_PERSIST_ENABLED", raising=False)

    config = _reload_config_module()
    settings = config.get_settings()

    assert settings.chat_persist_enabled is True


def test_graph_four_route_flags_have_conservative_defaults(monkeypatch):
    monkeypatch.setenv("FASTQA_GRAPH_KB_ENABLED", "true")
    monkeypatch.setenv("FASTQA_GRAPH_KB_V2_ENABLED", "true")
    monkeypatch.setenv("FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED", "true")
    monkeypatch.delenv("FASTQA_GRAPH_DIRECT_ANSWER_MIN_CONFIDENCE", raising=False)
    monkeypatch.delenv("FASTQA_GRAPH_MAX_DOI_CANDIDATES", raising=False)
    monkeypatch.delenv("FASTQA_GRAPH_ALLOW_SUSPICIOUS_DOI_FOR_RAG", raising=False)
    monkeypatch.delenv("FASTQA_GRAPH_COMMUNITY_ROUTE_ENABLED", raising=False)
    monkeypatch.delenv("FASTQA_GRAPH_PRECISE_NUMERIC_ENABLED", raising=False)

    config = _reload_config_module()
    settings = config.get_settings()

    assert settings.graph_kb_enabled is True
    assert settings.graph_kb_v2_enabled is True
    assert settings.graph_kb_rag_injection_enabled is True
    assert settings.graph_direct_answer_min_confidence >= 0.0
    assert settings.graph_max_doi_candidates > 0
    assert settings.graph_community_route_enabled is True


def test_config_split_execution_authority_is_rejected_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CONVERSATION_USER_WRITE_TARGET", "legacy")
    monkeypatch.setenv("CONVERSATION_CONTEXT_READ_TARGET", "public_service")
    monkeypatch.delenv("CONVERSATION_EXECUTION_AUTHORITY_TARGET", raising=False)

    import app.core.config as config

    reloaded = importlib.reload(config)
    reloaded.get_settings.cache_clear()
    with pytest.raises(ValueError, match="split authority"):
        reloaded.get_settings()


def test_fastqa_resource_config_surface_uses_shared_model_embedding_and_rerank_namespaces():
    shared_keys = _env_keys(REPO_ROOT / "resource/config/shared/model-endpoints.shared.env")
    secret_keys = _env_keys(REPO_ROOT / "resource/config/shared/model-endpoints.secret.env.example")
    service_keys = _env_keys(REPO_ROOT / "resource/config/services/fastQA/config.shared.env")
    service_secret_keys = _env_keys(REPO_ROOT / "resource/config/services/fastQA/config.secret.env.example")

    retired_shared = {
        "LLM_PROVIDER",
        "LLM_ENABLE_THINKING",
        "DASHSCOPE_BASE_URL",
        "DASHSCOPE_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "PATENT_OPENAI_BASE_URL",
        "PATENT_OPENAI_MODEL",
        "OPENAI_CONNECT_TIMEOUT_SECONDS",
        "OPENAI_READ_TIMEOUT_SECONDS",
        "OPENAI_STREAM_READ_TIMEOUT_SECONDS",
        "OPENAI_WRITE_TIMEOUT_SECONDS",
        "OPENAI_POOL_TIMEOUT_SECONDS",
        "EMBEDDING_TIMEOUT_SECONDS",
        "PATENT_EMBEDDING_BASE_URL",
        "PATENT_EMBEDDING_MODEL",
        "PATENT_EMBEDDING_MODEL_TYPE",
        "PATENT_EMBEDDING_API_URL",
        "PATENT_EMBEDDING_API_MODEL",
        "PATENT_EMBEDDING_API_TIMEOUT_SECONDS",
        "QA_RETRIEVAL_RERANK_PROVIDER",
        "QA_RETRIEVAL_RERANK_BASE_URL",
        "QA_RETRIEVAL_RERANK_MODEL",
        "QA_RETRIEVAL_RERANK_TIMEOUT",
        "PATENT_STAGE2_RERANK_PROVIDER",
        "PATENT_STAGE2_RERANK_BASE_URL",
        "PATENT_STAGE2_RERANK_MODEL",
        "PATENT_STAGE2_RERANK_TIMEOUT_SECONDS",
        "PATENT_STAGE2_RERANK_ENDPOINT_FAMILY",
        "OCR_BASE_URL",
        "OCR_MODEL",
        "OCR_TIMEOUT_SECONDS",
    }
    assert retired_shared.isdisjoint(shared_keys)
    assert {
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_CONNECT_TIMEOUT_SECONDS",
        "LLM_READ_TIMEOUT_SECONDS",
        "LLM_STREAM_READ_TIMEOUT_SECONDS",
        "LLM_WRITE_TIMEOUT_SECONDS",
        "LLM_POOL_TIMEOUT_SECONDS",
        "LLM_KEEPALIVE_EXPIRY_SECONDS",
        "LLM_MAX_CONNECTIONS",
        "LLM_MAX_KEEPALIVE_CONNECTIONS",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_MODEL",
        "EMBEDDING_MODEL_TYPE",
        "EMBEDDING_API_URL",
        "EMBEDDING_API_MODEL",
        "EMBEDDING_API_TIMEOUT_SECONDS",
        "RERANK_PROVIDER",
        "RERANK_BASE_URL",
        "RERANK_MODEL",
        "RERANK_TIMEOUT_SECONDS",
    } <= shared_keys

    assert {"LLM_API_KEY", "EMBEDDING_API_KEY", "RERANK_API_KEY"} <= secret_keys
    assert {
        "OPENAI_API_KEY",
        "DASHSCOPE_API_KEY",
        "QA_RETRIEVAL_RERANK_API_KEY",
        "PATENT_STAGE2_RERANK_API_KEY",
        "OCR_API_KEY",
    }.isdisjoint(secret_keys)

    assert {
        "QUERY_EXPANSION_MODEL",
        "QA_RETRIEVAL_RERANK_API_KEY",
        "FASTQA_STAGE2_CHAT_WARM_INTERVAL_SECONDS",
        "FASTQA_STAGE2_RERANK_WARMUP_ENABLED",
        "FASTQA_STAGE2_RERANK_WARM_INTERVAL_SECONDS",
        "FASTQA_STAGE2_WARM_ACTIVE_START_HOUR",
        "FASTQA_STAGE2_WARM_ACTIVE_END_HOUR",
        "UPLOAD_QA_USE_SIDECAR",
        "PDF_QA_USE_DEDICATED_LLM",
        "PDF_QA_MODEL",
        "PDF_QA_WARMUP_ENABLED",
    }.isdisjoint(service_keys)
    assert {"FASTQA_LLM_HTTP_SHARED_POOL_ENABLED", "QA_RETRIEVAL_RERANK_CANDIDATES", "ASK_STREAM_MAX_CONCURRENT"} <= service_keys
    assert {"DASHSCOPE_API_KEY", "OCR_API_KEY"}.isdisjoint(service_secret_keys)
