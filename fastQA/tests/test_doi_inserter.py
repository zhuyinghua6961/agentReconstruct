from __future__ import annotations

from collections import Counter
from types import SimpleNamespace

from app.modules.generation_pipeline.doi_inserter import programmatic_insert_dois


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
