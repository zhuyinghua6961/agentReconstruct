from agent_core.checker import check_answer
from retriever.vector_retriever import RetrievedChunk


def test_checker_precheck_short_circuits_unknown_doi(monkeypatch):
    called = {"llm": False}

    def fake_chat_completion(**kwargs):
        called["llm"] = True
        return '{"passed": true, "issues": []}'

    monkeypatch.setattr("agent_core.checker.chat_completion", fake_chat_completion)

    answer = "结论成立 [10.9999/unknown-doi, Preamble]"
    chunks = [[
        RetrievedChunk(
            text="known evidence",
            doi="10.1000/known-doi",
            title="Known",
            section_name="Preamble",
            chunk_index=0,
            distance=0.1,
        )
    ]]

    passed, issues = check_answer("demo", answer, chunks, client=object())

    assert passed is False
    assert len(issues) == 1
    assert issues[0]["citation"] == "[10.9999/unknown-doi, Preamble]"
    assert "not present in retrieved literature passages" in issues[0]["problem"]
    assert called["llm"] is False


def test_checker_calls_llm_when_all_cited_dois_exist(monkeypatch):
    called = {"llm": False}

    def fake_chat_completion(**kwargs):
        called["llm"] = True
        return '{"passed": true, "issues": []}'

    monkeypatch.setattr("agent_core.checker.chat_completion", fake_chat_completion)

    answer = "结论成立 [10.1000/known-doi, Preamble]"
    chunks = [[
        RetrievedChunk(
            text="known evidence",
            doi="10.1000/known-doi",
            title="Known",
            section_name="Preamble",
            chunk_index=0,
            distance=0.1,
        )
    ]]

    passed, issues = check_answer("demo", answer, chunks, client=object())

    assert passed is True
    assert issues == []
    assert called["llm"] is True



def test_checker_uses_no_retry_client_when_client_not_provided(monkeypatch):
    captured = {}
    fake_client = object()

    def fake_get_llm_client(*, max_retries=None):
        captured["max_retries"] = max_retries
        return fake_client

    def fake_chat_completion(**kwargs):
        captured["client"] = kwargs["client"]
        return '{"passed": true, "issues": []}'

    monkeypatch.setattr("agent_core.checker.get_llm_client", fake_get_llm_client)
    monkeypatch.setattr("agent_core.checker.chat_completion", fake_chat_completion)

    answer = "结论成立 [10.1000/known-doi, Preamble]"
    chunks = [[
        RetrievedChunk(
            text="known evidence",
            doi="10.1000/known-doi",
            title="Known",
            section_name="Preamble",
            chunk_index=0,
            distance=0.1,
        )
    ]]

    passed, issues = check_answer("demo", answer, chunks, client=None)

    assert passed is True
    assert issues == []
    assert captured["max_retries"] == 0
    assert captured["client"] is fake_client
