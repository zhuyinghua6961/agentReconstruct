from app.modules.qa_cache.pdf_cache import _pdf_text_cache_ttl_seconds
from app.modules.qa_cache.stage1_cache import _stage1_cache_ttl_seconds
from app.modules.qa_cache.stage2_cache import _stage2_cache_ttl_seconds
from app.modules.qa_cache.stage25_cache import _stage25_cache_ttl_seconds
from app.modules.qa_cache.stage3_cache import _stage3_cache_ttl_seconds


def test_fastqa_stage_cache_default_ttls_are_aligned():
    assert _stage1_cache_ttl_seconds() == 43200
    assert _stage2_cache_ttl_seconds() == 43200
    assert _stage25_cache_ttl_seconds() == 43200
    assert _stage3_cache_ttl_seconds() == 43200
    assert _pdf_text_cache_ttl_seconds() == 86400
