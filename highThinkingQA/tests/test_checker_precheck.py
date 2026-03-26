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


def test_checker_only_passes_cited_doi_evidence_to_llm(monkeypatch):
    captured = {}

    def fake_chat_completion(**kwargs):
        captured["prompt"] = kwargs["prompt"]
        return '{"passed": true, "issues": []}'

    monkeypatch.setattr("agent_core.checker.chat_completion", fake_chat_completion)

    answer = "结论成立 [10.1000/cited-doi, Results]"
    chunks = [[
        RetrievedChunk(
            text="cited evidence text",
            doi="10.1000/cited-doi",
            title="Cited",
            section_name="Results",
            chunk_index=0,
            distance=0.1,
        ),
        RetrievedChunk(
            text="uncited evidence text",
            doi="10.2000/uncited-doi",
            title="Uncited",
            section_name="Methods",
            chunk_index=1,
            distance=0.2,
        ),
    ]]

    passed, issues = check_answer("demo", answer, chunks, client=object())

    assert passed is True
    assert issues == []
    assert "cited evidence text" in captured["prompt"]
    assert "10.1000/cited-doi" in captured["prompt"]
    assert "uncited evidence text" not in captured["prompt"]
    assert "10.2000/uncited-doi" not in captured["prompt"]



def test_checker_prefers_matching_section_evidence_for_cited_reference(monkeypatch):
    captured = {}

    def fake_chat_completion(**kwargs):
        captured["prompt"] = kwargs["prompt"]
        return '{"passed": true, "issues": []}'

    monkeypatch.setattr("agent_core.checker.chat_completion", fake_chat_completion)

    answer = "结论成立 [10.1000/known-doi, Results]"
    chunks = [[
        RetrievedChunk(
            text="results evidence text",
            doi="10.1000/known-doi",
            title="Known",
            section_name="Results",
            chunk_index=0,
            distance=0.1,
        ),
        RetrievedChunk(
            text="methods evidence text",
            doi="10.1000/known-doi",
            title="Known",
            section_name="Methods",
            chunk_index=1,
            distance=0.2,
        ),
    ]]

    passed, issues = check_answer("demo", answer, chunks, client=object())

    assert passed is True
    assert issues == []
    assert "results evidence text" in captured["prompt"]
    assert "methods evidence text" not in captured["prompt"]



def test_checker_falls_back_to_doi_scope_when_cited_section_has_no_exact_match(monkeypatch):
    captured = {}

    def fake_chat_completion(**kwargs):
        captured["prompt"] = kwargs["prompt"]
        return '{"passed": true, "issues": []}'

    monkeypatch.setattr("agent_core.checker.chat_completion", fake_chat_completion)

    answer = "结论成立 [10.1000/known-doi, Discussion]"
    chunks = [[
        RetrievedChunk(
            text="results evidence text",
            doi="10.1000/known-doi",
            title="Known",
            section_name="Results",
            chunk_index=0,
            distance=0.1,
        ),
        RetrievedChunk(
            text="methods evidence text",
            doi="10.1000/known-doi",
            title="Known",
            section_name="Methods",
            chunk_index=1,
            distance=0.2,
        ),
    ]]

    passed, issues = check_answer("demo", answer, chunks, client=object())

    assert passed is True
    assert issues == []
    assert "results evidence text" in captured["prompt"]
    assert "methods evidence text" in captured["prompt"]



def test_checker_splits_multiple_citation_blocks_into_multiple_llm_calls(monkeypatch):
    prompts = []

    def fake_chat_completion(**kwargs):
        prompts.append(kwargs["prompt"])
        return '{"passed": true, "issues": []}'

    monkeypatch.setattr("agent_core.checker.chat_completion", fake_chat_completion)

    answer = (
        "第一段结论 A [10.1000/a, Results]\n\n"
        "第二段结论 B [10.2000/b, Methods]"
    )
    chunks = [
        [
            RetrievedChunk(
                text="evidence for A",
                doi="10.1000/a",
                title="Paper A",
                section_name="Results",
                chunk_index=0,
                distance=0.1,
            )
        ],
        [
            RetrievedChunk(
                text="evidence for B",
                doi="10.2000/b",
                title="Paper B",
                section_name="Methods",
                chunk_index=1,
                distance=0.2,
            )
        ],
    ]

    passed, issues = check_answer("demo", answer, chunks, client=object())

    assert passed is True
    assert issues == []
    assert len(prompts) == 2
    assert "第一段结论 A" in prompts[0]
    assert "第二段结论 B" not in prompts[0]
    assert "evidence for A" in prompts[0]
    assert "evidence for B" not in prompts[0]
    assert "第二段结论 B" in prompts[1]
    assert "第一段结论 A" not in prompts[1]
    assert "evidence for B" in prompts[1]
    assert "evidence for A" not in prompts[1]



