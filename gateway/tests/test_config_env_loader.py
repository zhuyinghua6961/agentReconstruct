from __future__ import annotations

import importlib


def test_gateway_settings_load_resource_config_on_direct_import(tmp_path, monkeypatch):
    resource_root = tmp_path / "resource"
    shared_root = resource_root / "config" / "shared"
    shared_root.mkdir(parents=True)
    (shared_root / "infrastructure.shared.env").write_text(
        "GATEWAY_PORT=18101\nFAST_BACKEND_BASE_URL=http://127.0.0.1:18008\n",
        encoding="utf-8",
    )

    for name in (
        "GATEWAY_PORT",
        "FAST_BACKEND_BASE_URL",
        "GATEWAY_ENV_FILE",
        "GATEWAY_ENV_FILES",
        "SERVICE_ENV_FILE",
        "SERVICE_ENV_FILES",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RESOURCE_ROOT", str(resource_root))

    import app.core.config as config

    reloaded = importlib.reload(config)
    settings = reloaded.GatewaySettings.from_env()

    assert settings.port == 18101
    assert settings.endpoints.fast == "http://127.0.0.1:18008"
