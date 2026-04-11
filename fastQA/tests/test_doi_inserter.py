from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

from app.modules.generation_pipeline.doi_inserter import _iter_sentence_units, programmatic_insert_dois


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


class _EmbeddingModel:
    def __init__(self):
        self.calls = []

    def encode(self, texts):
        outputs = []
        for text in texts:
            payload = str(text or "")
            self.calls.append(payload)
            if "提升" in payload or "稳定" in payload:
                outputs.append([1.0, 0.0])
            else:
                outputs.append([0.0, 1.0])
        return outputs


class _Agent:
    def __init__(self, *, use_sentence_embeddings: bool, require_pdf_evidence_for_doi: bool):
        self.enable_programmatic_doi_insertion = True
        self.seq_similarity_weight = 0.6
        self.vector_similarity_weight = 0.4
        self.max_seq_compare_chars = 1000
        self.use_sentence_embeddings = use_sentence_embeddings
        self.embedding_similarity_weight = 0.6
        self.insert_similarity_threshold = 0.45
        self.insert_seq_verify_threshold = 0.60
        self.insert_embed_verify_threshold = 0.60
        self.insert_vector_verify_threshold = 0.60
        self.require_pdf_evidence_for_doi = require_pdf_evidence_for_doi
        self.strict_mode = False
        self.use_new_aligner = False
        self.literature_expert = SimpleNamespace(embedding_model=_EmbeddingModel())
        self.pdf_load_counter = Counter()

    def _load_pdf_sentences(self, doi: str, max_pages: int = 30, max_chars: int = 15000):
        self.pdf_load_counter[doi] += 1
        return ["材料性能显著提升。", "循环稳定性显著提升。"]

    def _strict_verify_answer(self, answer, retrieval_results, question=None):
        return answer

    def _write_alignment_audit(self, question, original_answer, audit_list, retrieval_results):
        return None



def test_programmatic_insert_dois_loads_pdf_sentences_once_per_doi():
    agent = _Agent(use_sentence_embeddings=False, require_pdf_evidence_for_doi=True)
    retrieval_results = {
        "all_metadatas": [{"doi": "10.1/a"}],
        "all_documents": ["材料性能显著提升。"],
        "all_distances": [0.1],
    }

    answer = "材料性能显著提升。 材料性能显著提升。"
    result = programmatic_insert_dois(
        agent=agent,
        answer=answer,
        retrieval_results=retrieval_results,
        similarity_threshold=None,
        question="q",
        validate_and_fix_doi_fn=lambda doi: doi,
        aligner_cls=None,
        logger=_Logger(),
    )

    assert result.count("(doi=10.1/a)") == 2
    assert agent.pdf_load_counter["10.1/a"] == 1



def test_programmatic_insert_dois_reuses_sentence_and_doc_embeddings():
    agent = _Agent(use_sentence_embeddings=True, require_pdf_evidence_for_doi=False)
    retrieval_results = {
        "all_metadatas": [{"doi": "10.1/a"}, {"doi": "10.1/b"}],
        "all_documents": ["材料性能显著提升与循环稳定性改善。", "完全无关的检索片段。"],
        "all_distances": [0.1, 1.8],
    }

    answer = "材料性能显著提升。 材料性能显著提升。"
    programmatic_insert_dois(
        agent=agent,
        answer=answer,
        retrieval_results=retrieval_results,
        similarity_threshold=None,
        question="q",
        validate_and_fix_doi_fn=lambda doi: doi,
        aligner_cls=None,
        logger=_Logger(),
    )

    call_counter = Counter(agent.literature_expert.embedding_model.calls)
    assert call_counter["材料性能显著提升。"] == 1
    assert call_counter["材料性能显著提升与循环稳定性改善。"] == 1
    assert call_counter["完全无关的检索片段。"] == 1


