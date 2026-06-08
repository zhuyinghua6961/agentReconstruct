from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import httpx

from server.patent.file_contract import build_patent_file_contract
from server.patent.hybrid_synthesis import (
    PatentHybridSynthesisClient,
    build_patent_hybrid_synthesis_contract,
    build_patent_hybrid_synthesis_prompt,
)
from server.patent.pdf_service import PatentPdfService, build_pdf_synthesis_context


ROOT_DIR = Path(__file__).resolve().parents[1]


def _make_pdf_contract(*, pdf_path: Path, question: str = "请结合文件回答这个问题") -> object:
    return build_patent_file_contract(
        question=question,
        route="pdf_qa",
        source_scope="pdf",
        selected_file_ids=[11],
        primary_file_id=11,
        execution_files=[
            {
                "file_id": 11,
                "file_type": "pdf",
                "file_name": pdf_path.name,
                "local_path": str(pdf_path),
            }
        ],
        file_selection={"strategy": "explicit_selection", "selected_file_ids": [11], "source_scope": "pdf"},
        kb_enabled=False,
        allow_kb_verification=False,
    )


def _sample_contract(*, question: str = "请结合 PDF、表格和知识库回答这个问题") -> dict[str, object]:
    return build_patent_hybrid_synthesis_contract(
        question=question,
        source_scope="pdf+table+kb",
        pdf_answer="PDF 指出 LMFP/LFP 复配改善充电安全。",
        tabular_answer="表格显示 LMFP 120mAh、LFP 115mAh、NCM 140mAh。",
        kb_answer="知识库补充该方案常用于动力电池材料组合验证。",
        pdf_evidence_context="PDF 预览：LMFP/LFP 复配改善充电安全。",
        table_execution_context="表格预览：LMFP 120mAh；LFP 115mAh；NCM 140mAh。",
        pdf_synthesis_context="==== 文献 1: battery-paper.pdf ====\nLMFP/LFP 复配改善充电安全，并报告循环稳定性提升。",
        table_synthesis_context="匹配工作表: cells\n执行操作: aggregate\n容量结果：LMFP 120mAh；LFP 115mAh；NCM 140mAh。",
        kb_evidence_context="知识库预览：LMFP 常用于安全性与倍率平衡。",
        kb_synthesis_context="知识库补充：LMFP 常用于动力电池材料路线对比，但这里只能作为验证信息。",
        include_kb=True,
        kb_reference_instruction="知识库只能作为补充验证。",
        available_sources=["pdf", "table", "kb"],
        source_answer_modes={
            "pdf": "pdf_text_summary",
            "table": "table_execution_summary",
            "kb": "kb_qa",
        },
    )


def test_hybrid_synthesis_prompt_requires_file_precedence_and_source_boundaries():
    prompt = build_patent_hybrid_synthesis_prompt(synthesis_contract=_sample_contract())

    assert "文件证据优先" in prompt
    assert "知识库只能作为补充验证" in prompt
    assert "## 结论" in prompt
    assert "## 证据" in prompt
    assert "## 对比" in prompt
    assert "## 限制" in prompt


def test_hybrid_summary_prompt_requires_five_section_summary_shape():
    prompt = build_patent_hybrid_synthesis_prompt(synthesis_contract=_sample_contract(question="请总结这份文件"))

    assert "## 研究目的和背景" in prompt
    assert "## 研究方法/实验设计" in prompt
    assert "## 主要发现和结果" in prompt
    assert "## 结论和意义" in prompt
    assert "## 局限性" in prompt
    assert "注*" in prompt


def test_hybrid_synthesis_prompt_rejects_raw_execution_markers():
    prompt = build_patent_hybrid_synthesis_prompt(synthesis_contract=_sample_contract())

    assert "匹配工作表:" not in prompt
    assert "执行操作:" not in prompt
    assert "source_scope=" not in prompt
    assert "120mAh" in prompt


