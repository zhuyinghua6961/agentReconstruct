from __future__ import annotations

import importlib


def test_config_raises_thinking_service_concurrency_defaults(monkeypatch):
    monkeypatch.delenv("ASK_STREAM_MAX_CONCURRENT", raising=False)
    monkeypatch.delenv("ASK_EXECUTOR_MAX_WORKERS", raising=False)

    import config

    reloaded = importlib.reload(config)

    assert reloaded.ASK_STREAM_MAX_CONCURRENT == 20
    assert reloaded.ASK_EXECUTOR_MAX_WORKERS == 20
