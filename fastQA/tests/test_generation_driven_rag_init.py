from __future__ import annotations

import json
from pathlib import Path

from app.modules.graph_kb.models import GraphRagPayload
from app.modules.generation_pipeline.generation_driven_rag_facade import GenerationDrivenRAG


def test_generation_driven_rag_initializes_with_local_bootstrap(monkeypatch, tmp_path):
    topic_index = tmp_path / "vector_db_topic_index.json"
    topic_index.write_text(
        json.dumps(
            {
                "total_json_files": 3,
                "top_keywords": [{"keyword": "LFP"}, {"keyword": "cycle life"}],
                "topic_distribution": [{"topic": "aging", "doi_count": 2}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    monkeypatch.setenv("TOPIC_INDEX_PATH", str(topic_index))
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.build_openai_client_impl",
        lambda *, api_key, base_url, logger=None, http_client=None: {"api_key": api_key, "base_url": base_url},
    )

    rag = GenerationDrivenRAG()

    assert rag.api_key == "openai-key"
    assert rag.base_url == "https://example.com/v1"
    assert rag.model == "gpt-test"
    assert rag.client == {"api_key": "openai-key", "base_url": "https://example.com/v1"}
    assert "LFP" in rag._get_vector_db_context_for_prompt()
    assert rag.stage1_prompt
    assert rag.stage2_prompt
    assert rag.strict_mode is True


def test_generation_driven_rag_extracts_unique_dois(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.build_openai_client_impl",
        lambda *, api_key, base_url, logger=None, http_client=None: object(),
    )

    rag = GenerationDrivenRAG()
    dois = rag._extract_dois_from_results(
        {"metadatas": [{"doi": "10.1/a"}, {"doi": "10.1/a"}, {"doi": "10.2/b"}, {"no_doi": "x"}]}
    )

    assert dois == ["10.1/a", "10.2/b"]


def test_generation_driven_rag_stage1_uses_stage1_planning(monkeypatch, tmp_path):
    topic_index = tmp_path / "vector_db_topic_index.json"
    topic_index.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("TOPIC_INDEX_PATH", str(topic_index))

    class _Client:
        def __init__(self):
            self.chat = type(
                "Chat",
                (),
                {
                    "completions": type(
                        "Completions",
                        (),
                        {
                            "create": lambda self, **kwargs: type(
                                "Resp",
                                (),
                                {"choices": [type("Choice", (), {"message": type("Msg", (), {"content": '{"deep_answer":"a","retrieval_claims":["b"]}'})()})]},
                            )()
                        },
                    )()
                },
            )()

    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.build_openai_client_impl",
        lambda *, api_key, base_url, logger=None, http_client=None: _Client(),
    )

    rag = GenerationDrivenRAG()
    result = rag.stage1_pre_answer_and_planning("what is lfp?")

    assert result["success"] is True
    assert result["deep_answer"] == "a"
    assert result["retrieval_claims"][0]["claim"] == "b"


def test_generation_driven_rag_stage2_uses_stage2_retrieval(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.build_openai_client_impl",
        lambda *, api_key, base_url, logger=None, http_client=None: object(),
    )

    captured: dict[str, object] = {}

    def _fake_stage2(**kwargs):
        captured.update(kwargs)
        return {"success": True, "documents": [], "metadatas": [], "distances": [], "claim_to_results": {}, "unique_count": 0, "total_count": 0}

    monkeypatch.setattr("app.modules.generation_pipeline.generation_driven_rag_facade.run_stage2_targeted_retrieval_impl", _fake_stage2)
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.QueryExpander",
        lambda **kwargs: type("_Expander", (), {"expand": lambda self, query: query + " expanded"})(),
    )

    rag = GenerationDrivenRAG()
    result = rag.stage2_targeted_retrieval(
        retrieval_claims=[{"claim": "lfp"}],
        n_results_per_claim=4,
        user_question="what is lfp?",
        should_cancel=lambda: False,
        active_stream_count=2,
    )

    assert result["success"] is True
    assert captured["retrieval_claims"] == [{"claim": "lfp"}]
    assert captured["n_results_per_claim"] == 4
    assert captured["user_question"] == "what is lfp?"
    assert captured["client"] is rag.client
    assert captured["model"] == rag.model
    assert captured["literature_expert"] is rag.literature_expert
    assert callable(captured["preprocess_retrieval_query_fn"])
    assert callable(captured["validate_retrieval_relevance_fn"])
    assert callable(captured["extract_question_keywords_fn"])
    assert callable(captured["expand_query_fn"])


def test_generation_driven_rag_stage3_uses_pdf_pipeline(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.build_openai_client_impl",
        lambda *, api_key, base_url, logger=None, http_client=None: object(),
    )

    captured: dict[str, object] = {}

    def _fake_stage3(**kwargs):
        captured.update(kwargs)
        return {"10.1/a": [{"text": "evidence"}]}

    monkeypatch.setattr("app.modules.generation_pipeline.generation_driven_rag_facade.stage3_load_pdf_chunks_impl", _fake_stage3)
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.get_settings",
        lambda: type("Settings", (), {"papers_dir": "/tmp/papers"})(),
    )

    rag = GenerationDrivenRAG()
    result = rag.stage3_load_pdf_chunks(["10.1/a"], max_chunks_per_doi=2, should_cancel=lambda: False)

    assert result == {"10.1/a": [{"text": "evidence"}]}
    assert captured["dois"] == ["10.1/a"]
    assert captured["papers_dir"] == "/tmp/papers"
    assert captured["max_chunks_per_doi"] == 2


