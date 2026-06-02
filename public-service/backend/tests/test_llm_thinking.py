from __future__ import annotations

from app.modules.documents.llm_thinking import LOCAL_OPENAI_COMPATIBLE_API_KEY, local_sdk_api_key


def test_local_sdk_api_key_strips_bearer_prefix():
    assert local_sdk_api_key("Bearer sk-test") == "sk-test"
    assert local_sdk_api_key("bearer sk-test") == "sk-test"
    assert local_sdk_api_key("sk-test") == "sk-test"
    assert local_sdk_api_key("") == LOCAL_OPENAI_COMPATIBLE_API_KEY
