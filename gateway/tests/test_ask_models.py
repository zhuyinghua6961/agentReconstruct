from __future__ import annotations

from app.models.ask import AskRequest


def test_ask_request_trims_oversized_chat_history_content_instead_of_rejecting():
    payload = AskRequest.model_validate(
        {
            "question": "next question",
            "requested_mode": "fast",
            "conversation_id": 297,
            "user_id": 7,
            "chat_history": [
                {"role": "assistant", "content": "x" * 4339},
                {"role": "user", "content": "follow up"},
            ],
        }
    )

    assert len(payload.chat_history) == 2
    assert payload.chat_history[0].role == "assistant"
    assert len(payload.chat_history[0].content) == 4000
    assert payload.chat_history[1].content == "follow up"
