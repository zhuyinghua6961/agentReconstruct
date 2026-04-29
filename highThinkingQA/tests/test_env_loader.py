from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

import env_loader


def _reload_config_module():
    import config

    return importlib.reload(config)


def test_load_workspace_env_respects_file_precedence(tmp_path, monkeypatch):
    legacy = tmp_path / "config.env"
    shared = tmp_path / "config.shared.env"
    secret = tmp_path / "config.secret.env"
    dotenv = tmp_path / ".env"

    legacy.write_text("FOO=legacy\nBAR=legacy\n", encoding="utf-8")
    shared.write_text("BAR=shared\nBAZ=shared\n", encoding="utf-8")
    secret.write_text("BAZ=secret\nTOKEN=secret\n", encoding="utf-8")
    dotenv.write_text("TOKEN=dotenv\nAPP_PORT=9001\n", encoding="utf-8")

    monkeypatch.setattr(env_loader, "LEGACY_ENV_FILE", legacy)
    monkeypatch.setattr(env_loader, "SHARED_ENV_FILE", shared)
    monkeypatch.setattr(env_loader, "SECRET_ENV_FILE", secret)
    monkeypatch.setattr(env_loader, "DOTENV_FILE", dotenv)
    monkeypatch.setattr(env_loader, "ENV_FILE_CANDIDATES", (legacy, shared, secret, dotenv))
    monkeypatch.setattr(env_loader, "_resolve_config_root", lambda: None)

    for key in ("FOO", "BAR", "BAZ", "TOKEN", "APP_PORT"):
        monkeypatch.delenv(key, raising=False)

    loaded = env_loader.load_workspace_env(override_existing=False)

    assert loaded == (legacy, shared, secret, dotenv)
    assert env_loader.iter_workspace_env_files() == (legacy, shared, secret, dotenv)
    assert env_loader.os.environ["FOO"] == "legacy"
    assert env_loader.os.environ["BAR"] == "shared"
    assert env_loader.os.environ["BAZ"] == "secret"
    assert env_loader.os.environ["TOKEN"] == "dotenv"
    assert env_loader.os.environ["APP_PORT"] == "9001"


