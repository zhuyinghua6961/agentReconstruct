from __future__ import annotations

import json
import re
import httpx
import pytest
from types import SimpleNamespace

from app.modules.generation_pipeline.synthesis_postprocess import (
    build_references_from_pdf_chunks,
    build_top5_reference_context,
    extract_cited_dois,
    log_top5_coverage,
    resolve_stage4_reference_policy,
)
from app.modules.generation_pipeline.reference_alignment import align_dois_with_pdf_chunks
from app.modules.generation_pipeline.synthesis_streaming import (
    _expert_draft_block_for_stage4,
    iter_stage4_synthesis_with_pdf_chunks,
)


@pytest.fixture(autouse=True)
def _default_allow_legacy_fallback(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_REQUIRE_FACTS_FOR_DOI_SYNTHESIS", "false")
    monkeypatch.setenv("QA_STAGE4_FACT_SYNTHESIS_INCLUDE_DEEP_ANSWER", "false")
    monkeypatch.setenv("QA_STAGE4_FACT_EXTRACTION_PER_DOI_ENABLED", "false")
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "false")
    # config.shared.env may disable citation verify; tests assume DOI cleanup / repair on
    monkeypatch.setenv("QA_STAGE4_CITATION_VERIFY_AFTER_SYNTHESIS", "true")


class _FakeClient:
    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self._chunks)


class _PoolTimeoutClient(_FakeClient):
    def __init__(self):
        super().__init__([])

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        raise httpx.PoolTimeout("pool exhausted")


class _FactThenStreamClient:
    def __init__(self, *, facts_json: str, answer_chunks):
        self._facts_json = facts_json
        self._answer_chunks = answer_chunks
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream") is False:
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self._facts_json))])
        return iter(self._answer_chunks)


class _FactPerDoiRoundClient:
    """Return one fact card per extraction; doi matches first DOI line in the prompt body."""

    def __init__(self, *, answer_chunks):
        self._answer_chunks = answer_chunks
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream") is False:
            body = str(kwargs["messages"][1]["content"] or "")
            m = re.search(r"DOI:\s*([\w./+-]+)", body)
            doi = m.group(1) if m else "10.1/a"
            payload = json.dumps(
                [{"claim": f"从文献{doi}提取的要点", "doi": doi, "use_allowed": "answer_fact"}],
                ensure_ascii=False,
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=payload))])
        return iter(self._answer_chunks)


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def _chunk(text: str):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])


def _escape_braces(text: str) -> str:
    return str(text or "").replace("{", "{{").replace("}", "}}")


def _format_pdf_chunks_evidence(pdf_chunks: dict[str, list[dict]], user_question: str = "") -> str:
    parts = [f"用户问题：{user_question}"] if user_question else []
    for doi, chunks in pdf_chunks.items():
        for chunk in chunks:
            parts.append(f"DOI: {doi}\nPAGE: {chunk.get('page', 0)}\n{chunk.get('text', '')}")
    return "\n\n".join(parts).strip()


class _CaptureLogger:
    def __init__(self):
        self.records = []

    def info(self, msg, *args, **kwargs):
        self.records.append(("info", msg % args if args else msg))

    def warning(self, msg, *args, **kwargs):
        self.records.append(("warning", msg % args if args else msg))

    def error(self, msg, *args, **kwargs):
        self.records.append(("error", msg % args if args else msg))

    def debug(self, msg, *args, **kwargs):
        self.records.append(("debug", msg % args if args else msg))


def _logger():
    return _CaptureLogger()


def test_stage4_synthesis_streams_content_and_final_result(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FakeClient([_chunk("结论"), _chunk(" (doi=10.1/a)")])
    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="what is lfp?",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "evidence", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    assert outputs[0] == "结论"
    assert outputs[1] == " (doi=10.1/a)"
    assert outputs[-1]["success"] is True
    assert outputs[-1]["final_answer"] == "结论 (doi=10.1/a)"
    assert outputs[-1]["references"][0]["doi"] == "10.1/a"


def test_stage4_synthesis_logs_llm_request_first_chunk_and_completion(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FakeClient([_chunk("结论"), _chunk(" (doi=10.1/a)")])
    logger = _logger()

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="what is lfp?",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "evidence", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=logger,
        )
    )

    assert outputs[-1]["success"] is True
    info_messages = [message for level, message in logger.records if level == "info"]
    assert any("stage4 llm request start" in message and "model=m" in message for message in info_messages)
    assert any("stage4 llm first chunk received" in message and "chunk_chars=" in message for message in info_messages)
    assert any("stage4 llm stream completed" in message and "elapsed_ms=" in message for message in info_messages)


def test_stage4_synthesis_returns_cancelled_result():
    client = _FakeClient([_chunk("ignored")])
    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="q",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "evidence"}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            should_cancel=lambda: True,
            logger=_logger(),
        )
    )

    assert outputs == [{"success": False, "cancelled": True, "error": "cancelled"}]


