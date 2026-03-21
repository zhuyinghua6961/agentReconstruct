from __future__ import annotations

from app.modules.generation_pipeline.doi_validation import build_doi_variants, canonicalize_doi, validate_and_fix_doi


def test_validate_and_fix_doi_accepts_underscore_form():
    assert validate_and_fix_doi("10.1007_s11581-021-04073-2") == "10.1007/s11581-021-04073-2"


def test_build_doi_variants_returns_slash_and_underscore_forms():
    variants = build_doi_variants("10.1007_s11581-021-04073-2")
    assert variants == ["10.1007/s11581-021-04073-2", "10.1007_s11581-021-04073-2"]
    assert canonicalize_doi("10.1007_s11581-021-04073-2") == "10.1007/s11581-021-04073-2"
