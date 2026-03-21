from __future__ import annotations

import importlib
from pathlib import Path

import config as config_module
from server.services.conversation import chat_json_store as chat_json_store_module


def _reload_modules():
    importlib.reload(config_module)
    importlib.reload(chat_json_store_module)
    return config_module, chat_json_store_module


def test_chat_json_store_defaults_under_service_state_root(tmp_path, monkeypatch):
    state_root = (tmp_path / "state").resolve()
    monkeypatch.setenv("HIGHTHINKINGQA_SERVICE_STATE_ROOT", str(state_root))
    monkeypatch.delenv("CHAT_JSON_BASE_DIR", raising=False)

    _config, module = _reload_modules()
    store = module.ConversationJsonStore()

    expected = (state_root / "data/conversations/7/11.json").resolve()
    assert store.conversation_local_path(user_id=7, conversation_id=11) == expected

    result = store.write_document(
        user_id=7,
        conversation_id=11,
        document=store.build_default_document(
            conversation_id=11,
            user_id=7,
            title="demo",
            created_at="2026-03-18T10:00:00+08:00",
            updated_at="2026-03-18T10:00:00+08:00",
        ),
    )
    assert Path(result["local_path"]).resolve() == expected
    assert expected.exists()


def test_chat_json_store_relative_override_is_state_root_relative(tmp_path, monkeypatch):
    state_root = (tmp_path / "state").resolve()
    custom_root = (tmp_path / "custom-project-root").resolve()
    monkeypatch.setenv("HIGHTHINKINGQA_SERVICE_STATE_ROOT", str(state_root))
    monkeypatch.setenv("CHAT_JSON_BASE_DIR", "custom-chat")

    _config, module = _reload_modules()
    store = module.ConversationJsonStore(project_root=str(custom_root))

    assert store.conversation_local_path(user_id=3, conversation_id=9) == (state_root / "custom-chat/3/9.json").resolve()
