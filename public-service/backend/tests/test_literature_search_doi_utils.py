from __future__ import annotations

from types import SimpleNamespace

from app.modules.literature_search.doi_utils import extract_doi_from_filename, looks_like_doi_query, resolve_query_type


def test_metadata_to_doi_from_document_name():
    from app.modules.literature_search.doi_utils import metadata_to_doi

    assert metadata_to_doi({"document_name": "10.1016_j.apenergy.2016.01.096"}) == "10.1016/j.apenergy.2016.01.096"


def test_extract_doi_from_filename_restores_first_slash():
    assert extract_doi_from_filename("10.1002_adfm.201500286.pdf") == "10.1002/adfm.201500286"


def test_looks_like_doi_query_detects_prefix():
    assert looks_like_doi_query("10.1002/adfm.201500286") is True
    assert looks_like_doi_query("LiFePO4 cathode") is False


def test_resolve_query_type_auto():
    assert resolve_query_type(query="10.1002/foo", query_type="auto") == "doi"
    assert resolve_query_type(query="lithium iron phosphate", query_type="auto") == "title"
    assert resolve_query_type(query="anything", query_type="title") == "title"


def test_resolve_query_type_forced_doi():
    assert resolve_query_type(query="not a doi", query_type="doi") == "doi"
