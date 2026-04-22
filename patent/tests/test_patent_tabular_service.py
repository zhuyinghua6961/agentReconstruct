from __future__ import annotations

from pathlib import Path

import httpx

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


def _make_multi_contract(
    csv_a: Path,
    csv_b: Path,
    *,
    question: str = "对比一下这两个表格",
) -> PatentFileContract:
    return PatentFileContract(
        route="tabular_qa",
        source_scope="table",
        selected_file_ids=[33, 34],
        primary_file_id=33,
        execution_files=[
            PatentExecutionFile(
                file_id=33,
                file_type="csv",
                file_name="a.csv",
                family="table",
                payload={"local_path": str(csv_a)},
            ),
            PatentExecutionFile(
                file_id=34,
                file_type="csv",
                file_name="b.csv",
                family="table",
                payload={"local_path": str(csv_b)},
            ),
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [33, 34], "source_scope": "table"},
        kb_enabled=False,
        allow_kb_verification=False,
        question=question,
    )


def _write_csv(path: Path) -> None:
    path.write_text("material,capacity_mah,note\nLMFP,120,stable\nLFP,115,safe\n", encoding="utf-8")


def _compare_workbook(file_name: str, *, capacity_column: str) -> dict:
    return {
        "file_name": file_name,
        "file_type": "csv",
        "sheet_count": 1,
        "sheets": [
            {
                "sheet_name": "Sheet1",
                "headers": ["批次", capacity_column, "温度"],
                "rows": [
                    {"批次": "B1", capacity_column: "100", "温度": "25"},
                    {"批次": "B2", capacity_column: "120", "温度": "35"},
                ],
                "row_count": 2,
            }
        ],
    }


def _compare_profile(file_name: str, *, capacity_column: str) -> dict:
    return {
        "file_name": file_name,
        "sheet_count": 1,
        "sheets": [
            {
                "sheet_name": "Sheet1",
                "column_names": ["批次", capacity_column, "温度"],
                "numeric_columns": [capacity_column, "温度"],
                "date_like_columns": [],
                "text_columns": ["批次"],
                "columns": [
                    {"name": "批次", "normalized_name": "批次", "is_numeric": False, "is_date_like": False},
                    {"name": capacity_column, "normalized_name": capacity_column, "is_numeric": True, "is_date_like": False},
                    {"name": "温度", "normalized_name": "温度", "is_numeric": True, "is_date_like": False},
                ],
            }
        ],
    }


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


