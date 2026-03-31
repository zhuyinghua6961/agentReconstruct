from server.services.stage_cache import _decompose_ttl, _direct_answer_ttl, _retrieve_ttl


def test_highthinking_stage_cache_default_ttls_align_with_fastqa_principles(monkeypatch):
    for name in (
        "HT_QA_DIRECT_CACHE_TTL_SECONDS",
        "HT_QA_DECOMPOSE_CACHE_TTL_SECONDS",
        "HT_QA_RETRIEVE_CACHE_TTL_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    assert _direct_answer_ttl() == 43200
    assert _decompose_ttl() == 43200
    assert _retrieve_ttl() == 43200
