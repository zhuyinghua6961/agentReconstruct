from server.utils.doi import normalize_doi


def test_normalize_doi_handles_polluted_reference_tokens():
    assert normalize_doi("doi:10.1007_s11581-021-04073-2).") == "10.1007/s11581-021-04073-2"


def test_normalize_doi_handles_papers_prefixed_pdf_path_like_current_highthinking_behavior():
    assert normalize_doi("papers/10.1007_s11581-021-04073-2.pdf") == "10.1007_s11581-021-04073-2"


def test_normalize_doi_handles_url_encoded_path():
    assert normalize_doi("10.1007%2Fs11581-021-04073-2") == "10.1007/s11581-021-04073-2"
