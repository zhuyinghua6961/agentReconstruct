from __future__ import annotations

from app.modules.generation_pipeline.doi_validation import (
    build_doi_variants,
    canonicalize_doi,
    extract_valid_dois,
    validate_and_fix_doi,
)


def test_validate_and_fix_doi_accepts_underscore_form():
    assert validate_and_fix_doi("10.1007_s11581-021-04073-2") == "10.1007/s11581-021-04073-2"


def test_build_doi_variants_returns_slash_and_underscore_forms():
    variants = build_doi_variants("10.1007_s11581-021-04073-2")
    assert variants == ["10.1007/s11581-021-04073-2", "10.1007_s11581-021-04073-2"]
    assert canonicalize_doi("10.1007_s11581-021-04073-2") == "10.1007/s11581-021-04073-2"


def test_validate_and_fix_doi_repairs_missing_slash_after_prefix():
    assert validate_and_fix_doi("10.1016j.est.2024.113859") == "10.1016/j.est.2024.113859"


def test_extract_valid_dois_splits_concatenated_tokens():
    assert extract_valid_dois("10.1016j.jpowsour.2005.03.09910.1016j.jpowsour.2013.06.070") == [
        "10.1016/j.jpowsour.2005.03.099",
        "10.1016/j.jpowsour.2013.06.070",
    ]


def test_extract_valid_dois_keeps_parenthesized_suffix():
    assert extract_valid_dois("(doi=10.1016/S0378-7753(03)00297-0)") == [
        "10.1016/S0378-7753(03)00297-0"
    ]
