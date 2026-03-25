from __future__ import annotations

from types import SimpleNamespace

from app.modules.generation_pipeline.synthesis_postprocess import (
    build_references_from_pdf_chunks,
    build_top5_reference_context,
    extract_cited_dois,
    log_top5_coverage,
    resolve_stage4_reference_policy,
)
from app.modules.generation_pipeline.reference_alignment import align_dois_with_pdf_chunks
from app.modules.generation_pipeline.synthesis_streaming import iter_stage4_synthesis_with_pdf_chunks


class _FakeClient:
    def __init__(self, chunks):
        self._chunks = chunks
        self.calls = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self._chunks)


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


def test_extract_cited_dois_and_build_references():
    answer = "A (doi=10.1/a). B (doi=10.1_a) C"
    cited, _ = extract_cited_dois(answer, logger=_logger())
    references = build_references_from_pdf_chunks(
        cited_dois=cited,
        pdf_chunks={"10.1/a": [{"text": "sample evidence text", "page": 1}]},
    )

    assert set(cited) == {"10.1/a", "10.1_a"}
    assert {item["doi"] for item in references} == {"10.1/a"}


def test_stage4_default_min_citations_matches_legacy(monkeypatch):
    monkeypatch.delenv("QA_STAGE4_MIN_CITATIONS", raising=False)

    topk, min_citations, element_guard = resolve_stage4_reference_policy(topk=12)

    assert topk == 12
    assert min_citations == 10
    assert element_guard is True


def test_build_top5_reference_context_uses_legacy_min_citations_default(monkeypatch):
    monkeypatch.delenv("QA_STAGE4_MIN_CITATIONS", raising=False)

    _scores, reference_text = build_top5_reference_context(
        retrieval_results={
            "claim_to_results": {
                "c1": {
                    "distances": [0.1],
                    "metadatas": [{"doi": "10.1/a"}],
                }
            }
        },
        logger=_logger(),
        topk=12,
    )

    assert "必须至少引用 10 篇不同文献" in reference_text



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
    prompt = client.calls[0]["messages"][1]["content"]
    assert "介绍磷酸铁锂的优点" in prompt
    assert "它的优点包括安全性和寿命" in prompt
    assert "之前在讨论LFP优缺点" in prompt
    assert "继续分析缺点" in prompt
    assert "上轮已确认其安全性较高" in prompt
    assert "trace-u1" not in prompt
    assert "trace-summary" not in prompt
    assert "should-not-leak" not in prompt
    assert '"stage1": 12' not in prompt
