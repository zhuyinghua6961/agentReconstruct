from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import app.modules.generation_pipeline.intent_detect as idetect
from app.modules.generation_pipeline.intent_detect import (
    apply_intent_tag_to_question_focus,
    build_intent_detect_system_prompt,
    format_intent_hint_for_stage1_user_block,
    intent_detect_enabled,
    intent_detect_model,
    run_intent_detect_quick_tag,
)


@pytest.fixture(autouse=True)
def _clear_intent_model_env(monkeypatch):
    for name in (
        "INTENT_MODEL_ENABLED",
        "INTENT_MODEL_API_KEY",
        "INTENT_MODEL_BASE_URL",
        "INTENT_MODEL",
        "INTENT_MODEL_TIMEOUT_SECONDS",
        "QA_INTENT_DETECT_ENABLED",
        "QA_INTENT_DETECT_MODEL",
        "QA_INTENT_DETECT_OVERRIDE_FOCUS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_normalize_intent_maps_raw_line_to_tag():
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="  mechanism_analysis  "))]
    )
    r = run_intent_detect_quick_tag(client=client, user_question="反应机理是什么", logger=None)
    assert r["ok"] is True
    assert r["intent_tag"] == "mechanism_analysis"


def test_build_intent_detect_prompt_mentions_cn_input_and_strict_key():
    blob = build_intent_detect_system_prompt()
    assert "User text may be Chinese or English" in blob
    assert "mechanism_analysis" in blob
    assert "generic" in blob


def test_normalize_intent_strips_markdown_fence():
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="```\ncharacterization\n```"))]
    )
    r = run_intent_detect_quick_tag(client=client, user_question="SEM 能看出什么？", logger=None)
    assert r["ok"] is True
    assert r["intent_tag"] == "characterization"


def test_run_intent_detect_fallback_generic_on_upstream_error():
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("network")
    r = run_intent_detect_quick_tag(client=client, user_question="test", logger=None)
    assert r["ok"] is False
    assert r["intent_tag"] == "generic"
    assert "network" in str(r.get("error", ""))


def test_intent_detect_prefers_unified_intent_model_env(monkeypatch):
    monkeypatch.delenv("QA_INTENT_DETECT_ENABLED", raising=False)
    monkeypatch.delenv("QA_INTENT_DETECT_MODEL", raising=False)
    monkeypatch.setenv("INTENT_MODEL_ENABLED", "true")
    monkeypatch.setenv("INTENT_MODEL", "unified-intent")

    assert intent_detect_enabled() is True
    assert intent_detect_model() == "unified-intent"


def test_intent_detect_shared_default_does_not_block_legacy_enable(monkeypatch):
    monkeypatch.setenv("INTENT_MODEL_ENABLED", "false")
    monkeypatch.setenv("QA_INTENT_DETECT_ENABLED", "true")

    assert intent_detect_enabled() is True


def test_intent_detect_uses_dedicated_endpoint_when_key_is_configured(monkeypatch):
    calls: list[dict] = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "characterization"}}]}

    def _post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _Response()

    monkeypatch.setenv("INTENT_MODEL_API_KEY", "intent-key")
    monkeypatch.setenv("INTENT_MODEL_BASE_URL", "https://intent.example/v1")
    monkeypatch.setenv("INTENT_MODEL", "intent-model")
    monkeypatch.setattr(idetect.httpx, "post", _post)
    primary_client = MagicMock()
    primary_client.chat.completions.create.side_effect = AssertionError("primary client should not be used")

    r = run_intent_detect_quick_tag(client=primary_client, user_question="SEM 能看出什么？", logger=None)

    assert r["ok"] is True
    assert r["intent_tag"] == "characterization"
    assert calls[0]["url"] == "https://intent.example/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer intent-key"
    assert calls[0]["json"]["model"] == "intent-model"
    assert calls[0]["json"]["enable_thinking"] is False


def test_format_hint_empty_when_not_ok():
    assert format_intent_hint_for_stage1_user_block(intent_result={"ok": False}) == ""


def test_apply_override_mechanism_over_synthesis(monkeypatch):
    monkeypatch.setenv("QA_INTENT_DETECT_OVERRIDE_FOCUS", "true")
    base = {
        "focus_type": "synthesis_preparation",
        "focus_summary": "制备",
        "evidence_axes": ["烧结温度"],
        "secondary_axes": [],
        "confidence": "medium",
    }
    out = apply_intent_tag_to_question_focus(intent_tag="mechanism_analysis", question_focus=base)
    assert out["focus_type"] == "mechanism_analysis"
    assert "机理" in out.get("focus_summary", "")


def test_apply_override_skips_when_disabled(monkeypatch):
    monkeypatch.setenv("QA_INTENT_DETECT_OVERRIDE_FOCUS", "false")
    base = {"focus_type": "synthesis_preparation", "focus_summary": "", "evidence_axes": [], "secondary_axes": [], "confidence": "low"}
    out = apply_intent_tag_to_question_focus(intent_tag="mechanism_analysis", question_focus=base)
    assert out["focus_type"] == "synthesis_preparation"
