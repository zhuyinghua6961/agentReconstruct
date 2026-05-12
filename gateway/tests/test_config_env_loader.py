from __future__ import annotations

import importlib
from pathlib import Path


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


def test_gateway_resource_config_surface_removes_fixed_infra_and_admission_switches():
    infrastructure_keys = _env_keys(REPO_ROOT / "resource/config/shared/infrastructure.shared.env")
    gateway_keys = _env_keys(REPO_ROOT / "resource/config/services/gateway/config.shared.env")

    assert "REDIS_ENABLED" not in infrastructure_keys
    assert "MINIO_USE_PROXY" not in infrastructure_keys
    assert {
        "REDIS_HOST",
        "REDIS_PORT",
        "REDIS_DB",
        "REDIS_SOCKET_CONNECT_TIMEOUT_SEC",
        "REDIS_SOCKET_TIMEOUT_SEC",
        "MYSQL_HOST",
        "MYSQL_PORT",
        "MINIO_BUCKET",
        "MINIO_SECURE",
        "MINIO_DOWNLOAD_EXPIRES",
    } <= infrastructure_keys

    assert {
        "GATEWAY_ADMISSION_ENABLED",
        "GATEWAY_ADMISSION_DISPATCHER_ENABLED",
        "GATEWAY_ADMISSION_WORKER_ENABLED",
    }.isdisjoint(gateway_keys)
    assert {
        "INTERACTIVE_EXECUTION_MAX_CONCURRENT",
        "INTERACTIVE_EXECUTION_FAST_OR_PATENT_MAX_CONCURRENT",
        "INTERACTIVE_EXECUTION_THINKING_MAX_CONCURRENT",
        "REDIS_KEY_PREFIX",
    } <= gateway_keys
