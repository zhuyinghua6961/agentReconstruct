from __future__ import annotations

from server.patent.stage2_controls import (
    build_stage2_runtime_signature,
    resolve_stage2_runtime_toggles,
)


def test_stage2_convergence_rollout_gate_defaults_off(monkeypatch):
    for key in (
        "PATENT_STAGE2_CONVERGENCE_ENABLED",
        "PATENT_STAGE2_RERANK_ENABLED",
        "PATENT_STAGE2_VALIDATION_ENABLED",
        "RERANK_BASE_URL",
        "RERANK_MODEL",
        "PATENT_STAGE2_RERANK_BASE_URL",
        "PATENT_STAGE2_RERANK_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    toggles = resolve_stage2_runtime_toggles()

    assert toggles.convergence_enabled is False
    assert toggles.rerank_enabled is False
    assert toggles.validation_enabled is True


def test_stage2_rerank_enabled_ignores_disabled_env(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_RERANK_ENABLED", "false")
    monkeypatch.setenv("RERANK_BASE_URL", "https://rerank.example/v1")
    monkeypatch.setenv("RERANK_MODEL", "rerank-model")

    toggles = resolve_stage2_runtime_toggles()

    assert toggles.rerank_enabled is True


def test_stage2_runtime_signature_includes_behavior_affecting_fields(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_MAX_GLOBAL_PATENTS", "12")
    monkeypatch.setenv("RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("RERANK_MODEL", "gte-rerank-v2")
    monkeypatch.setenv("RERANK_BASE_URL", "https://dashscope.aliyuncs.com")
    monkeypatch.setenv("RERANK_TIMEOUT_SECONDS", "33.5")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_PROVIDER", "local")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_MODEL", "legacy-rerank")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_BASE_URL", "https://legacy.example")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_TIMEOUT_SECONDS", "44.5")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_ENDPOINT_FAMILY", "legacy-family")

    signature = build_stage2_runtime_signature(base_signature={"retrieval_version": "retrieval-v2"})

    assert signature["retrieval_version"] == "retrieval-v2"
    assert signature["stage2_convergence_enabled"] is True
    assert signature["stage2_max_global_patents"] == 12
    assert signature["stage2_rerank_provider"] == "openai_compatible"
    assert signature["stage2_rerank_model"] == "gte-rerank-v2"
    assert signature["stage2_rerank_base_url"] == "https://dashscope.aliyuncs.com"
    assert signature["stage2_rerank_timeout_seconds"] == 33.5
    assert signature["stage2_rerank_endpoint_family"] == "openai_compatible"
    assert "stage2_rerank_api_key" not in signature
    assert signature["stage2_rerank_adapter_version"]
    assert signature["stage2_guardrail_version"]
    assert signature["stage2_validation_version"]
    assert signature["stage2_scoring_version"]


def test_stage2_runtime_signature_falls_back_to_legacy_rerank_endpoint_aliases_for_one_version(monkeypatch):
    for name in ("RERANK_PROVIDER", "RERANK_MODEL", "RERANK_BASE_URL", "RERANK_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PATENT_STAGE2_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_MODEL", "legacy-rerank")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_BASE_URL", "https://legacy.example")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_TIMEOUT_SECONDS", "44.5")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_ENDPOINT_FAMILY", "legacy-family")

    signature = build_stage2_runtime_signature()

    assert signature["stage2_rerank_provider"] == "openai_compatible"
    assert signature["stage2_rerank_model"] == "legacy-rerank"
    assert signature["stage2_rerank_base_url"] == "https://legacy.example"
    assert signature["stage2_rerank_timeout_seconds"] == 44.5
    assert signature["stage2_rerank_endpoint_family"] == "openai_compatible"
