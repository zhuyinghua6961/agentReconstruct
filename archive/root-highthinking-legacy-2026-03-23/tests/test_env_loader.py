from __future__ import annotations

import importlib
from pathlib import Path

import env_loader


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


def test_config_uses_env_values_and_resolves_relative_paths(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "masked")
    monkeypatch.setenv("LLM_MODEL", "unit-test-model")
    monkeypatch.setenv("PAPERS_DIR", "tmp-papers")

    import config

    reloaded = importlib.reload(config)
    expected_dir = str((Path(reloaded.PROJECT_ROOT) / "tmp-papers").resolve())

    assert reloaded.DASHSCOPE_API_KEY == "masked"
    assert reloaded.LLM_MODEL == "unit-test-model"
    assert reloaded.PAPERS_DIR == expected_dir