def test_build_hybrid_synthesis_contract_includes_internal_contexts_and_source_metadata():
    contract = _sample_contract()

    assert contract["pdf_synthesis_context"]
    assert contract["table_synthesis_context"]
    assert contract["kb_synthesis_context"]
    assert contract["synthesis_prompt_version"]
    assert contract["available_sources"] == ["pdf", "table", "kb"]
    assert contract["source_answer_modes"] == {
        "pdf": "pdf_text_summary",
        "table": "table_execution_summary",
        "kb": "kb_qa",
    }


def test_build_hybrid_synthesis_contract_uses_richer_pdf_context_than_public_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("PATENT_ORIGINAL_MINIO_ONLY", "false")
    pdf_path = tmp_path / "battery-paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nplaceholder\n")
    service = PatentPdfService(
        extract_pdf_text_fn=lambda _path, max_pages=10, exclude_references=True: (
            "This paper studies LMFP/LFP blending.\n"
            "It reports safer charging behavior and longer cycle retention.\n"
            "The discussion also includes boundary conditions and measurement setup."
        ),
        answer_question_fn=lambda **_kwargs: "PDF 结论：LMFP/LFP 复配改善了充电安全性。",
    )

    pdf_result = service.execute(contract=_make_pdf_contract(pdf_path=pdf_path), include_kb=False)
    public_preview = str(pdf_result["metadata"]["pdf_evidence_context"] or "")
    synthesis_context = build_pdf_synthesis_context(
        prepared_pdf_text=str(pdf_result["metadata"]["prepared_pdf_text"] or ""),
        pdf_text="",
    )
    contract = build_patent_hybrid_synthesis_contract(
        question="请结合文件回答这个问题",
        source_scope="pdf+table",
        pdf_answer=str(pdf_result["answer_text"] or ""),
        pdf_evidence_context=public_preview,
        pdf_synthesis_context=synthesis_context,
        tabular_answer="表格结论",
        table_execution_context="表格预览",
        table_synthesis_context="表格详细证据",
        available_sources=["pdf", "table"],
        source_answer_modes={"pdf": str(pdf_result["metadata"]["answer_mode"] or ""), "table": "table_execution_summary"},
    )

    assert contract["pdf_synthesis_context"]
    assert len(contract["pdf_synthesis_context"]) > len(public_preview)