def test_checker_merges_issues_from_parallel_subchecks(monkeypatch):
    calls = []

    def fake_chat_completion(**kwargs):
        prompt = kwargs["prompt"]
        calls.append(prompt)
        if "10.1000/a" in prompt:
            return '{"passed": false, "issues": [{"claim": "第一段结论 A", "citation": "[10.1000/a, Results]", "problem": "fabrication"}]}'
        return '{"passed": false, "issues": [{"claim": "第二段结论 B", "citation": "[10.2000/b, Methods]", "problem": "data_mismatch"}]}'

    monkeypatch.setattr("agent_core.checker.chat_completion", fake_chat_completion)

    answer = (
        "第一段结论 A [10.1000/a, Results]\n\n"
        "第二段结论 B [10.2000/b, Methods]"
    )
    chunks = [
        [
            RetrievedChunk(
                text="evidence for A",
                doi="10.1000/a",
                title="Paper A",
                section_name="Results",
                chunk_index=0,
                distance=0.1,
            )
        ],
        [
            RetrievedChunk(
                text="evidence for B",
                doi="10.2000/b",
                title="Paper B",
                section_name="Methods",
                chunk_index=1,
                distance=0.2,
            )
        ],
    ]

    passed, issues = check_answer("demo", answer, chunks, client=object())

    assert passed is False
    assert len(calls) == 2
    assert len(issues) == 2
    assert {item["citation"] for item in issues} == {"[10.1000/a, Results]", "[10.2000/b, Methods]"}



def test_checker_disables_thinking_for_llm_audit(monkeypatch):
    captured = {}

    def fake_chat_completion(**kwargs):
        captured["enable_thinking"] = kwargs.get("enable_thinking")
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
    assert captured["enable_thinking"] is False


def test_checker_limits_chunk_count_per_slice(monkeypatch):
    captured = {}

    def fake_chat_completion(**kwargs):
        captured["prompt"] = kwargs["prompt"]
        return '{"passed": true, "issues": []}'

    monkeypatch.setattr("agent_core.checker.chat_completion", fake_chat_completion)

    answer = "结论成立 [10.1000/known-doi, Results]"
    chunks = [[
        RetrievedChunk(
            text=f"evidence chunk {idx} " + ("x" * 400),
            doi="10.1000/known-doi",
            title="Known",
            section_name="Results",
            chunk_index=idx,
            distance=0.01 * idx,
        )
        for idx in range(12)
    ]]

    passed, issues = check_answer("demo", answer, chunks, client=object())

    assert passed is True
    assert issues == []
    assert "evidence chunk 0" in captured["prompt"]
    assert "evidence chunk 7" in captured["prompt"]
    assert "evidence chunk 8" not in captured["prompt"]
    assert "evidence chunk 11" not in captured["prompt"]



def test_checker_truncates_long_chunk_text_before_prompt(monkeypatch):
    captured = {}

    def fake_chat_completion(**kwargs):
        captured["prompt"] = kwargs["prompt"]
        return '{"passed": true, "issues": []}'

    monkeypatch.setattr("agent_core.checker.chat_completion", fake_chat_completion)

    long_text = "prefix-" + ("A" * 1400) + "-tail-marker"
    answer = "结论成立 [10.1000/known-doi, Results]"
    chunks = [[
        RetrievedChunk(
            text=long_text,
            doi="10.1000/known-doi",
            title="Known",
            section_name="Results",
            chunk_index=0,
            distance=0.1,
        )
    ]]

    passed, issues = check_answer("demo", answer, chunks, client=object())

    assert passed is True
    assert issues == []
    assert "prefix-" in captured["prompt"]
    assert "-tail-marker" not in captured["prompt"]
