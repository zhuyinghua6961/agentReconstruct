from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_tp_path = Path(__file__).resolve().parents[1] / "app/modules/generation_pipeline/text_processing.py"
_spec = importlib.util.spec_from_file_location("_text_processing_stage2_test", _tp_path)
assert _spec and _spec.loader
_text_processing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_text_processing)
finalize_retrieval_keywords_for_embedding = _text_processing.finalize_retrieval_keywords_for_embedding


def test_finalize_prioritizes_must_include_when_core_exceeds_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QA_STAGE2_EMBEDDING_QUERY_MAX_KEYWORDS", "8")
    core = " ".join([f"w{i}" for i in range(1, 20)])
    out = finalize_retrieval_keywords_for_embedding(
        core,
        ["如何制备高压实型", "磷酸铁锂材料"],
        max_keywords=None,
        max_injection_slots=None,
        logger=None,
    )
    assert "如何制备高压实型" in out
    assert "磷酸铁锂材料" in out
    tokens = out.split()
    assert len(tokens) <= 8
    assert tokens.index("如何制备高压实型") < tokens.index("磷酸铁锂材料")


def test_finalize_max_injection_slots_truncates_must_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QA_STAGE2_EMBEDDING_QUERY_MAX_KEYWORDS", "10")
    core = "a b c d e f g h i j k l m n o p"
    out = finalize_retrieval_keywords_for_embedding(
        core,
        ["must1", "must2", "must3"],
        max_keywords=10,
        max_injection_slots=1,
        logger=None,
    )
    assert out.startswith("must1")
    assert "must2" not in out
    assert "must3" not in out


def test_finalize_drops_slot_noise_must_include() -> None:
    out = finalize_retrieval_keywords_for_embedding(
        "LiFePO4 正常词",
        ["nmp_null_null"],
        max_keywords=10,
        max_injection_slots=None,
        logger=None,
    )
    assert "null" not in out.lower()
    assert "LiFePO4" in out or "磷酸铁锂" in out
