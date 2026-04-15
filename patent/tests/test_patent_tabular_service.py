from __future__ import annotations

from pathlib import Path

import server.patent.tabular_service as tabular_service_module
from server.patent.file_models import PatentExecutionFile, PatentFileContract
from server.patent.tabular_service import PatentTabularAnswerClient, PatentTabularService


def _make_contract(csv_path: Path, *, question: str = "哪个材料的容量更高") -> PatentFileContract:
    return PatentFileContract(
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33],
        primary_file_id=33,
        execution_files=[
            PatentExecutionFile(
                file_id=33,
                file_type="csv",
                file_name="claims.csv",
                family="table",
                payload={"local_path": str(csv_path)},
            )
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [33], "source_scope": "table"},
        kb_enabled=False,
        allow_kb_verification=False,
        question=question,
    )


def _write_csv(path: Path) -> None:
    path.write_text("material,capacity_mah,note\nLMFP,120,stable\nLFP,115,safe\n", encoding="utf-8")


def test_tabular_answer_client_from_env_uses_injected_http_client_without_taking_ownership(monkeypatch):
    class _FakeHttpClient:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    monkeypatch.setenv("PATENT_OPENAI_API_KEY", "key")
    monkeypatch.setenv("PATENT_OPENAI_BASE_URL", "https://example.com")
    monkeypatch.setenv("PATENT_OPENAI_MODEL", "tabular-model")
    http_client = _FakeHttpClient()

    client = PatentTabularAnswerClient.from_env(http_client=http_client)

    assert client is not None
    assert client._client is http_client
    client.close()
    assert http_client.closed is False


def test_tabular_service_prefers_answer_question_fn_before_answer_client(tmp_path):
    csv_path = tmp_path / "claims.csv"
    _write_csv(csv_path)

    class _ExplodingAnswerClient:
        def answer(self, **_kwargs):
            raise AssertionError("answer_client should not run when answer_question_fn is present")

    service = PatentTabularService(
        extract_table_text_fn=lambda *_args, **_kwargs: "文件: claims.csv\nLMFP 120mAh\nLFP 115mAh",
        answer_question_fn=lambda **_kwargs: "LLM answer from callable",
        answer_client=_ExplodingAnswerClient(),
    )

    result = service.execute(contract=_make_contract(csv_path), include_kb=False)

    assert result["metadata"]["answer_backend"] == "llm"
    assert "LLM answer from callable" in result["answer_text"]


def test_tabular_service_uses_answer_client_before_fallback(tmp_path):
    csv_path = tmp_path / "claims.csv"
    _write_csv(csv_path)

    class _FakeTabularClient:
        def answer(self, **_kwargs):
            return "LLM answer from client"

    service = PatentTabularService(
        extract_table_text_fn=lambda *_args, **_kwargs: "文件: claims.csv\nLMFP 120mAh\nLFP 115mAh",
        answer_client=_FakeTabularClient(),
    )

    result = service.execute(contract=_make_contract(csv_path), include_kb=False)

    assert result["metadata"]["answer_backend"] == "llm"
    assert "LLM answer from client" in result["answer_text"]


def test_tabular_service_marks_fallback_backend_when_client_missing(tmp_path):
    csv_path = tmp_path / "claims.csv"
    _write_csv(csv_path)
    service = PatentTabularService(
        extract_table_text_fn=lambda *_args, **_kwargs: "文件: claims.csv\nLMFP 120mAh\nLFP 115mAh",
        answer_question_fn=None,
        answer_client=None,
        auto_answer_client=False,
    )

    result = service.execute(contract=_make_contract(csv_path), include_kb=False)

    assert result["metadata"]["answer_backend"] == "fallback"
    assert result["answer_text"]


def test_tabular_service_marks_unavailable_when_client_errors_and_returns_fallback_answer(tmp_path):
    csv_path = tmp_path / "claims.csv"
    _write_csv(csv_path)

    class _ExplodingTabularClient:
        def answer(self, **_kwargs):
            raise RuntimeError("client boom")

    service = PatentTabularService(
        extract_table_text_fn=lambda *_args, **_kwargs: "文件: claims.csv\nLMFP 120mAh\nLFP 115mAh",
        answer_client=_ExplodingTabularClient(),
    )

    result = service.execute(contract=_make_contract(csv_path), include_kb=False)

    assert result["metadata"]["answer_backend"] == "unavailable"
    assert result["answer_text"]


def test_tabular_service_passes_rich_answer_context_to_answer_fn_and_keeps_public_context_compact(tmp_path):
    csv_path = tmp_path / "claims.csv"
    _write_csv(csv_path)
    captured: dict[str, str] = {}

    def _answer_question_fn(**kwargs):
        captured["table_text"] = str(kwargs.get("table_text") or "")
        return "表格分析结论"

    service = PatentTabularService(answer_question_fn=_answer_question_fn)
    result = service.execute(contract=_make_contract(csv_path), include_kb=False)

    assert "统计摘要:" in captured["table_text"]
    assert "代表性行:" in captured["table_text"]
    assert "统计摘要:" not in result["metadata"]["table_evidence_context"]
    assert result["metadata"]["table_answer_context_chars"] >= len(result["metadata"]["table_evidence_context"])
    assert result["metadata"]["table_synthesis_context_chars"] >= len(result["metadata"]["table_evidence_context"])


def test_tabular_service_marks_skip_cache_when_structured_context_loading_fails(tmp_path, monkeypatch):
    csv_path = tmp_path / "claims.csv"
    _write_csv(csv_path)

    def _boom(**_kwargs):
        raise RuntimeError("workbook boom")

    monkeypatch.setattr(tabular_service_module, "load_workbook_cached", _boom)
    service = PatentTabularService(auto_answer_client=False)

    result = service.execute(contract=_make_contract(csv_path), include_kb=False)

    assert result["metadata"]["answer_backend"] == "unavailable"
    assert result["_skip_file_route_cache"] is True
    assert "无法生成基于表格的回答" in result["answer_text"]
