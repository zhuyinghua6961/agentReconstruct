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


def test_gateway_settings_keep_patent_file_routes_enabled_by_default(monkeypatch):
    monkeypatch.delenv("GATEWAY_PATENT_FILE_ROUTES_ENABLED", raising=False)

    settings = GatewaySettings.from_env()

    assert settings.patent_file_routes_enabled is True


def test_gateway_settings_can_enable_patent_file_routes(monkeypatch):
    monkeypatch.setenv("GATEWAY_PATENT_FILE_ROUTES_ENABLED", "true")

    settings = GatewaySettings.from_env()

    assert settings.patent_file_routes_enabled is True


def test_gateway_settings_can_disable_patent_file_routes(monkeypatch):
    monkeypatch.setenv("GATEWAY_PATENT_FILE_ROUTES_ENABLED", "false")

    settings = GatewaySettings.from_env()

    assert settings.patent_file_routes_enabled is False


def test_gateway_settings_expose_admission_defaults(monkeypatch):
    monkeypatch.delenv("INTERACTIVE_EXECUTION_MAX_CONCURRENT", raising=False)
    monkeypatch.delenv("INTERACTIVE_EXECUTION_FAST_OR_PATENT_MAX_CONCURRENT", raising=False)
    monkeypatch.delenv("INTERACTIVE_EXECUTION_THINKING_MAX_CONCURRENT", raising=False)
    monkeypatch.delenv("INTERACTIVE_EXECUTION_PER_USER_MAX_ACTIVE", raising=False)
    monkeypatch.delenv("INTERACTIVE_EXECUTION_THINKING_MIN_SLOTS", raising=False)
    monkeypatch.delenv("INTERACTIVE_QUEUE_MAX_SIZE", raising=False)
    monkeypatch.delenv("INTERACTIVE_QUEUED_TTL_SECONDS", raising=False)
    monkeypatch.delenv("INTERACTIVE_POST_ADMIT_ATTACH_TTL_SECONDS", raising=False)
    monkeypatch.delenv("GATEWAY_ADMISSION_ENABLED", raising=False)
    monkeypatch.delenv("GATEWAY_RUNTIME_ROLE", raising=False)
    monkeypatch.delenv("REDIS_ENABLED", raising=False)
    monkeypatch.delenv("GATEWAY_REFRESH_SURVIVABLE_QA_TASKS_ENABLED", raising=False)

    settings = GatewaySettings.from_env()

    assert settings.redis.enabled is False
    assert settings.redis.key_prefix == "gateway"
    assert settings.admission.enabled is False
    assert settings.admission.runtime_role == "web"
    assert settings.admission.max_concurrent == 20
    assert settings.admission.fast_or_patent_max_concurrent == 20
    assert settings.admission.thinking_max_concurrent == 5
    assert settings.admission.per_user_max_active == 5
    assert settings.admission.thinking_min_slots == 1
    assert settings.admission.queue_max_size == 200
    assert settings.admission.queued_ttl_seconds == 900
    assert settings.admission.post_admit_attach_ttl_seconds == 600
    assert settings.refresh_survivable_qa_tasks_enabled is False


def test_gateway_settings_accept_runtime_role_and_redis_env(monkeypatch):
    monkeypatch.setenv("GATEWAY_RUNTIME_ROLE", "admission_worker")
    monkeypatch.setenv("GATEWAY_ADMISSION_ENABLED", "1")
    monkeypatch.setenv("GATEWAY_ADMISSION_CONTROL_TOKEN", "secret-token")
    monkeypatch.setenv("REDIS_ENABLED", "1")
    monkeypatch.setenv("REDIS_KEY_PREFIX", "gateway_dev")
    monkeypatch.setenv("INTERACTIVE_EXECUTION_MAX_CONCURRENT", "12")
    monkeypatch.setenv("INTERACTIVE_EXECUTION_PER_USER_MAX_ACTIVE", "3")
    monkeypatch.setenv("INTERACTIVE_EXECUTION_THINKING_MIN_SLOTS", "2")
    monkeypatch.setenv("INTERACTIVE_QUEUE_MAX_SIZE", "88")

    settings = GatewaySettings.from_env()

    assert settings.admission.enabled is True
    assert settings.admission.is_admission_worker is True
    assert settings.admission.control_api_token == "secret-token"
    assert settings.redis.enabled is True
    assert settings.redis.key_prefix == "gateway_dev"
    assert settings.admission.max_concurrent == 12
    assert settings.admission.per_user_max_active == 3
    assert settings.admission.thinking_min_slots == 2
    assert settings.admission.queue_max_size == 88


def test_gateway_settings_can_enable_refresh_survivable_qa_tasks(monkeypatch):
    monkeypatch.setenv("GATEWAY_REFRESH_SURVIVABLE_QA_TASKS_ENABLED", "true")

    settings = GatewaySettings.from_env()

    assert settings.refresh_survivable_qa_tasks_enabled is True
