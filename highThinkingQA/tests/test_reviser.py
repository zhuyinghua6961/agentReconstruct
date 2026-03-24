from agent_core.reviser import revise_answer


def test_reviser_uses_no_retry_client_when_client_not_provided(monkeypatch):
    captured = {}
    fake_client = object()

    def fake_get_llm_client(*, max_retries=None):
        captured["max_retries"] = max_retries
        return fake_client

    def fake_chat_completion(**kwargs):
        captured["client"] = kwargs["client"]
        return "revised answer"

    monkeypatch.setattr("agent_core.reviser.get_llm_client", fake_get_llm_client)
    monkeypatch.setattr("agent_core.reviser.chat_completion", fake_chat_completion)

    revised = revise_answer(
        question="demo",
        answer="draft answer",
        issues=[{"claim": "x", "citation": "[10.1000/demo]", "problem": "bad citation"}],
        client=None,
    )

    assert revised == "revised answer"
    assert captured["max_retries"] == 0
    assert captured["client"] is fake_client
