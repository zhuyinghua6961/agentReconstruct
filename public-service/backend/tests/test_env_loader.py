from __future__ import annotations

import importlib


def test_public_service_loads_resource_service_config_without_explicit_env_files(tmp_path, monkeypatch):
    resource_root = tmp_path / "resource"
    service_root = resource_root / "config" / "services" / "public-service"
    service_root.mkdir(parents=True)
    (service_root / "config.shared.env").write_text(
        "PUBLIC_SERVICE_PORT=18102\nREDIS_KEY_PREFIX=public_service_test\n",
        encoding="utf-8",
    )

    for name in (
        "PUBLIC_SERVICE_PORT",
        "REDIS_KEY_PREFIX",
        "PUBLIC_SERVICE_ENV_FILE",
        "PUBLIC_SERVICE_ENV_FILES",
        "PUBLIC_SERVICE_LOAD_DOTENV",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RESOURCE_ROOT", str(resource_root))

    import app.core.env_loader as env_loader
    import app.core.config as config

    importlib.reload(env_loader)
    reloaded = importlib.reload(config)
    reloaded.get_settings.cache_clear()
    settings = reloaded.get_settings()

    assert settings.port == 18102
    assert settings.redis_key_prefix == "public_service_test"


def test_public_service_loads_legacy_dotenv_with_resource_config(tmp_path, monkeypatch):
    resource_root = tmp_path / "resource"
    service_root = resource_root / "config" / "services" / "public-service"
    service_root.mkdir(parents=True)

    legacy_root = tmp_path / "public-service"
    legacy_root.mkdir()
    (legacy_root / ".env").write_text("PUBLIC_SERVICE_PORT=19102\n", encoding="utf-8")

    for name in (
        "PUBLIC_SERVICE_PORT",
        "PUBLIC_SERVICE_ENV_FILE",
        "PUBLIC_SERVICE_ENV_FILES",
        "PUBLIC_SERVICE_LOAD_DOTENV",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RESOURCE_ROOT", str(resource_root))

    import app.core.env_loader as env_loader

    monkeypatch.setattr(env_loader, "SERVICE_DIR", legacy_root)
    monkeypatch.setattr(
        env_loader,
        "_LEGACY_ENV_FILES",
        (
            legacy_root / "config.shared.env",
            legacy_root / "config.secret.env",
            legacy_root / ".env",
        ),
    )

    paths = env_loader.iter_env_files()

    assert legacy_root / ".env" in paths