def test_generation_driven_rag_stage25_uses_md_expansion(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.build_openai_client_impl",
        lambda *, api_key, base_url, logger=None, http_client=None: object(),
    )

    captured: dict[str, object] = {}

    def _fake_stage25(**kwargs):
        captured.update(kwargs)
        return {"enabled": True, "applied": True, "md_chunks_by_doi": {"10.1/a": [{"text": "md"}]}, "stats": {"hit_doi_count": 1, "total_md_chunks": 1}}

    monkeypatch.setattr("app.modules.generation_pipeline.generation_driven_rag_facade.run_stage25_md_expansion_impl", _fake_stage25)

    rag = GenerationDrivenRAG()
    result = rag.stage25_md_expansion(
        retrieval_results={"documents": []},
        user_question="what is lfp?",
        dois=["10.1/a"],
    )

    assert result["applied"] is True
    assert captured["user_question"] == "what is lfp?"
    assert captured["dois"] == ["10.1/a"]
    assert captured["literature_expert"] is rag.literature_expert


def test_generation_driven_rag_stage4_uses_synthesis_streaming(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.build_openai_client_impl",
        lambda *, api_key, base_url, logger=None, http_client=None: object(),
    )

    captured: dict[str, object] = {}

    def _fake_stage4(**kwargs):
        captured.update(kwargs)
        yield "hello"
        yield {"success": True, "final_answer": "hello", "references": [], "cited_dois": [], "source_count": 0}

    monkeypatch.setattr("app.modules.generation_pipeline.generation_driven_rag_facade.iter_stage4_synthesis_with_pdf_chunks_impl", _fake_stage4)

    rag = GenerationDrivenRAG()
    result = list(
        rag.stage4_synthesis_with_pdf_chunks(
            user_question="what is lfp?",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "evidence"}]},
            retrieval_results={"documents": []},
            should_cancel=lambda: False,
        )
    )

    assert result[0] == "hello"
    assert result[-1]["success"] is True
    assert captured["user_question"] == "what is lfp?"


def test_generation_driven_rag_forwards_graph_evidence_into_stage_calls(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.build_openai_client_impl",
        lambda *, api_key, base_url, logger=None, http_client=None: object(),
    )

    captured: dict[str, object] = {}

    def _fake_stage1(**kwargs):
        captured["stage1"] = kwargs
        return {"success": True, "deep_answer": "a", "retrieval_claims": []}

    def _fake_stage2(**kwargs):
        captured["stage2"] = kwargs
        return {"success": True, "documents": [], "metadatas": [], "distances": [], "claim_to_results": {}, "unique_count": 0, "total_count": 0}

    def _fake_stage4(**kwargs):
        captured["stage4"] = kwargs
        yield {"success": True, "final_answer": "ok", "references": [], "cited_dois": [], "source_count": 0}

    monkeypatch.setattr("app.modules.generation_pipeline.generation_driven_rag_facade.run_stage1_pre_answer_and_planning_impl", _fake_stage1)
    monkeypatch.setattr("app.modules.generation_pipeline.generation_driven_rag_facade.run_stage2_targeted_retrieval_impl", _fake_stage2)
    monkeypatch.setattr("app.modules.generation_pipeline.generation_driven_rag_facade.iter_stage4_synthesis_with_pdf_chunks_impl", _fake_stage4)

    rag = GenerationDrivenRAG()
    payload = GraphRagPayload(
        stage1_context_block="doi:10.1000/test",
        stage4_fact_block="structured graph facts",
        cache_fingerprint="graph:abc",
    )

    rag.stage1_pre_answer_and_planning("what is lfp?", graph_context=payload.stage1_context_block)
    rag.stage2_targeted_retrieval(
        retrieval_claims=[{"claim": "lfp"}],
        n_results_per_claim=3,
        user_question="what is lfp?",
        graph_evidence=payload,
    )
    list(
        rag.stage4_synthesis_with_pdf_chunks(
            user_question="what is lfp?",
            deep_answer="draft",
            pdf_chunks={"10.1/a": [{"text": "evidence"}]},
            retrieval_results={"documents": []},
            graph_fact_block=payload.stage4_fact_block,
        )
    )

    assert captured["stage1"]["graph_context"] == "doi:10.1000/test"
    assert captured["stage2"]["graph_evidence"] is payload
    assert captured["stage4"]["graph_fact_block"] == "structured graph facts"


def test_generation_driven_rag_uses_legacy_inline_stage_prompts_not_workspace_prompt_files(monkeypatch, tmp_path):
    topic_index = tmp_path / "vector_db_topic_index.json"
    topic_index.write_text("{}", encoding="utf-8")
    prompt_root = tmp_path / "prompts"
    prompt_root.mkdir()
    (prompt_root / "system_prompt.txt").write_text("WRONG GRAPH PROMPT", encoding="utf-8")
    (prompt_root / "synthesis_prompt.txt").write_text("WRONG GRAPH SYNTHESIS", encoding="utf-8")

    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.com/v1")
    monkeypatch.setenv("TOPIC_INDEX_PATH", str(topic_index))
    monkeypatch.setenv("MATERIAL_AGENT_PROMPTS_DIR", str(prompt_root))
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.build_openai_client_impl",
        lambda *, api_key, base_url, logger=None, http_client=None: object(),
    )

    rag = GenerationDrivenRAG()

    assert "Neo4j" not in rag.stage1_prompt
    assert "Cypher" not in rag.stage1_prompt
    assert rag.stage1_prompt.startswith("你是一位长期从事磷酸铁锂（LFP）正极材料研发")
    assert rag.stage2_prompt.startswith("你是一名最终的答案润色与校验专家")
