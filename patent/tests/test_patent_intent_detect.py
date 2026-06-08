from __future__ import annotations

import logging
import httpx
import pytest
from types import SimpleNamespace

import server.patent.intent_detect as idetect
from server.patent.intent_detect import (
    format_intent_hint_for_stage1_user_block,
    intent_detect_enabled,
    intent_detect_model,
    patent_intent_detect_cache_signature,
    run_intent_detect_quick_tag,
)


class _Logger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass


class _FakeClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(dict(kwargs))
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))])


@pytest.fixture(autouse=True)
def _clear_intent_model_env(monkeypatch):
    for name in (
        "INTENT_MODEL_ENABLED",
        "INTENT_MODEL_API_KEY",
        "INTENT_MODEL_BASE_URL",
        "INTENT_MODEL",
        "INTENT_MODEL_TIMEOUT_SECONDS",
        "PATENT_INTENT_DETECT_ENABLED",
        "PATENT_INTENT_DETECT_MODEL",
        "QA_INTENT_DETECT_ENABLED",
        "QA_INTENT_DETECT_MODEL",
        "LLM_IS_THINKING_MODEL",
        "LLM_THINKING_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)


def test_patent_intent_detect_cache_signature_reflects_toggle(monkeypatch):
    monkeypatch.delenv("PATENT_INTENT_DETECT_ENABLED", raising=False)
    monkeypatch.delenv("QA_INTENT_DETECT_ENABLED", raising=False)
    assert patent_intent_detect_cache_signature() == {"patent_intent_detect": False}

    monkeypatch.setenv("PATENT_INTENT_DETECT_ENABLED", "1")
    monkeypatch.delenv("QA_INTENT_DETECT_MODEL", raising=False)
    monkeypatch.setenv("PATENT_INTENT_DETECT_MODEL", "custom-intent-model")
    sig = patent_intent_detect_cache_signature()
    assert sig == {"patent_intent_detect": True, "patent_intent_detect_model": "custom-intent-model"}


def test_intent_detect_enabled_accepts_legacy_qa_alias(monkeypatch):
    monkeypatch.delenv("PATENT_INTENT_DETECT_ENABLED", raising=False)
    monkeypatch.setenv("QA_INTENT_DETECT_ENABLED", "true")
    assert intent_detect_enabled() is True
    monkeypatch.delenv("QA_INTENT_DETECT_ENABLED", raising=False)
    monkeypatch.delenv("PATENT_INTENT_DETECT_ENABLED", raising=False)
    assert intent_detect_enabled() is False


def test_intent_detect_model_prefers_patent_then_qa(monkeypatch):
    monkeypatch.delenv("PATENT_INTENT_DETECT_MODEL", raising=False)
    monkeypatch.delenv("QA_INTENT_DETECT_MODEL", raising=False)
    assert intent_detect_model() == "qwen3-8b"
    monkeypatch.setenv("QA_INTENT_DETECT_MODEL", "via-qa")
    assert intent_detect_model() == "via-qa"
    monkeypatch.setenv("PATENT_INTENT_DETECT_MODEL", "via-patent")
    assert intent_detect_model() == "via-patent"


def test_intent_detect_prefers_unified_intent_model_env(monkeypatch):
    monkeypatch.setenv("INTENT_MODEL_ENABLED", "true")
    monkeypatch.setenv("PATENT_INTENT_DETECT_ENABLED", "false")
    monkeypatch.setenv("QA_INTENT_DETECT_ENABLED", "false")
    monkeypatch.setenv("INTENT_MODEL", "unified-intent")
    monkeypatch.setenv("PATENT_INTENT_DETECT_MODEL", "via-patent")
    monkeypatch.setenv("QA_INTENT_DETECT_MODEL", "via-qa")

    assert intent_detect_enabled() is True
    assert intent_detect_model() == "unified-intent"


def test_intent_detect_shared_default_does_not_block_legacy_enable(monkeypatch):
    monkeypatch.setenv("INTENT_MODEL_ENABLED", "false")
    monkeypatch.setenv("PATENT_INTENT_DETECT_ENABLED", "false")
    monkeypatch.setenv("QA_INTENT_DETECT_ENABLED", "true")

    assert intent_detect_enabled() is True


def test_run_intent_detect_uses_dedicated_endpoint_when_key_is_configured(monkeypatch):
    calls: list[dict] = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "comparative_tradeoff"}}]}

    def _post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _Response()

    monkeypatch.setenv("INTENT_MODEL_API_KEY", "intent-key")
    monkeypatch.setenv("INTENT_MODEL_BASE_URL", "https://intent.example/v1")
    monkeypatch.setenv("INTENT_MODEL", "intent-model")
    monkeypatch.setenv("INTENT_MODEL_AUTH_MODE", "x-api-key")
    monkeypatch.setattr(idetect.httpx, "post", _post)

    class _PrimaryClient:
        class chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    raise AssertionError("primary client should not be used")

    got = run_intent_detect_quick_tag(client=_PrimaryClient(), user_question="对比两种路线？", logger=_Logger())

    assert got["ok"] is True
    assert got["intent_tag"] == "comparative_tradeoff"
    assert calls[0]["url"] == "https://intent.example/v1/chat/completions"
    assert calls[0]["headers"]["X-API-Key"] == "intent-key"
    assert calls[0]["json"]["model"] == "intent-model"
    assert calls[0]["json"]["enable_thinking"] is False
    assert "thinking" not in calls[0]["json"]


