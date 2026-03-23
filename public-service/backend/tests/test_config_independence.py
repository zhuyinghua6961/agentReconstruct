from __future__ import annotations

import importlib
from pathlib import Path

import pytest


@pytest.fixture()
def config_module(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("PUBLIC_SERVICE_ENV_FILE", raising=False)
    monkeypatch.delenv("PUBLIC_SERVICE_ENV_FILES", raising=False)
    monkeypatch.delenv("PUBLIC_SERVICE_LOAD_DOTENV", raising=False)
    monkeypatch.delenv("PUBLIC_SERVICE_DATA_ROOT", raising=False)
    monkeypatch.delenv("UPLOAD_DIR", raising=False)
    monkeypatch.delenv("PAPERS_DIR", raising=False)
    monkeypatch.delenv("CHAT_JSON_BASE_DIR", raising=False)
    monkeypatch.delenv("VECTOR_DB_PATH", raising=False)
    monkeypatch.delenv("TRANSLATION_CACHE_DIR", raising=False)
    monkeypatch.delenv("PUBLIC_SERVICE_LOGS_DIR", raising=False)
    monkeypatch.delenv("LOCAL_STORAGE_ROOT", raising=False)
    monkeypatch.delenv("PUBLIC_SERVICE_PORT", raising=False)
    monkeypatch.delenv("MYSQL_HOST", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("CONVERSATION_EXECUTION_AUTHORITY_TARGET", raising=False)
    monkeypatch.delenv("CONVERSATION_ASSISTANT_WRITE_TARGET", raising=False)
    monkeypatch.delenv("CONVERSATION_OVERLAY_ENABLED", raising=False)
    monkeypatch.delenv("CONVERSATION_USER_WRITE_TARGET", raising=False)
    monkeypatch.delenv("CONVERSATION_CONTEXT_READ_TARGET", raising=False)
    monkeypatch.chdir(tmp_path)
    import app.core.config as config
    import app.core.env_loader as env_loader

    importlib.reload(env_loader)
    importlib.reload(config)
    config.get_settings.cache_clear()
    yield config
    config.get_settings.cache_clear()


def test_no_implicit_repo_root_env_loading(config_module) -> None:
    settings = config_module.get_settings()
    assert settings.mysql_host == "127.0.0.1"
    assert settings.port == 8102


def test_explicit_env_file_loading(config_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / "public-service.env"
    env_file.write_text("MYSQL_HOST=10.0.0.8\nPUBLIC_SERVICE_PORT=9123\n", encoding="utf-8")
    monkeypatch.setenv("PUBLIC_SERVICE_ENV_FILE", str(env_file))

    import app.core.env_loader as env_loader
    import app.core.config as config

    importlib.reload(env_loader)
    importlib.reload(config)
    config.get_settings.cache_clear()
    settings = config.get_settings()
    assert settings.mysql_host == "10.0.0.8"
    assert settings.port == 9123


def test_default_data_dirs_resolve_under_tmp_root(config_module) -> None:
    settings = config_module.get_settings()
    assert settings.data_root == Path("/tmp/public-service")
    assert settings.uploads_dir == Path("/tmp/public-service/uploads")
    assert settings.papers_dir == Path("/tmp/public-service/papers")
    assert settings.chat_json_base_dir == Path("/tmp/public-service/data/conversations")
    assert settings.vector_db_path == Path("/tmp/public-service/vector_database")
    assert settings.translation_cache_dir == Path("/tmp/public-service/translation_cache")
    assert settings.logs_dir == Path("/tmp/public-service/logs")
    assert settings.local_storage_root == Path("/tmp/public-service/storage")


def test_relative_overrides_resolve_under_data_root(config_module, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PUBLIC_SERVICE_DATA_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("UPLOAD_DIR", "uploads-custom")
    monkeypatch.setenv("PAPERS_DIR", "papers-custom")
    monkeypatch.setenv("CHAT_JSON_BASE_DIR", "state/chat")
    monkeypatch.setenv("VECTOR_DB_PATH", "vector/chroma")
    monkeypatch.setenv("TRANSLATION_CACHE_DIR", "cache/translations")
    monkeypatch.setenv("PUBLIC_SERVICE_LOGS_DIR", "logs-custom")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", "storage-custom")
    config_module.get_settings.cache_clear()

    settings = config_module.get_settings()
    root = (tmp_path / "runtime").resolve()
    assert settings.data_root == root
    assert settings.uploads_dir == root / "uploads-custom"
    assert settings.papers_dir == root / "papers-custom"
    assert settings.chat_json_base_dir == root / "state/chat"
    assert settings.vector_db_path == root / "vector/chroma"
    assert settings.translation_cache_dir == root / "cache/translations"
    assert settings.logs_dir == root / "logs-custom"
    assert settings.local_storage_root == root / "storage-custom"


def test_conversation_rollout_flags_keep_execution_authority_coupled(
    config_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONVERSATION_EXECUTION_AUTHORITY_TARGET", "public_service")
    monkeypatch.setenv("CONVERSATION_ASSISTANT_WRITE_TARGET", "legacy")
    monkeypatch.setenv("CONVERSATION_OVERLAY_ENABLED", "1")
    config_module.get_settings.cache_clear()

    settings = config_module.get_settings()

    assert settings.conversation_execution_authority_target == "public_service"
    assert settings.conversation_execution_user_write_target == "public_service"
    assert settings.conversation_execution_context_read_target == "public_service"
    assert settings.conversation_assistant_write_target == "legacy"
    assert settings.conversation_overlay_enabled is True


def test_conversation_split_execution_authority_is_rejected_in_production(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CONVERSATION_USER_WRITE_TARGET", "legacy")
    monkeypatch.setenv("CONVERSATION_CONTEXT_READ_TARGET", "public_service")
    monkeypatch.chdir(tmp_path)

    import app.core.config as config
    import app.core.env_loader as env_loader

    importlib.reload(env_loader)
    reloaded = importlib.reload(config)
    reloaded.get_settings.cache_clear()
    with pytest.raises(ValueError, match="split authority"):
        reloaded.get_settings()
