from server.utils.doi import extract_dois, normalize_doi


def test_normalize_doi_handles_polluted_reference_tokens():
    assert normalize_doi("doi:10.1007_s11581-021-04073-2).") == "10.1007/s11581-021-04073-2"


def test_normalize_doi_handles_equals_prefixed_reference_tokens():
    assert normalize_doi("doi=10.1016/j.psep.2024.10.111") == "10.1016/j.psep.2024.10.111"


def test_normalize_doi_handles_papers_prefixed_pdf_path_like_current_highthinking_behavior():
    assert normalize_doi("papers/10.1007_s11581-021-04073-2.pdf") == "10.1007_s11581-021-04073-2"


def test_normalize_doi_handles_url_encoded_path():
    assert normalize_doi("10.1007%2Fs11581-021-04073-2") == "10.1007/s11581-021-04073-2"


def test_normalize_doi_repairs_missing_slash_after_prefix():
    assert normalize_doi("10.1016j.est.2024.113859") == "10.1016/j.est.2024.113859"


def test_extract_dois_splits_concatenated_tokens():
    assert extract_dois("10.1016j.jpowsour.2005.03.09910.1016j.jpowsour.2013.06.070") == [
        "10.1016/j.jpowsour.2005.03.099",
        "10.1016/j.jpowsour.2013.06.070",
    ]


def test_extract_dois_keeps_parenthesized_suffix():
    assert extract_dois("(doi=10.1016/S0378-7753(03)00297-0)") == [
        "10.1016/S0378-7753(03)00297-0"
    ]


def test_extract_dois_repairs_merged_second_doi_missing_leading_separator():
    assert extract_dois("10.1016/j.electacta.2006.05.002)1002.aem.202501444") == [
        "10.1016/j.electacta.2006.05.002",
        "10.1002/aem.202501444",
    ]


def test_extract_dois_repairs_square_bracket_merged_second_doi_missing_leading_separator():
    assert extract_dois("10.1016/j.electacta.2006.05.002]1002.aem.202501444") == [
        "10.1016/j.electacta.2006.05.002",
        "10.1002/aem.202501444",
    ]


def test_extract_dois_does_not_fabricate_doi_from_non_doi_suffix():
    assert extract_dois("(n=5)1002.alpha") == []
