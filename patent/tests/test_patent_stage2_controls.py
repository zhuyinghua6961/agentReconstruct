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
    ):
        monkeypatch.delenv(key, raising=False)

    toggles = resolve_stage2_runtime_toggles()

    assert toggles.convergence_enabled is False
    assert toggles.rerank_enabled is True
    assert toggles.validation_enabled is True


def test_stage2_runtime_signature_includes_behavior_affecting_fields(monkeypatch):
    monkeypatch.setenv("PATENT_STAGE2_CONVERGENCE_ENABLED", "true")
    monkeypatch.setenv("PATENT_STAGE2_MAX_GLOBAL_PATENTS", "12")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_PROVIDER", "dashscope")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_MODEL", "gte-rerank-v2")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_BASE_URL", "https://dashscope.aliyuncs.com")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_TIMEOUT_SECONDS", "33.5")
    monkeypatch.setenv("PATENT_STAGE2_RERANK_ENDPOINT_FAMILY", "dashscope-text-rerank")

    signature = build_stage2_runtime_signature(base_signature={"retrieval_version": "retrieval-v2"})

    assert signature["retrieval_version"] == "retrieval-v2"
    assert signature["stage2_convergence_enabled"] is True
    assert signature["stage2_max_global_patents"] == 12
    assert signature["stage2_rerank_provider"] == "dashscope"
    assert signature["stage2_rerank_model"] == "gte-rerank-v2"
    assert signature["stage2_rerank_base_url"] == "https://dashscope.aliyuncs.com"
    assert signature["stage2_rerank_timeout_seconds"] == 33.5
    assert signature["stage2_rerank_endpoint_family"] == "dashscope-text-rerank"
    assert "stage2_rerank_api_key" not in signature
    assert signature["stage2_rerank_adapter_version"]
    assert signature["stage2_guardrail_version"]
    assert signature["stage2_validation_version"]
    assert signature["stage2_scoring_version"]