def test_run_intent_detect_dedicated_endpoint_logs_model_call(monkeypatch, caplog):
    calls: list[dict] = []

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "comparative_tradeoff"}}]}

    def _post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _Response()

    monkeypatch.setenv("INTENT_MODEL_API_KEY", "intent-key")
    monkeypatch.setenv("INTENT_MODEL_BASE_URL", "https://intent.example/v1")
    monkeypatch.setenv("INTENT_MODEL", "intent-model")
    monkeypatch.setattr(idetect.httpx, "post", _post)
    caplog.set_level(logging.INFO, logger="server.patent.intent_detect")

    got = run_intent_detect_quick_tag(client=object(), user_question="对比两种路线？", logger=_Logger())

    messages = [record.message for record in caplog.records]
    assert got["ok"] is True
    assert any(
        "model_call start" in message
        and "service=patent" in message
        and "component=llm_intent" in message
        and "model=intent-model" in message
        and "message_count=2" in message
        for message in messages
    )
    assert any(
        "model_call success" in message
        and "component=llm_intent" in message
        and "status_code=200" in message
        and "answer_chars=" in message
        and "elapsed_ms=" in message
        for message in messages
    )


def test_run_intent_detect_normalizes_full_chat_completion_base_url(monkeypatch):
    calls: list[dict] = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "generic"}}]}

    def _post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _Response()

    monkeypatch.setenv("INTENT_MODEL_API_KEY", "intent-key")
    monkeypatch.setenv("INTENT_MODEL_BASE_URL", "https://intent.example/v1/chat/completions")
    monkeypatch.setenv("INTENT_MODEL", "intent-model")
    monkeypatch.setattr(idetect.httpx, "post", _post)

    got = run_intent_detect_quick_tag(client=object(), user_question="hello", logger=_Logger())

    assert got["ok"] is True
    assert calls[0]["url"] == "https://intent.example/v1/chat/completions"


def test_run_intent_detect_disables_thinking_for_thinking_model(monkeypatch):
    calls: list[dict] = []

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "comparative_tradeoff"}}]}

    def _post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return _Response()

    monkeypatch.setenv("LLM_IS_THINKING_MODEL", "true")
    monkeypatch.setenv("LLM_THINKING_ENABLED", "true")
    monkeypatch.setenv("INTENT_MODEL_API_KEY", "intent-key")
    monkeypatch.setenv("INTENT_MODEL_BASE_URL", "https://intent.example/v1")
    monkeypatch.setattr(idetect.httpx, "post", _post)

    got = run_intent_detect_quick_tag(client=object(), user_question="对比两种路线？", logger=_Logger())

    assert got["ok"] is True
    assert calls[0]["json"]["enable_thinking"] is False
    assert "thinking" not in calls[0]["json"]
    assert "reasoning_effort" not in calls[0]["json"]


def test_run_intent_detect_client_path_disables_thinking_for_thinking_model(monkeypatch):
    monkeypatch.setenv("LLM_IS_THINKING_MODEL", "true")
    monkeypatch.setenv("LLM_THINKING_ENABLED", "true")
    client = _FakeClient("generic")

    got = run_intent_detect_quick_tag(client=client, user_question="反应机理？", logger=_Logger())

    assert got["ok"] is True
    assert client.calls[0]["extra_body"] == {"enable_thinking": False}
    assert "reasoning_effort" not in client.calls[0]


def test_run_intent_detect_quick_tag_normalizes_tag():
    client = _FakeClient("mechanism_analysis")
    got = run_intent_detect_quick_tag(client=client, user_question="反应机理？", logger=_Logger())
    assert got["ok"] is True
    assert got["intent_tag"] == "mechanism_analysis"


def test_format_intent_hint_empty_when_not_ok():
    assert (
        format_intent_hint_for_stage1_user_block(
            intent_result={"ok": False, "intent_tag": "generic"},
        )
        == ""
    )
    hint = format_intent_hint_for_stage1_user_block(
        intent_result={"ok": True, "intent_tag": "generic"},
    )
    assert "generic" in hint
    assert "快速意图识别" in hint


def test_run_intent_detect_re_raises_pool_timeout():
    def _boom(**_kwargs):
        raise httpx.PoolTimeout("pool timeout simulation")

    class _PoolTimeoutClient:
        chat = SimpleNamespace(completions=SimpleNamespace(create=_boom))

    try:
        run_intent_detect_quick_tag(client=_PoolTimeoutClient(), user_question="?", logger=_Logger())
    except Exception as exc:
        assert isinstance(exc, httpx.PoolTimeout)
    else:
        raise AssertionError("expected PoolTimeout")
