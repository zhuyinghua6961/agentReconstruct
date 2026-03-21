import os

from app.core.config import GatewaySettings


def test_gateway_settings_default_public_backend_is_public_service(monkeypatch):
    monkeypatch.delenv("PUBLIC_BACKEND_BASE_URL", raising=False)
    monkeypatch.delenv("FAST_BACKEND_BASE_URL", raising=False)
    monkeypatch.delenv("THINKING_BACKEND_BASE_URL", raising=False)
    monkeypatch.delenv("PATENT_BACKEND_BASE_URL", raising=False)
    monkeypatch.delenv("GATEWAY_CONVERSATION_FILE_PROVIDER", raising=False)

    settings = GatewaySettings.from_env()

    assert settings.endpoints.public == "http://127.0.0.1:8102"
    assert settings.endpoints.fast == "http://127.0.0.1:8008"
    assert settings.conversation_file_provider == "noop"


def test_gateway_settings_accepts_explicit_public_service_provider(monkeypatch):
    monkeypatch.setenv("PUBLIC_BACKEND_BASE_URL", "http://127.0.0.1:9020")
    monkeypatch.setenv("GATEWAY_CONVERSATION_FILE_PROVIDER", "public_http")

    settings = GatewaySettings.from_env()

    assert settings.endpoints.public == "http://127.0.0.1:9020"
    assert settings.conversation_file_provider == "public_http"


def test_gateway_settings_report_backend_warning_for_default_mode_endpoints(monkeypatch):
    monkeypatch.delenv("FAST_BACKEND_BASE_URL", raising=False)
    monkeypatch.delenv("THINKING_BACKEND_BASE_URL", raising=False)
    monkeypatch.delenv("PATENT_BACKEND_BASE_URL", raising=False)

    settings = GatewaySettings.from_env()

    assert "fast_backend_uses_default_placeholder" in settings.backend_config_warnings
    assert "thinking_backend_uses_default_placeholder" in settings.backend_config_warnings
    assert "patent_backend_uses_default_placeholder" in settings.backend_config_warnings


def test_gateway_settings_can_enable_strict_backend_validation(monkeypatch):
    monkeypatch.setenv("GATEWAY_STRICT_BACKEND_CONFIG", "1")

    settings = GatewaySettings.from_env()

    assert settings.strict_backend_config is True