def test_iter_workspace_env_files_uses_service_config_root(tmp_path, monkeypatch):
    config_root = tmp_path / "config"
    config_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HIGHTHINKINGQA_SERVICE_CONFIG_ROOT", str(config_root))
    monkeypatch.delenv("HIGHTHINKINGQA_ENV_FILE", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_ENV_FILES", raising=False)
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
    config_root = resource_root / "config" / "services" / "highThinkingQA"
    shared_root.mkdir(parents=True)
    config_root.mkdir(parents=True)
    for name in ("infrastructure.shared.env", "model-endpoints.shared.env", "infrastructure.secret.env"):
        (shared_root / name).write_text(f"{name}=1\n", encoding="utf-8")
    for name in ("config.env", "config.shared.env", "config.secret.env", ".env"):
        (config_root / name).write_text(f"{name}=1\n", encoding="utf-8")

    monkeypatch.setenv("RESOURCE_ROOT", str(resource_root))
    monkeypatch.delenv("HIGHTHINKINGQA_SERVICE_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("SERVICE_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_ENV_FILE", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_ENV_FILES", raising=False)
    monkeypatch.delenv("SERVICE_ENV_FILE", raising=False)
    monkeypatch.delenv("SERVICE_ENV_FILES", raising=False)

    reloaded = importlib.reload(env_loader)

    assert reloaded.iter_workspace_env_files() == (
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
    service_root = resource_root / "config" / "services" / "highThinkingQA"
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
    monkeypatch.delenv("HIGHTHINKINGQA_SERVICE_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("SERVICE_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_ENV_FILE", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_ENV_FILES", raising=False)
    monkeypatch.delenv("SERVICE_ENV_FILE", raising=False)
    monkeypatch.delenv("SERVICE_ENV_FILES", raising=False)
    monkeypatch.setattr(env_loader, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(env_loader, "REPO_ROOT", workspace_dir)
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
    shared_file = resource_root / "config" / "shared" / "model-endpoints.shared.env"
    service_config = resource_root / "config" / "services" / "highThinkingQA" / "config.env"
    shared_file.parent.mkdir(parents=True)
    service_config.parent.mkdir(parents=True)
    legacy_secret.write_text("HT_QA_CACHE_EPOCH=legacy\n", encoding="utf-8")
    shared_file.write_text("HT_QA_CACHE_EPOCH=shared\n", encoding="utf-8")
    service_config.write_text("HT_QA_CACHE_EPOCH=service\n", encoding="utf-8")

    monkeypatch.setenv("RESOURCE_ROOT", str(resource_root))
    monkeypatch.delenv("HT_QA_CACHE_EPOCH", raising=False)
    monkeypatch.setattr(env_loader, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(env_loader, "REPO_ROOT", workspace_dir)
    monkeypatch.setattr(env_loader, "ENV_FILE_CANDIDATES", (legacy_secret.resolve(),))

    env_loader.load_workspace_env(override_existing=False)

    assert os.environ["HT_QA_CACHE_EPOCH"] == "service"

    monkeypatch.setenv("HT_QA_CACHE_EPOCH", "process")
    env_loader.load_workspace_env(override_existing=False)

    assert os.environ["HT_QA_CACHE_EPOCH"] == "process"


def test_http_settings_resolve_port_from_shared_infrastructure(tmp_path, monkeypatch):
    resource_root = tmp_path / "resource"
    shared_root = resource_root / "config" / "shared"
    service_root = resource_root / "config" / "services" / "highThinkingQA"
    shared_root.mkdir(parents=True)
    service_root.mkdir(parents=True)
    (shared_root / "infrastructure.shared.env").write_text(
        "HIGHTHINKINGQA_HOST=127.0.0.1\nHIGHTHINKINGQA_PORT=18009\n",
        encoding="utf-8",
    )
    for name in ("HIGHTHINKINGQA_HOST", "HIGHTHINKINGQA_PORT", "APP_HOST", "APP_PORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RESOURCE_ROOT", str(resource_root))

    config = _reload_config_module()

    assert config.HTTP_SETTINGS.app_host == "127.0.0.1"
    assert config.HTTP_SETTINGS.app_port == 18009


def test_resolve_resource_root_defaults_to_repo_resource(monkeypatch):
    monkeypatch.delenv("RESOURCE_ROOT", raising=False)

    reloaded = importlib.reload(env_loader)

    assert reloaded.resolve_resource_root() == (reloaded.REPO_ROOT / "resource").resolve()


def test_config_uses_env_values_and_resolves_relative_paths(monkeypatch):
    state_root = Path("/tmp/highthinking-state")
    asset_root = Path("/tmp/highthinking-assets")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "masked")
    monkeypatch.setenv("LLM_MODEL", "unit-test-model")
    monkeypatch.setenv("HIGHTHINKINGQA_SERVICE_STATE_ROOT", str(state_root))
    monkeypatch.setenv("HIGHTHINKINGQA_SERVICE_ASSET_ROOT", str(asset_root))
    monkeypatch.setenv("PAPERS_DIR", "tmp-papers")
    monkeypatch.setenv("PROMPTS_DIR", "custom-prompts")
    monkeypatch.setenv("CHROMA_PERSIST_DIR", "custom-vdb")

    import config

    reloaded = importlib.reload(config)

    assert reloaded.DASHSCOPE_API_KEY == "masked"
    assert reloaded.LLM_MODEL == "unit-test-model"
    assert reloaded.PAPERS_DIR == str((state_root / "tmp-papers").resolve())
    assert reloaded.PROMPTS_DIR == str((asset_root / "custom-prompts").resolve())
    assert reloaded.CHROMA_PERSIST_DIR == str((state_root / "custom-vdb").resolve())


def test_config_derives_service_roots_from_resource_root(tmp_path, monkeypatch):
    resource_root = (tmp_path / "resource").resolve()
    (resource_root / "assets" / "prompts").mkdir(parents=True, exist_ok=True)
    service_config_root = resource_root / "config" / "services" / "highThinkingQA"
    service_config_root.mkdir(parents=True, exist_ok=True)
    (service_config_root / "config.shared.env").write_text(
        "\n".join(
            (
                "PAPERS_DIR=papers",
                "UPLOAD_DIR=uploads",
                "CHAT_JSON_BASE_DIR=data/conversations",
                "PROMPTS_DIR=prompts",
                "CHROMA_PERSIST_DIR=vectordb",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("RESOURCE_ROOT", str(resource_root))
    monkeypatch.delenv("HIGHTHINKINGQA_SERVICE_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_SERVICE_STATE_ROOT", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("HIGHTHINKINGQA_SERVICE_ASSET_ROOT", raising=False)
    monkeypatch.delenv("SERVICE_CONFIG_ROOT", raising=False)
    monkeypatch.delenv("SERVICE_STATE_ROOT", raising=False)
    monkeypatch.delenv("SERVICE_RUNTIME_ROOT", raising=False)
    monkeypatch.delenv("SERVICE_ASSET_ROOT", raising=False)
    monkeypatch.delenv("PAPERS_DIR", raising=False)
    monkeypatch.delenv("PROMPTS_DIR", raising=False)
    monkeypatch.delenv("UPLOAD_DIR", raising=False)
    monkeypatch.delenv("CHAT_JSON_BASE_DIR", raising=False)
    monkeypatch.setenv("CHROMA_PERSIST_DIR", "")
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("CONVERSATION_EXECUTION_AUTHORITY_TARGET", raising=False)
    monkeypatch.delenv("CONVERSATION_ASSISTANT_WRITE_TARGET", raising=False)
    monkeypatch.delenv("CONVERSATION_OVERLAY_ENABLED", raising=False)
    monkeypatch.delenv("CONVERSATION_USER_WRITE_TARGET", raising=False)
    monkeypatch.delenv("CONVERSATION_CONTEXT_READ_TARGET", raising=False)

    import config

    reloaded = importlib.reload(config)

    assert reloaded.RESOURCE_ROOT == resource_root
    assert Path(reloaded.SERVICE_CONFIG_ROOT) == resource_root / "config/services/highThinkingQA"
    assert Path(reloaded.SERVICE_STATE_ROOT) == resource_root / "state/dev/highThinkingQA"
    assert Path(reloaded.SERVICE_RUNTIME_ROOT) == resource_root / "runtime/dev/highThinkingQA"
    assert Path(reloaded.SERVICE_ASSET_ROOT) == resource_root / "assets"
    assert reloaded.PAPERS_DIR == str((resource_root / "state/dev/highThinkingQA/papers").resolve())
    assert reloaded.UPLOAD_DIR == str((resource_root / "state/dev/highThinkingQA/uploads").resolve())
    assert reloaded.CHAT_JSON_BASE_DIR == str((resource_root / "state/dev/highThinkingQA/data/conversations").resolve())
    assert reloaded.PROMPTS_DIR == str((resource_root / "assets/prompts").resolve())


def test_config_absolute_path_overrides_are_preserved(tmp_path, monkeypatch):
    state_root = (tmp_path / "state").resolve()
    asset_root = (tmp_path / "assets").resolve()
    explicit_papers = (tmp_path / "external" / "papers").resolve()
    explicit_prompts = (tmp_path / "external" / "prompts").resolve()

    monkeypatch.setenv("HIGHTHINKINGQA_SERVICE_STATE_ROOT", str(state_root))
    monkeypatch.setenv("HIGHTHINKINGQA_SERVICE_ASSET_ROOT", str(asset_root))
    monkeypatch.setenv("PAPERS_DIR", str(explicit_papers))
    monkeypatch.setenv("PROMPTS_DIR", str(explicit_prompts))

    import config

    reloaded = importlib.reload(config)

    assert reloaded.PAPERS_DIR == str(explicit_papers)
    assert reloaded.PROMPTS_DIR == str(explicit_prompts)


def test_config_maps_service_roots_to_state_runtime_and_assets(tmp_path, monkeypatch):
    state_root = (tmp_path / "state").resolve()
    runtime_root = (tmp_path / "runtime").resolve()
    asset_root = (tmp_path / "assets").resolve()
    (asset_root / "prompts").mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HIGHTHINKINGQA_SERVICE_STATE_ROOT", str(state_root))
    monkeypatch.setenv("HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setenv("HIGHTHINKINGQA_SERVICE_ASSET_ROOT", str(asset_root))
    monkeypatch.setenv("PAPERS_DIR", "papers-custom")
    monkeypatch.delenv("PROMPTS_DIR", raising=False)
    monkeypatch.delenv("UPLOAD_DIR", raising=False)
    monkeypatch.delenv("CHAT_JSON_BASE_DIR", raising=False)
    monkeypatch.setenv("CHROMA_PERSIST_DIR", "")

    import config

    reloaded = importlib.reload(config)

    assert Path(reloaded.SERVICE_STATE_ROOT) == state_root
    assert Path(reloaded.SERVICE_RUNTIME_ROOT) == runtime_root
    assert Path(reloaded.SERVICE_ASSET_ROOT) == asset_root
    assert reloaded.PAPERS_DIR == str((state_root / "papers-custom").resolve())
    assert reloaded.UPLOAD_DIR == str((state_root / "uploads").resolve())
    assert reloaded.CHAT_JSON_BASE_DIR == str((state_root / "data/conversations").resolve())
    assert reloaded.CHROMA_PERSIST_DIR == str((state_root / "vectordb").resolve())
    assert reloaded.PROMPTS_DIR == str((asset_root / "prompts").resolve())
    assert reloaded.APP_RUNTIME_ROOT == str(runtime_root)
    assert reloaded.APP_RUNTIME_LOGS_DIR == str((runtime_root / "logs").resolve())


def test_config_conversation_rollout_flags_keep_execution_authority_coupled(monkeypatch):
    monkeypatch.setenv("CONVERSATION_EXECUTION_AUTHORITY_TARGET", "public_service")
    monkeypatch.setenv("CONVERSATION_ASSISTANT_WRITE_TARGET", "legacy")
    monkeypatch.setenv("CONVERSATION_OVERLAY_ENABLED", "1")
    monkeypatch.delenv("CONVERSATION_USER_WRITE_TARGET", raising=False)
    monkeypatch.delenv("CONVERSATION_CONTEXT_READ_TARGET", raising=False)

    reloaded = _reload_config_module()

    assert reloaded.CONVERSATION_EXECUTION_AUTHORITY_TARGET == "public_service"
    assert reloaded.CONVERSATION_EXECUTION_USER_WRITE_TARGET == "public_service"
    assert reloaded.CONVERSATION_EXECUTION_CONTEXT_READ_TARGET == "public_service"
    assert reloaded.CONVERSATION_ASSISTANT_WRITE_TARGET == "legacy"
    assert reloaded.CONVERSATION_OVERLAY_ENABLED is True


def test_config_split_execution_authority_is_rejected_in_production(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CONVERSATION_USER_WRITE_TARGET", "legacy")
    monkeypatch.setenv("CONVERSATION_CONTEXT_READ_TARGET", "public_service")
    monkeypatch.delenv("CONVERSATION_EXECUTION_AUTHORITY_TARGET", raising=False)

    import config

    with pytest.raises(ValueError, match="split authority"):
        importlib.reload(config)


def test_config_defaults_conversation_rollout_to_public_service(tmp_path, monkeypatch):
    config_root = (tmp_path / 'config').resolve()
    config_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv('HIGHTHINKINGQA_SERVICE_CONFIG_ROOT', str(config_root))
    monkeypatch.delenv('HIGHTHINKINGQA_ENV_FILE', raising=False)
    monkeypatch.delenv('HIGHTHINKINGQA_ENV_FILES', raising=False)
    monkeypatch.delenv('SERVICE_ENV_FILE', raising=False)
    monkeypatch.delenv('SERVICE_ENV_FILES', raising=False)
    monkeypatch.delenv('CONVERSATION_EXECUTION_AUTHORITY_TARGET', raising=False)
    monkeypatch.delenv('CONVERSATION_ASSISTANT_WRITE_TARGET', raising=False)
    monkeypatch.delenv('CONVERSATION_OVERLAY_ENABLED', raising=False)
    monkeypatch.delenv('CONVERSATION_USER_WRITE_TARGET', raising=False)
    monkeypatch.delenv('CONVERSATION_CONTEXT_READ_TARGET', raising=False)

    import config

    reloaded = importlib.reload(config)

    assert reloaded.CONVERSATION_EXECUTION_AUTHORITY_TARGET == 'public_service'
    assert reloaded.CONVERSATION_EXECUTION_USER_WRITE_TARGET == 'public_service'
    assert reloaded.CONVERSATION_EXECUTION_CONTEXT_READ_TARGET == 'public_service'
    assert reloaded.CONVERSATION_ASSISTANT_WRITE_TARGET == 'public_service'
    assert reloaded.CONVERSATION_OVERLAY_ENABLED is False
