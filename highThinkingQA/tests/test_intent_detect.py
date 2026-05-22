"""Unit tests for highThinkingQA intent_detect (env + normalization via API response)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent_core import intent_detect as idetect


@pytest.fixture(autouse=True)
def _clear_ht_intent_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "HT_QA_INTENT_DETECT_ENABLED",
        "QA_INTENT_DETECT_ENABLED",
        "HT_QA_INTENT_DETECT_MODEL",
        "QA_INTENT_DETECT_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_intent_detect_enabled_prefers_ht_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HT_QA_INTENT_DETECT_ENABLED", "1")
    assert idetect.intent_detect_enabled() is True
    monkeypatch.setenv("QA_INTENT_DETECT_ENABLED", "0")
    assert idetect.intent_detect_enabled() is True


def test_intent_detect_enabled_shared_qa_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QA_INTENT_DETECT_ENABLED", "true")
    assert idetect.intent_detect_enabled() is True


def test_intent_detect_model_ht_overrides_shared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QA_INTENT_DETECT_MODEL", "model-a")
    monkeypatch.setenv("HT_QA_INTENT_DETECT_MODEL", "model-b")
    assert idetect.intent_detect_model() == "model-b"


def test_intent_detect_model_falls_back_to_shared_then_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QA_INTENT_DETECT_MODEL", "tongyi-intent-detect-v3")
    assert idetect.intent_detect_model() == "tongyi-intent-detect-v3"


def test_run_intent_detect_quick_tag_strips_noise() -> None:
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = "```\nmechanism_analysis\n```"
    resp = MagicMock()
    resp.choices = [choice]
    client.chat.completions.create.return_value = resp
    out = idetect.run_intent_detect_quick_tag(
        client=client,
        user_question="为什么会出现相变?",
        logger=None,
    )
    assert out["ok"] is True
    assert out["intent_tag"] == "mechanism_analysis"


def test_pool_timeout_propagates() -> None:
    client = MagicMock()
    PoolTimeoutExc = type("PoolTimeout", (Exception,), {})
    client.chat.completions.create.side_effect = PoolTimeoutExc("boom")
    with pytest.raises(PoolTimeoutExc):
        idetect.run_intent_detect_quick_tag(client=client, user_question="x", logger=None)