def test_tabular_answer_client_uses_injected_http_client_and_preserves_timeout_dimensions():
    class _FakeSharedPool:
        def __init__(self) -> None:
            self.config = type(
                "_Config",
                (),
                {
                    "connect_timeout_seconds": 1.5,
                    "read_timeout_seconds": 2.5,
                    "stream_read_timeout_seconds": 9.5,
                    "write_timeout_seconds": 3.5,
                    "pool_timeout_seconds": 4.5,
                },
            )()

        def snapshot(self) -> dict[str, object]:
            return {
                "pool_owner": "app",
                "client_owner": "shared",
                "shared_client_id": "tabular-shared",
                "pid": 1,
                "bootstrap_source": "startup",
                "pool_timeout_count": 0,
                "pool_wait_ms": 0.0,
            }

        def record_pool_wait(self, **_kwargs) -> None:
            return None

        def record_pool_timeout(self, **_kwargs) -> None:
            return None

    class _FakeHttpClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.closed = False
            self._patent_shared_pool = _FakeSharedPool()

        def post(self, url, *, headers=None, json=None, timeout=None):
            self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
            return httpx.Response(
                200,
                request=httpx.Request("POST", str(url)),
                json={"choices": [{"message": {"content": "table answer"}}]},
            )

        def close(self) -> None:
            self.closed = True

    http_client = _FakeHttpClient()
    client = PatentTabularAnswerClient(
        api_key="key",
        base_url="https://example.com",
        model="model",
        timeout_seconds=29.0,
        http_client=http_client,
    )

    answer = client.answer(
        question="哪个材料的容量更高",
        table_text="文件: claims.csv\nLMFP 120mAh\nLFP 115mAh",
        include_kb=False,
        route_hint="tabular_qa",
        source_scope="table",
    )

    assert answer == "table answer"
    assert len(http_client.calls) == 1
    timeout = http_client.calls[0]["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 1.5
    assert timeout.read == 2.5
    assert timeout.write == 3.5
    assert timeout.pool == 4.5
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

    assert "全表统计摘要:" in captured["table_text"]
    assert "代表性样例:" in captured["table_text"]
    assert "全表统计摘要:" not in result["metadata"]["table_evidence_context"]
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


def test_tabular_service_multi_table_compare_uses_request_scoped_descriptors_without_mutating_cached_workbooks(
    tmp_path,
    monkeypatch,
):
    csv_a = tmp_path / "a.csv"
    csv_b = tmp_path / "b.csv"
    _write_csv(csv_a)
    _write_csv(csv_b)

    cached_workbook_a = _compare_workbook("a.csv", capacity_column="容量")
    cached_workbook_b = _compare_workbook("b.csv", capacity_column="容量_Ah")
    profiles_by_name = {
        "a.csv": _compare_profile("a.csv", capacity_column="容量"),
        "b.csv": _compare_profile("b.csv", capacity_column="容量_Ah"),
    }
    captured: dict[str, object] = {}

    def _fake_load_workbook_cached(**kwargs):
        file_name = str(kwargs.get("file_name") or "")
        return cached_workbook_a if file_name == "a.csv" else cached_workbook_b

    def _fake_profile_workbook(workbook):
        return profiles_by_name[str(workbook.get("file_name") or "")]

    def _fake_plan_tabular_query(**kwargs):
        profiles = [dict(item) for item in (kwargs.get("profiles") or [])]
        captured["profiles"] = profiles
        captured["workbook_count"] = kwargs.get("workbook_count")
        return {
            "needs_clarification": True,
            "clarification_message": "请指定要对比的 sheet 名称。",
            "clarification_reason": "sheet_compare_ambiguous",
        }

    monkeypatch.setattr(tabular_service_module, "load_workbook_cached", _fake_load_workbook_cached)
    monkeypatch.setattr(tabular_service_module, "profile_workbook", _fake_profile_workbook)
    monkeypatch.setattr(tabular_service_module, "plan_tabular_query", _fake_plan_tabular_query)

    service = PatentTabularService(auto_answer_client=False)
    bundle = service._load_table_context_bundle(contract=_make_multi_contract(csv_a, csv_b))

    assert bundle["status"] == "clarification"
    assert captured["workbook_count"] == 2
    assert [profile["file_id"] for profile in captured["profiles"]] == [33, 34]
    assert [profile["file_name"] for profile in captured["profiles"]] == ["a.csv", "b.csv"]
    assert "file_id" not in cached_workbook_a
    assert "file_id" not in cached_workbook_b


def test_load_table_context_bundle_returns_unreadable_status_when_no_table_file_is_readable(tmp_path):
    missing_csv = tmp_path / "missing.csv"
    service = PatentTabularService(auto_answer_client=False)

    bundle = service._load_table_context_bundle(contract=_make_contract(missing_csv))

    assert bundle["status"] == "unreadable"
    assert bundle["answer_mode"] == "table_execution_unavailable"
    assert "表格原始内容" in bundle["user_message"]


def test_load_table_context_bundle_does_not_silently_downgrade_multi_table_compare_when_one_file_is_unreadable(tmp_path):
    csv_a = tmp_path / "a.csv"
    missing_csv = tmp_path / "missing.csv"
    _write_csv(csv_a)
    service = PatentTabularService(auto_answer_client=False)

    bundle = service._load_table_context_bundle(
        contract=_make_multi_contract(csv_a, missing_csv, question="对比一下这两个表格"),
    )

    assert bundle["status"] == "unreadable"
    assert bundle["answer_mode"] == "table_execution_unavailable"
    assert "对比" in bundle["user_message"] or "表格原始内容" in bundle["user_message"]


def test_tabular_service_returns_clarification_status_for_compare_plan_ambiguity(tmp_path, monkeypatch):
    csv_a = tmp_path / "a.csv"
    csv_b = tmp_path / "b.csv"
    _write_csv(csv_a)
    _write_csv(csv_b)

    monkeypatch.setattr(
        PatentTabularService,
        "_load_table_context_bundle",
        lambda self, *, contract: {
            "status": "clarification",
            "compact_evidence_context": "",
            "answer_context": "",
            "synthesis_context": "",
            "user_message": "请指定要对比的 sheet 名称。",
            "answer_mode": "table_execution_clarification",
            "_skip_file_route_cache": False,
        },
    )

    service = PatentTabularService(auto_answer_client=False)
    result = service.execute(contract=_make_multi_contract(csv_a, csv_b), include_kb=False)

    assert result["metadata"]["answer_mode"] == "table_execution_clarification"
    assert "请指定" in result["answer_text"]


def test_tabular_service_returns_compare_unavailable_not_unreadable_when_execution_is_logical_failure(
    tmp_path,
    monkeypatch,
):
    csv_a = tmp_path / "a.csv"
    csv_b = tmp_path / "b.csv"
    _write_csv(csv_a)
    _write_csv(csv_b)

    monkeypatch.setattr(
        PatentTabularService,
        "_load_table_context_bundle",
        lambda self, *, contract: {
            "status": "execution_unavailable",
            "compact_evidence_context": "",
            "answer_context": "",
            "synthesis_context": "",
            "user_message": "当前已读取到表格文件，但未能生成可用的表格对比结果，请补充更明确的对比维度。",
            "answer_mode": "table_execution_compare_unavailable",
            "_skip_file_route_cache": False,
        },
    )

    service = PatentTabularService(auto_answer_client=False)
    result = service.execute(contract=_make_multi_contract(csv_a, csv_b), include_kb=False)

    assert result["metadata"]["answer_mode"] == "table_execution_compare_unavailable"
    assert "表格原始内容" not in result["answer_text"]


def test_build_patent_tabular_prompt_compare_mode_with_summary_keywords_keeps_compare_sections():
    prompt = tabular_service_module._build_patent_tabular_prompt(
        question="总结并对比这两个表格",
        table_text="多表对比摘要:\n- 表格数: 2",
        route_hint="tabular_qa",
        source_scope="table",
        include_kb=False,
        operation_hint="compare_tables",
    )

    assert "## 结论" in prompt
    assert "## 研究目的和背景" not in prompt


def test_tabular_service_build_answer_keeps_compare_structure_even_with_summary_keywords():
    service = PatentTabularService(
        answer_question_fn=lambda **kwargs: "B1 批次下 b.csv 的平均容量高于 a.csv。",
    )

    answer, backend = service._build_answer(
        question="总结并对比这两个表格",
        table_text="多表对比摘要:\n- 表格数: 2\n- 分组对比: 是",
        include_kb=False,
        route_hint="tabular_qa",
        source_scope="table",
        operation_hint="compare_tables",
    )

    assert backend == "llm"
    assert "## 结论" in answer
    assert "## 研究目的和背景" not in answer