def test_stage4_synthesis_includes_graph_fact_block_in_prompt(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FakeClient([_chunk("结论"), _chunk(" (doi=10.1/a)")])
    list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="what is lfp?",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "evidence", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            graph_fact_block="structured graph facts",
            logger=_logger(),
        )
    )

    prompt = client.calls[-1]["messages"][1]["content"]
    assert "structured graph facts" in prompt


def test_stage4_synthesis_includes_comparison_evidence_contract(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FakeClient([_chunk("结论"), _chunk(" (doi=10.1/a)")])
    list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="草酸亚铁、铁红作为原料各有什么优劣势？",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "evidence", "page": 1}]},
            retrieval_results={
                "comparison_groups": [
                    {
                        "label": "草酸亚铁",
                        "aliases": ["FeC2O4"],
                        "evidence_status": "sufficient",
                        "doi_candidates": ["10.1/a"],
                        "md_hits": [{"doi": "10.1/a", "text": "ferrous oxalate evidence"}],
                    },
                    {
                        "label": "铁红",
                        "aliases": ["Fe2O3"],
                        "evidence_status": "insufficient",
                        "missing_evidence_reason": "abstract_hits_below_threshold",
                        "doi_candidates": [],
                        "md_hits": [],
                    },
                ]
            },
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    prompt = client.calls[-1]["messages"][1]["content"]
    assert "多对象对比证据包" in prompt
    assert "草酸亚铁" in prompt
    assert "铁红" in prompt
    assert "abstract_hits_below_threshold" in prompt
    assert "必须分别覆盖每个对比对象" in prompt


def test_stage4_synthesis_appends_comparison_validation_note(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FakeClient([_chunk("草酸亚铁有还原气氛优势"), _chunk(" (doi=10.1/a)")])
    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="草酸亚铁、铁红作为原料各有什么优劣势？",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "evidence", "page": 1}]},
            retrieval_results={
                "comparison_groups": [
                    {"label": "草酸亚铁", "evidence_status": "sufficient", "doi_candidates": ["10.1/a"]},
                    {
                        "label": "铁红",
                        "evidence_status": "insufficient",
                        "missing_evidence_reason": "abstract_hits_below_threshold",
                        "doi_candidates": [],
                    },
                ]
            },
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    assert outputs[-1]["success"] is True
    assert "证据覆盖提示" in outputs[-1]["final_answer"]
    assert "铁红" in outputs[-1]["final_answer"]


def test_extract_cited_dois_and_build_references():
    answer = "A (doi=10.1/a). B (doi=10.1_a) C"
    cited, _ = extract_cited_dois(answer, logger=_logger())
    references = build_references_from_pdf_chunks(
        cited_dois=cited,
        pdf_chunks={"10.1/a": [{"text": "sample evidence text", "page": 1}]},
    )

    assert set(cited) == {"10.1/a"}
    assert {item["doi"] for item in references} == {"10.1/a"}


def test_stage4_default_min_citations_when_env_unset(monkeypatch):
    monkeypatch.delenv("QA_STAGE4_MIN_CITATIONS", raising=False)

    topk, min_citations, element_guard = resolve_stage4_reference_policy(topk=12)

    assert topk == 12
    assert min_citations == 4
    assert element_guard is True


def test_build_top5_reference_context_uses_default_min_citations(monkeypatch):
    monkeypatch.delenv("QA_STAGE4_MIN_CITATIONS", raising=False)

    _scores, reference_text = build_top5_reference_context(
        retrieval_results={
            "claim_to_results": {
                "c1": {
                    "distances": [0.1, 0.1, 0.1, 0.1],
                    "metadatas": [
                        {"doi": "10.1/a"},
                        {"doi": "10.1/b"},
                        {"doi": "10.1/c"},
                        {"doi": "10.1/d"},
                    ],
                }
            }
        },
        logger=_logger(),
        topk=12,
    )

    assert len(_scores) >= 4
    assert "必须至少引用 4 篇不同文献" in reference_text


def test_build_top_reference_context_intersects_with_pdf_chunks():
    """Top-ref list must only include DOIs present in pdf_chunks (citation-verify allowlist)."""
    retrieval = {
        "claim_to_results": {
            "c1": {
                "distances": [0.05, 0.1],
                "metadatas": [{"doi": "10.12/x-high-rank"}, {"doi": "10.12/y-low-rank-in-pdf"}],
            }
        }
    }
    pdf_chunks = {
        "10.12/y-low-rank-in-pdf": [{"text": "only this PDF exists", "page": 1}],
    }
    scores, reference_text = build_top5_reference_context(
        retrieval_results=retrieval,
        logger=_logger(),
        pdf_chunks=pdf_chunks,
        topk=5,
        min_citations=4,
        user_question="",
    )
    assert len(scores) == 1
    assert scores[0][0] == "10.12/y-low-rank-in-pdf"
    assert "10.12/x-high-rank" not in reference_text
    assert "10.12/y-low-rank-in-pdf" in reference_text


