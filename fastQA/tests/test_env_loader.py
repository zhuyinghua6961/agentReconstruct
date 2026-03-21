from __future__ import annotations

import importlib
from pathlib import Path

import app.core.env_loader as env_loader


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

    assert result[:4] == (
        (config_root / "config.env").resolve(),
        (config_root / "config.shared.env").resolve(),
        (config_root / "config.secret.env").resolve(),
        (config_root / ".env").resolve(),
    )
    assert result[4:] == reloaded.ENV_FILE_CANDIDATES


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
    resource_root.mkdir(parents=True, exist_ok=True)
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