def test_programmatic_insert_dois_preserves_markdown_block_boundaries():
    agent = _Agent(use_sentence_embeddings=False, require_pdf_evidence_for_doi=False)
    retrieval_results = {
        "all_metadatas": [{"doi": "10.1/a"}, {"doi": "10.1/b"}],
        "all_documents": ["开头结论。", "液相极化增强。"],
        "all_distances": [0.1, 0.1],
    }

    answer = """开头结论。

## 机理分析
- 液相极化增强。"""
    result = programmatic_insert_dois(
        agent=agent,
        answer=answer,
        retrieval_results=retrieval_results,
        similarity_threshold=None,
        question="q",
        validate_and_fix_doi_fn=lambda doi: doi,
        aligner_cls=None,
        logger=_Logger(),
    )

    assert "(doi=10.1/a)\n\n## 机理分析" in result
    assert "## 机理分析\n- 液相极化增强。 (doi=10.1/b)" in result


def test_iter_sentence_units_keeps_decimal_measurements_intact():
    units = _iter_sentence_units("颗粒尺寸约为 0.22μm，压实密度可达 3.6 g/cm³。")

    assert units == [("颗粒尺寸约为 0.22μm，压实密度可达 3.6 g/cm³。", "")]


def test_iter_sentence_units_does_not_split_existing_doi_tokens():
    units = _iter_sentence_units("参考文献 (doi=10.1007/s11595-019-2086-y) 与 10.1016/j.ssi.2024.116535 支撑该结论。")

    assert units == [("参考文献 (doi=10.1007/s11595-019-2086-y) 与 10.1016/j.ssi.2024.116535 支撑该结论。", "")]


def test_iter_sentence_units_splits_mixed_language_sentences_without_space_after_period():
    units = _iter_sentence_units("English claim.第二句说明另一件事。")

    assert units == [("English claim.", ""), ("第二句说明另一件事。", "")]


def test_iter_sentence_units_splits_english_sentences_without_space_after_period():
    units = _iter_sentence_units("Sentence one.Next sentence.")

    assert units == [("Sentence one.", ""), ("Next sentence.", "")]


def test_iter_sentence_units_stops_raw_doi_before_following_sentence_without_space():
    units = _iter_sentence_units("10.1016/j.ssi.2024.116535.Next sentence.")

    assert units == [("10.1016/j.ssi.2024.116535.", ""), ("Next sentence.", "")]


def test_iter_sentence_units_splits_lowercase_scientific_sentence_start_after_period():
    units = _iter_sentence_units("The electrolyte was adjusted. pH remained at 3.5.")

    assert units == [("The electrolyte was adjusted.", " "), ("pH remained at 3.5.", "")]


def test_iter_sentence_units_keeps_common_abbreviation_mid_sentence_intact():
    units = _iter_sentence_units("Smith et al. reported higher conductivity.")

    assert units == [("Smith et al. reported higher conductivity.", "")]


def test_programmatic_insert_dois_keeps_decimal_values_when_appending_citations():
    agent = _Agent(use_sentence_embeddings=False, require_pdf_evidence_for_doi=False)
    retrieval_results = {
        "all_metadatas": [{"doi": "10.1/a"}],
        "all_documents": ["颗粒尺寸约为 0.22μm，压实密度可达 3.6 g/cm³。"],
        "all_distances": [0.1],
    }

    answer = "颗粒尺寸约为 0.22μm，压实密度可达 3.6 g/cm³。"
    result = programmatic_insert_dois(
        agent=agent,
        answer=answer,
        retrieval_results=retrieval_results,
        similarity_threshold=None,
        question="q",
        validate_and_fix_doi_fn=lambda doi: doi,
        aligner_cls=None,
        logger=_Logger(),
    )

    assert "0.22μm" in result
    assert "3.6 g/cm³" in result
    assert "0. (doi=" not in result
    assert "3. (doi=" not in result
    assert result.endswith(" (doi=10.1/a)")
