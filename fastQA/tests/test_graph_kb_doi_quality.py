from __future__ import annotations

from app.modules.graph_kb.doi_quality import classify_doi_quality


def test_valid_doi():
    result = classify_doi_quality("10.1021/jp1005692")

    assert result.status == "valid"


def test_truncated_doi_is_suspicious():
    result = classify_doi_quality("10.1007/s12598-")

    assert result.status == "suspicious"


def test_glued_doi_is_suspicious_or_invalid():
    result = classify_doi_quality("10.1039/d2nj04292dReceived")

    assert result.status in {"suspicious", "invalid"}


def test_doi_with_single_underscore_separator_normalizes_to_slash():
    result = classify_doi_quality("10.1021_jp1005692.")

    assert result.status == "valid"
    assert result.doi == "10.1021/jp1005692"


def test_url_corrupted_doi_is_invalid_or_suspicious():
    result = classify_doi_quality("https://doi.org/10.1021/jp1005692")

    assert result.status in {"invalid", "suspicious"}