def test_hybrid_synthesis_client_does_not_close_injected_http_client():
    shared_pool = SimpleNamespace(
        config=SimpleNamespace(
            connect_timeout_seconds=1.5,
            read_timeout_seconds=2.5,
            stream_read_timeout_seconds=9.5,
            write_timeout_seconds=3.5,
            pool_timeout_seconds=4.5,
        ),
        snapshot=lambda: {
            "pool_owner": "app",
            "client_owner": "shared",
            "shared_client_id": "hybrid-shared",
            "pid": 1,
            "bootstrap_source": "startup",
            "pool_timeout_count": 0,
            "pool_wait_ms": 0.0,
        },
        record_pool_wait=lambda **_kwargs: None,
        record_pool_timeout=lambda **_kwargs: None,
    )

    class _FakeHttpClient:
        def __init__(self) -> None:
            self.closed = False
            self.calls: list[dict[str, object]] = []
            self._patent_shared_pool = shared_pool

        def post(self, url, *, headers=None, json=None, timeout=None):
            self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
            return httpx.Response(
                200,
                request=httpx.Request("POST", str(url)),
                json={"choices": [{"message": {"content": "hybrid answer"}}]},
            )

        def close(self):
            self.closed = True

    shared = _FakeHttpClient()
    client = PatentHybridSynthesisClient(
        api_key="key",
        base_url="https://example.com",
        model="model",
        http_client=shared,
    )

    answer = client.answer(synthesis_contract=_sample_contract())

    assert answer == "hybrid answer"
    timeout = shared.calls[0]["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 1.5
    assert timeout.read == 2.5
    assert timeout.write == 3.5
    assert timeout.pool == 4.5
    client.close()
    assert shared.closed is False


def test_hybrid_synthesis_client_logs_model_call_success(caplog):
    class _FakeHttpClient:
        def post(self, url, *, headers=None, json=None, timeout=None):
            return httpx.Response(
                200,
                request=httpx.Request("POST", str(url)),
                json={"choices": [{"message": {"content": "hybrid answer"}}]},
            )

        def close(self):
            return None

    client = PatentHybridSynthesisClient(
        api_key="key",
        base_url="https://example.com",
        model="hybrid-model",
        http_client=_FakeHttpClient(),
    )

    with caplog.at_level(logging.INFO, logger="patent.hybrid_synthesis"):
        answer = client.answer(synthesis_contract=_sample_contract())

    assert answer == "hybrid answer"
    messages = [record.message for record in caplog.records if record.name == "patent.hybrid_synthesis"]
    assert any("model_call start" in message and "component=llm_hybrid" in message and "model=hybrid-model" in message for message in messages)
    assert any("model_call success" in message and "component=llm_hybrid" in message and "answer_chars=13" in message for message in messages)


def test_hybrid_synthesis_client_from_env_reads_hybrid_budget(monkeypatch):
    class _FakeHttpClient:
        def close(self):
            raise AssertionError("injected client should not be closed by this test")

    monkeypatch.setenv("LLM_API_KEY", "key")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.com")
    monkeypatch.setenv("LLM_MODEL", "model")
    monkeypatch.setenv("PATENT_HYBRID_MAX_TOKENS", "4096")

    client = PatentHybridSynthesisClient.from_env(http_client=_FakeHttpClient())

    assert client is not None
    assert client.runtime_signature()["max_tokens"] == 4096


def test_hybrid_synthesis_client_stage4_enables_thinking(monkeypatch):
    class _FakeHttpClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def post(self, url, *, headers=None, json=None, timeout=None):
            self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
            return httpx.Response(
                200,
                request=httpx.Request("POST", str(url)),
                json={"choices": [{"message": {"content": "hybrid answer"}}]},
            )

        def close(self):
            return None

    monkeypatch.setenv("LLM_IS_THINKING_MODEL", "true")
    monkeypatch.setenv("LLM_THINKING_ENABLED", "true")
    http_client = _FakeHttpClient()
    client = PatentHybridSynthesisClient(
        api_key="",
        base_url="https://example.com",
        model="model",
        max_tokens=3000,
        http_client=http_client,
    )

    answer = client.answer(synthesis_contract=_sample_contract())

    assert answer == "hybrid answer"
    call = http_client.calls[0]
    payload = call["json"]
    assert "Authorization" not in call["headers"]
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "high"
    assert payload["max_tokens"] == 8192
    assert "temperature" not in payload
    assert "top_p" not in payload


def test_hybrid_synthesis_client_from_env_prefers_unified_llm_namespace(monkeypatch):
    class _FakeHttpClient:
        def close(self):
            raise AssertionError("injected client should not be closed by this test")

    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "llm-model")
    monkeypatch.setenv("LLM_READ_TIMEOUT_SECONDS", "47")
    monkeypatch.setenv("PATENT_OPENAI_API_KEY", "patent-key")
    monkeypatch.setenv("PATENT_OPENAI_BASE_URL", "https://patent.example/v1")
    monkeypatch.setenv("PATENT_OPENAI_MODEL", "patent-model")
    monkeypatch.setenv("PATENT_OPENAI_TIMEOUT_SECONDS", "99")

    client = PatentHybridSynthesisClient.from_env(http_client=_FakeHttpClient())

    assert client is not None
    signature = client.runtime_signature()
    assert signature["model"] == "llm-model"
    assert signature["timeout_seconds"] == 47.0
    assert client._api_key == "llm-key"
    assert client._base_url == "https://llm.example/v1"


def test_config_shared_env_example_includes_tabular_and_hybrid_answer_knobs():
    content = (ROOT_DIR / "config.shared.env.example").read_text(encoding="utf-8")

    assert "PATENT_TABULAR_MAX_TOKENS=" in content
    assert "PATENT_TABULAR_TOP_P=" in content
    assert "PATENT_TABULAR_MAX_CONTEXT_CHARS=" in content
    assert "PATENT_HYBRID_TABLE_CONTEXT_CHARS=" in content
    assert "PATENT_HYBRID_MAX_TOKENS=" in content