def test_stage4_fact_mode_removes_dois_outside_fact_list(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FactThenStreamClient(
        facts_json='[{"fact":"A事实明确来自A文献","doi":"10.1/a"}]',
        answer_chunks=[_chunk("A事实明确来自A文献 (doi=10.1/b)")],
    )

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="what is lfp?",
            deep_answer="draft",
            pdf_chunks={
                "10.1/a": [{"text": "A事实明确来自A文献", "page": 1}],
                "10.1/b": [{"text": "B文献是别的内容", "page": 2}],
            },
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    assert outputs[-1]["success"] is True
    assert outputs[-1]["final_answer"] == "A事实明确来自A文献"
    assert outputs[-1]["references"] == []
    assert client.calls[1]["messages"][1]["content"].count("10.1/a") >= 1
    assert "10.1/b" not in client.calls[1]["messages"][1]["content"]


def test_stage4_fact_mode_removes_bare_dois_outside_fact_list(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FactThenStreamClient(
        facts_json='[{"fact":"A事实明确来自A文献","doi":"10.1/a"}]',
        answer_chunks=[_chunk("A事实明确来自A文献 (10.1/b)")],
    )

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="what is lfp?",
            deep_answer="draft",
            pdf_chunks={
                "10.1/a": [{"text": "A事实明确来自A文献", "page": 1}],
                "10.1/b": [{"text": "B文献是别的内容", "page": 2}],
            },
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    assert outputs[-1]["success"] is True
    assert outputs[-1]["final_answer"] == "A事实明确来自A文献"
    assert outputs[-1]["references"] == []


def test_stage4_fact_mode_normalizes_allowed_bare_doi(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FactThenStreamClient(
        facts_json='[{"fact":"A事实明确来自A文献","doi":"10.1/a"}]',
        answer_chunks=[_chunk("A事实明确来自A文献 (10.1/a)")],
    )

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="what is lfp?",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "A事实明确来自A文献", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    assert outputs[-1]["success"] is True
    assert outputs[-1]["final_answer"] == "A事实明确来自A文献 (doi=10.1/a)"
    assert [item["doi"] for item in outputs[-1]["references"]] == ["10.1/a"]


def test_stage4_extract_cited_dois_accepts_bare_parenthesized_doi():
    assert extract_cited_dois("A事实明确来自A文献 (10.1/a)", _logger())[0] == ["10.1/a"]


def test_stage4_fact_mode_does_not_programmatically_pad_citations(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "2")
    client = _FactThenStreamClient(
        facts_json='[{"fact":"A事实明确来自A文献","doi":"10.1/a"}]',
        answer_chunks=[_chunk("A事实明确来自A文献 (doi=10.1/a)")],
    )

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="what is lfp?",
            deep_answer="draft",
            pdf_chunks={
                "10.1/a": [{"text": "A事实明确来自A文献", "page": 1}],
                "10.1/b": [{"text": "B文献是别的内容", "page": 2}],
            },
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            programmatic_insert_dois_fn=lambda answer, retrieval_results, similarity_threshold=None, question=None: answer
            + " 第二句 (doi=10.1/b)",
            logger=_logger(),
        )
    )

    assert outputs[-1]["success"] is True
    assert outputs[-1]["final_answer"] == "A事实明确来自A文献 (doi=10.1/a)"
    assert [item["doi"] for item in outputs[-1]["references"]] == ["10.1/a"]


def test_stage4_fact_mode_system_prompt_does_not_force_citation_padding(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "3")
    client = _FactThenStreamClient(
        facts_json='[{"fact":"A事实明确来自A文献","doi":"10.1/a"}]',
        answer_chunks=[_chunk("A事实明确来自A文献 (doi=10.1/a)")],
    )

    list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="what is lfp?",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "A事实明确来自A文献", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    system_prompt = client.calls[1]["messages"][0]["content"]
    assert "事实列表中没有的 DOI 禁止引用" in system_prompt
    assert "不要为了满足引用数量" in system_prompt
    assert "不要为了满足定量信息要求" in system_prompt


def test_stage4_fact_mode_prompt_excludes_deep_answer_claims(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FactThenStreamClient(
        facts_json='[{"fact":"FePO4可作为铁源和磷源","doi":"10.1/a"}]',
        answer_chunks=[_chunk("FePO4可作为铁源和磷源 (doi=10.1/a)")],
    )

    list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="磷酸铁路线有什么优劣势？",
            deep_answer="当前动力电池级LiFePO4生产中超过80%采用FePO4路线，成本降低15%",
            pdf_chunks={"10.1/a": [{"text": "FePO4可作为铁源和磷源", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    prompt = client.calls[1]["messages"][1]["content"]
    assert "可引用事实卡片" in prompt
    assert "超过80%" not in prompt
    assert "成本降低15%" not in prompt


def test_expert_draft_block_disabled_by_default(monkeypatch):
    monkeypatch.delenv("QA_STAGE4_FACT_SYNTHESIS_INCLUDE_DEEP_ANSWER", raising=False)
    assert "未注入" in _expert_draft_block_for_stage4(deep_answer="任何预回答正文")


def test_expert_draft_block_includes_text_when_enabled(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_FACT_SYNTHESIS_INCLUDE_DEEP_ANSWER", "true")
    assert "UNIQUE_DRAFT_LINE" in _expert_draft_block_for_stage4(deep_answer="  UNIQUE_DRAFT_LINE  ")


def test_expert_draft_block_truncates_when_enabled(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_FACT_SYNTHESIS_INCLUDE_DEEP_ANSWER", "true")
    monkeypatch.setenv("QA_STAGE4_EXPERT_DRAFT_MAX_CHARS", "5")
    out = _expert_draft_block_for_stage4(deep_answer="abcdefghijklmnop")
    assert "截断" in out
    assert "abcde" in out


def test_stage4_fact_prompt_includes_expert_draft_when_env_enabled(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    monkeypatch.setenv("QA_STAGE4_FACT_SYNTHESIS_INCLUDE_DEEP_ANSWER", "true")
    client = _FactThenStreamClient(
        facts_json='[{"claim":"FePO4可作为铁源和磷源","doi":"10.1/a"}]',
        answer_chunks=[_chunk("FePO4可作为铁源和磷源 (doi=10.1/a)")],
    )

    list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="问题？",
            deep_answer="## 预回答小节\n专家初稿里的一段分析用于衔接。",
            pdf_chunks={"10.1/a": [{"text": "FePO4可作为铁源和磷源", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="legacy {user_question}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    prompt = client.calls[1]["messages"][1]["content"]
    assert "专家初稿" in prompt
    assert "预回答小节" in prompt


def test_stage4_fact_mode_includes_answer_plan_as_structure_not_fact_source(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FactThenStreamClient(
        facts_json='[{"claim":"FePO4可作为铁源和磷源","doi":"10.1/a"}]',
        answer_chunks=[_chunk("FePO4可作为铁源和磷源 (doi=10.1/a)")],
    )

    list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="磷酸铁路线有什么优劣势？",
            deep_answer="磷酸铁路线成本降低15%",
            pdf_chunks={"10.1/a": [{"text": "FePO4可作为铁源和磷源", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
            answer_plan={
                "answer_type": "process_comparison",
                "dimensions": [{"name": "成本", "evidence_needed": "原料成本数据"}],
                "summary_plan": {"decision_axes": ["高性能选型"]},
            },
        )
    )

    prompt = client.calls[1]["messages"][1]["content"]
    assert "结构化分析计划" in prompt
    assert "原料成本数据" in prompt
    assert "高性能选型" in prompt
    assert "不能作为事实来源" in prompt
    assert "成本降低15%" not in prompt


def test_stage4_fact_mode_requires_supported_points_separate_from_evidence_gaps(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FactThenStreamClient(
        facts_json='[{"claim":"酸洗铁红可通过掺杂策略制备磷酸铁锂正极材料","doi":"10.1/red"}]',
        answer_chunks=[_chunk("酸洗铁红可通过掺杂策略制备磷酸铁锂正极材料 (doi=10.1/red)")],
    )

    list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="铁红作为原料制备磷酸铁锂有什么优劣势？",
            deep_answer="",
            pdf_chunks={"10.1/red": [{"text": "酸洗铁红可通过掺杂策略制备磷酸铁锂正极材料", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    prompt = client.calls[1]["messages"][1]["content"]
    assert "优劣势与对比类问题（灵活叙述，勿套固定栏目）" in prompt
    assert "不要把「证据缺口」写成好像被引用的文献证明了「缺点」" in prompt
    assert "可据此推断" in prompt
    assert "严禁对每个对象把" in prompt
    assert "禁止样板结构" in prompt


def test_stage4_fact_extraction_prompt_includes_question_and_comparison_objects(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FactThenStreamClient(
        facts_json='[{"fact":"FePO4可作为铁源和磷源","doi":"10.1/a"}]',
        answer_chunks=[_chunk("FePO4可作为铁源和磷源 (doi=10.1/a)")],
    )

    list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="磷酸铁、草酸亚铁作为原料各有什么优劣势？",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "FePO4可作为铁源和磷源", "page": 1}]},
            retrieval_results={
                "comparison_groups": [
                    {"label": "磷酸铁", "aliases": ["FePO4"]},
                    {"label": "草酸亚铁", "aliases": ["FeC2O4"]},
                ]
            },
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    extraction_prompt = client.calls[0]["messages"][1]["content"]
    assert "原始问题：磷酸铁、草酸亚铁作为原料各有什么优劣势？" in extraction_prompt
    assert "对比对象：磷酸铁（FePO4）；草酸亚铁（FeC2O4）" in extraction_prompt
    assert "attributes" in extraction_prompt
    assert "use_allowed" in extraction_prompt
    assert "不要求原文直接使用用户问题中的问法" in extraction_prompt
    assert "10.xxx_yyy" in extraction_prompt


def test_stage4_fact_mode_empty_facts_does_not_fallback_to_legacy_doi_generation(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_REQUIRE_FACTS_FOR_DOI_SYNTHESIS", "true")
    monkeypatch.setenv("QA_STAGE4_EMPTY_FACTS_FALLBACK_MODE", "evidence_insufficient")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FactThenStreamClient(
        facts_json="[]",
        answer_chunks=[_chunk("legacy answer (doi=10.1/a)")],
    )

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="磷酸铁路线有什么优劣势？",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "FePO4可作为铁源和磷源", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    assert len(client.calls) == 1
    assert outputs[-1]["success"] is True
    assert "证据不足" in outputs[-1]["final_answer"]
    assert outputs[-1]["references"] == []


def test_stage4_empty_fact_cards_use_restricted_synthesis_with_doi_validation(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_REQUIRE_FACTS_FOR_DOI_SYNTHESIS", "true")
    monkeypatch.setenv("QA_STAGE4_EMPTY_FACTS_FALLBACK_MODE", "restricted_synthesis")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FactThenStreamClient(
        facts_json="[]",
        answer_chunks=[
            _chunk("FePO4 可作为铁源和磷源 (doi=10.1/a)\n"),
            _chunk("这条无关结论不应保留无效引用 (doi=10.9/missing)"),
        ],
    )

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="磷酸铁作为原料有什么优劣势？",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "FePO4 可作为铁源和磷源", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    assert len(client.calls) == 2
    assert client.calls[1]["stream"] is True
    restricted_prompt = client.calls[1]["messages"][1]["content"]
    assert "文献综述" in restricted_prompt
    assert "主要发现" in restricted_prompt
    assert "深度分析" in restricted_prompt
    assert "总结与建议" in restricted_prompt
    assert "问题类型" in restricted_prompt
    assert "回答重点" in restricted_prompt
    assert "PDF原文" in restricted_prompt
    assert "详细优先" in restricted_prompt
    assert "工艺参数" in restricted_prompt
    assert "性能数据" in restricted_prompt
    assert "不要输出对比表" in restricted_prompt
    assert outputs[-1]["success"] is True
    assert "FePO4 可作为铁源和磷源 (doi=10.1/a)" in outputs[-1]["final_answer"]
    assert "10.9/missing" not in outputs[-1]["final_answer"]
    assert outputs[-1]["references"] == [{"doi": "10.1/a", "chunk_count": 1, "sample_text": "FePO4 可作为铁源和磷源..."}]


def test_stage4_fact_synthesis_prompt_requests_controlled_engineering_analysis(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FactThenStreamClient(
        facts_json='[{"claim":"FePO4 固相路线适合规模化生产","doi":"10.1/a","use_allowed":"answer_fact","not_allowed":["不能据此推出吨成本最低"]}]',
        answer_chunks=[_chunk("FePO4 固相路线适合规模化生产 (doi=10.1/a)")],
    )

    list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="磷酸铁、草酸亚铁、铁红作为磷酸铁锂原料各有什么优劣势？",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "FePO4 solid-state route is suitable for large-scale production.", "page": 1}]},
            retrieval_results={
                "comparison_groups": [
                    {"label": "磷酸铁", "evidence_status": "sufficient"},
                    {"label": "草酸亚铁", "evidence_status": "insufficient", "missing_evidence_reason": "abstract_hits_below_threshold"},
                    {"label": "铁红", "evidence_status": "insufficient", "missing_evidence_reason": "abstract_hits_below_threshold"},
                ]
            },
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    synthesis_prompt = client.calls[1]["messages"][1]["content"]
    assert "文献综述" in synthesis_prompt
    assert "主要发现" in synthesis_prompt
    assert "深度分析" in synthesis_prompt
    assert "总结与建议" in synthesis_prompt
    assert "问题类型" in synthesis_prompt
    assert "回答重点" in synthesis_prompt
    assert "PDF原文" in synthesis_prompt
    assert "详细优先" in synthesis_prompt
    assert "工艺参数" in synthesis_prompt
    assert "性能数据" in synthesis_prompt
    assert "不要输出对比表" in synthesis_prompt
    assert "不能据此推出吨成本最低" in synthesis_prompt


def test_stage4_evidence_card_accepts_dynamic_attributes(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FactThenStreamClient(
        facts_json="""[
          {
            "claim": "FePO4 前驱体可作为 LiFePO4 合成的铁源和磷源",
            "source_quote": "FePO4 precursor was used as iron and phosphate source for LiFePO4 synthesis.",
            "doi": "10.1/a",
            "relevance_to_question": "这条证据可用于回答磷酸铁作为原料的反应路径特点",
            "use_allowed": "cautious_inference",
            "not_allowed": ["不能据此推出成本优势"],
            "attributes": {
              "material": "FePO4",
              "route_role": "iron and phosphate source",
              "observed_context": "LiFePO4 synthesis"
            }
          }
        ]""",
        answer_chunks=[_chunk("FePO4 前驱体可作为 LiFePO4 合成的铁源和磷源 (doi=10.1/a)")],
    )

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="磷酸铁作为原料有什么优劣势？",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "FePO4 precursor was used as iron and phosphate source for LiFePO4 synthesis.", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    synthesis_prompt = client.calls[1]["messages"][1]["content"]
    assert outputs[-1]["success"] is True
    assert outputs[-1]["final_answer"] == "FePO4 前驱体可作为 LiFePO4 合成的铁源和磷源 (doi=10.1/a)"
    assert "claim=FePO4 前驱体可作为 LiFePO4 合成的铁源和磷源" in synthesis_prompt
    assert "attributes=" in synthesis_prompt
    assert "route_role" in synthesis_prompt
    assert "不能据此推出成本优势" in synthesis_prompt


def test_stage4_evidence_card_accepts_underscore_doi_from_store(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FactThenStreamClient(
        facts_json='[{"claim":"FePO4 可作为铁源和磷源","doi":"10.1021_ie500503b","use_allowed":"answer_fact"}]',
        answer_chunks=[_chunk("FePO4 可作为铁源和磷源 (doi=10.1021/ie500503b)")],
    )

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="磷酸铁作为原料有什么优劣势？",
            deep_answer="draft",
            pdf_chunks={"10.1021_ie500503b": [{"text": "FePO4 precursor was used as iron and phosphate source", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    assert outputs[-1]["success"] is True
    assert outputs[-1]["references"]
    assert outputs[-1]["references"][0]["doi"] == "10.1021/ie500503b"


def test_stage4_fact_mode_removes_cited_sentence_with_numbers_missing_from_fact(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FactThenStreamClient(
        facts_json='[{"fact":"FePO4可作为铁源和磷源","doi":"10.1/a"}]',
        answer_chunks=[
            _chunk("当前动力电池级LiFePO4生产中超过80%采用FePO4路线，成本降低15% (doi=10.1/a)\n"),
            _chunk("FePO4可作为铁源和磷源 (doi=10.1/a)"),
        ],
    )

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="磷酸铁路线有什么优劣势？",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "FePO4可作为铁源和磷源", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    assert outputs[-1]["success"] is True
    assert "超过80%" not in outputs[-1]["final_answer"]
    assert "成本降低15%" not in outputs[-1]["final_answer"]
    assert outputs[-1]["final_answer"] == "FePO4可作为铁源和磷源 (doi=10.1/a)"


def test_stage4_fact_mode_removes_cited_sentence_with_low_fact_overlap(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FactThenStreamClient(
        facts_json='[{"fact":"FePO4可作为铁源和磷源","doi":"10.1/a"}]',
        answer_chunks=[
            _chunk("铁红路线原料成本最低 (doi=10.1/a)\n"),
            _chunk("FePO4可作为铁源和磷源 (doi=10.1/a)"),
        ],
    )

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="磷酸铁路线有什么优劣势？",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "FePO4可作为铁源和磷源", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    assert outputs[-1]["success"] is True
    assert "铁红路线原料成本最低" not in outputs[-1]["final_answer"]
    assert outputs[-1]["final_answer"] == "FePO4可作为铁源和磷源 (doi=10.1/a)"


def test_stage4_fact_mode_keeps_supported_cited_sentence_when_same_line_has_engineering_inference(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FactThenStreamClient(
        facts_json='[{"fact":"铁沉淀效率达99%","doi":"10.1/a"}]',
        answer_chunks=[
            _chunk(
                "草酸亚铁可通过草酸沉淀法从含铁溶液中获得，铁沉淀效率达99%，且具有层状结构特征 (doi=10.1/a)。"
                "工程推断：高沉淀效率可能意味着铁元素利用率较好，但还需要热分解和电化学性能数据。"
            )
        ],
    )

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="草酸亚铁作为原料有什么优劣势？",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "铁沉淀效率达99%", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )

    assert outputs[-1]["success"] is True
    assert "铁沉淀效率达99%" in outputs[-1]["final_answer"]
    assert "(doi=10.1/a)" in outputs[-1]["final_answer"]
    assert "工程推断：高沉淀效率可能意味着铁元素利用率较好" in outputs[-1]["final_answer"]



def test_stage4_synthesis_falls_back_to_pdf_alignment_when_llm_omits_doi(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FakeClient([_chunk("结论没有引用")])

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="what is lfp?",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "evidence", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            align_dois_with_pdf_chunks_fn=lambda answer, pdf_chunks, user_question="": answer + " (doi=10.1/a)",
            logger=_logger(),
        )
    )

    assert outputs[-1]["success"] is True
    assert outputs[-1]["final_answer"] == "结论没有引用 (doi=10.1/a)"
    assert outputs[-1]["references"][0]["doi"] == "10.1/a"


def test_stage4_synthesis_uses_programmatic_doi_insertion_when_llm_citations_insufficient(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "2")
    client = _FakeClient([_chunk("结论只有一个引用 (doi=10.1/a)")])

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="what is lfp?",
            deep_answer="draft",
            pdf_chunks={
                "10.1/a": [{"text": "evidence a", "page": 1}],
                "10.1/b": [{"text": "evidence b", "page": 2}],
            },
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            programmatic_insert_dois_fn=lambda answer, retrieval_results, similarity_threshold=None, question=None: answer + " 第二句 (doi=10.1/b)",
            logger=_logger(),
        )
    )

    assert outputs[-1]["success"] is True
    assert outputs[-1]["final_answer"].endswith("第二句 (doi=10.1/b)")
    assert {item["doi"] for item in outputs[-1]["references"]} == {"10.1/a", "10.1/b"}


def test_stage4_citation_verify_false_skips_cleanup_repair_and_align(monkeypatch):
    """关闭 QA_STAGE4_CITATION_VERIFY_AFTER_SYNTHESIS 时不做 DOI 清洗、程序化补引用与 align 回退。"""
    monkeypatch.setenv("QA_STAGE4_CITATION_VERIFY_AFTER_SYNTHESIS", "false")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "2")
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    client = _FactThenStreamClient(
        facts_json='[{"fact":"A事实","doi":"10.1/a"}]',
        answer_chunks=[_chunk("A事实 (doi=10.1/b)")],
    )

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="what is lfp?",
            deep_answer="draft",
            pdf_chunks={
                "10.1/a": [{"text": "A事实", "page": 1}],
                "10.1/b": [{"text": "other", "page": 2}],
            },
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            programmatic_insert_dois_fn=(
                lambda answer, retrieval_results, similarity_threshold=None, question=None: answer + " REPAIR_MARKER"
            ),
            align_dois_with_pdf_chunks_fn=lambda answer, pdf_chunks, user_question="": answer + " ALIGN_MARKER",
            logger=_logger(),
        )
    )

    assert outputs[-1]["success"] is True
    assert outputs[-1]["final_answer"] == "A事实 (doi=10.1/b)"
    assert "REPAIR_MARKER" not in outputs[-1]["final_answer"]
    assert "ALIGN_MARKER" not in outputs[-1]["final_answer"]


def test_stage4_synthesis_keeps_success_when_reference_building_raises(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FakeClient([_chunk("结论 (doi=10.1/a)")])

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="what is lfp?",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "evidence", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=lambda cited_dois, pdf_chunks: (_ for _ in ()).throw(RuntimeError("boom")),
            logger=_logger(),
        )
    )

    assert outputs[-1]["success"] is True
    assert outputs[-1]["final_answer"] == "结论 (doi=10.1/a)"
    assert outputs[-1]["references"] == []


def test_build_references_from_pdf_chunks_matches_underscore_pdf_keys():
    references = build_references_from_pdf_chunks(
        cited_dois=["10.1007/s11581-021-04073-2"],
        pdf_chunks={
            "10.1007_s11581-021-04073-2": [{"text": "sample evidence text", "page": 1}],
        },
    )

    assert len(references) == 1
    assert references[0]["doi"] == "10.1007/s11581-021-04073-2"


def test_build_references_from_pdf_chunks_prefers_pdf_preview_over_html_md_chunk():
    references = build_references_from_pdf_chunks(
        cited_dois=["10.1/a"],
        pdf_chunks={
            "10.1/a": [
                {"text": "```html <html><body>md html preview</body></html>", "source": "md_expansion"},
                {"text": "clean pdf preview", "page": 2, "source": "pdf"},
            ],
        },
    )

    assert len(references) == 1
    assert references[0]["sample_text"].startswith("clean pdf preview")




def test_align_dois_with_pdf_chunks_preserves_markdown_block_boundaries():
    logger = _logger()
    result = align_dois_with_pdf_chunks(
        """开头结论。

## 机理分析
- 液相极化增强。""",
        {
            "10.1/a": [
                {"text": "开头结论。" + "补充证据" * 20, "page": 1},
                {"text": "液相极化增强。" + "补充证据" * 20, "page": 2},
            ]
        },
        emb_model=None,
        threshold=0.1,
        logger=logger,
    )

    assert "(doi=10.1/a)\n\n## 机理分析" in result
    assert "## 机理分析\n- 液相极化增强。 (doi=10.1/a)" in result



def test_stage4_synthesis_logs_context_summary_without_leaking_contents(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FakeClient([_chunk("结论 (doi=10.1/a)")])
    logger = _logger()

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="那它的缺点呢?",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "evidence", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            conversation_context={
                "recent_turns_for_llm": [
                    {"role": "user", "content": "介绍磷酸铁锂的优点", "trace_id": "trace-u1"},
                    {"role": "assistant", "content": "它的优点包括安全性和寿命", "trace_id": "trace-a1"},
                ],
                "summary_for_llm": {
                    "short_summary": "之前在讨论LFP优缺点",
                    "open_threads": ["继续分析缺点"],
                    "memory_facts": ["上轮已确认其安全性较高"],
                },
            },
            logger=logger,
        )
    )

    assert outputs[-1]["success"] is True
    info_messages = [message for level, message in logger.records if level == "info"]
    target = next(message for message in info_messages if message.startswith("stage4 conversation context attached"))
    assert "turns=2" in target
    assert "summary_present=True" in target
    assert "short_summary_present=True" in target
    assert "open_threads=1" in target
    assert "memory_facts=1" in target
    assert "介绍磷酸铁锂的优点" not in target
    assert "它的优点包括安全性和寿命" not in target
    assert "之前在讨论LFP优缺点" not in target


def test_stage4_synthesis_includes_conversation_context_but_excludes_trace_fields(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FakeClient([_chunk("结论 (doi=10.1/a)")])

    outputs = list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="那它的缺点呢?",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "evidence", "page": 1}]},
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            conversation_context={
                "recent_turns_for_llm": [
                    {"role": "user", "content": "介绍磷酸铁锂的优点", "trace_id": "trace-u1"},
                    {"role": "assistant", "content": "它的优点包括安全性和寿命", "trace_id": "trace-a1"},
                ],
                "summary_for_llm": {
                    "short_summary": "之前在讨论LFP优缺点",
                    "open_threads": ["继续分析缺点"],
                    "memory_facts": ["上轮已确认其安全性较高"],
                    "trace_id": "trace-summary",
                    "steps": [{"name": "should-not-leak"}],
                    "timings": {"stage1": 12},
                },
            },
            logger=_logger(),
        )
    )

    assert outputs[-1]["success"] is True
    prompt = client.calls[-1]["messages"][1]["content"]
    assert "介绍磷酸铁锂的优点" in prompt
    assert "它的优点包括安全性和寿命" in prompt
    assert "之前在讨论LFP优缺点" in prompt
    assert "继续分析缺点" in prompt
    assert "上轮已确认其安全性较高" in prompt
    assert "trace-u1" not in prompt
    assert "trace-summary" not in prompt
    assert "should-not-leak" not in prompt
    assert '"stage1": 12' not in prompt


def test_stage4_fact_per_doi_extraction_merges_distinct_dois(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_FACT_EXTRACTION_PER_DOI_ENABLED", "true")
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _FactPerDoiRoundClient(
        answer_chunks=[_chunk("磷酸铁锂性能 (doi=10.1/a)"), _chunk(" 与纳米化 (doi=10.1/b)")],
    )
    list(
        iter_stage4_synthesis_with_pdf_chunks(
            user_question="磷酸铁锂粒径如何影响性能？",
            deep_answer="draft",
            pdf_chunks={
                "10.1/a": [{"text": "磷酸铁锂粒度影响倍率性能与循环。", "page": 1}],
                "10.1/b": [{"text": "磷酸铁锂电化学性能与纳米粒径密切相关。", "page": 1}],
            },
            retrieval_results={"claim_to_results": {}},
            stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
            client=client,
            model="m",
            safe_dict_cls=_SafeDict,
            escape_braces_fn=_escape_braces,
            format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
            build_top5_reference_context_fn=build_top5_reference_context,
            extract_cited_dois_fn=extract_cited_dois,
            log_top5_coverage_fn=log_top5_coverage,
            build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
            logger=_logger(),
        )
    )
    non_stream = [c for c in client.calls if c.get("stream") is False]
    assert len(non_stream) >= 2
    synth = client.calls[-1]
    assert synth.get("stream") is True
    prompt = synth["messages"][1]["content"]
    assert "10.1/a" in prompt and "10.1/b" in prompt


def test_stage4_synthesis_propagates_pool_timeout_without_fallback(monkeypatch):
    monkeypatch.setenv("QA_STAGE4_MIN_CITATIONS", "1")
    client = _PoolTimeoutClient()

    try:
        list(
            iter_stage4_synthesis_with_pdf_chunks(
                user_question="what is lfp?",
                deep_answer="draft",
                pdf_chunks={"10.1/a": [{"text": "evidence", "page": 1}]},
                retrieval_results={"claim_to_results": {}},
                stage2_prompt="prompt {user_question} {deep_answer} {evidence_documents} {top5_references}",
                client=client,
                model="m",
                safe_dict_cls=_SafeDict,
                escape_braces_fn=_escape_braces,
                format_pdf_chunks_evidence_fn=_format_pdf_chunks_evidence,
                build_top5_reference_context_fn=build_top5_reference_context,
                extract_cited_dois_fn=extract_cited_dois,
                log_top5_coverage_fn=log_top5_coverage,
                build_references_from_pdf_chunks_fn=build_references_from_pdf_chunks,
                logger=_logger(),
            )
        )
    except httpx.PoolTimeout:
        pass
    else:  # pragma: no cover - enforced by failing test before fix
        raise AssertionError("expected PoolTimeout to propagate")
