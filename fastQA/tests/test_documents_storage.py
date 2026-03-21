from __future__ import annotations

from app.modules.storage.paper_storage import build_paper_filename, ensure_local_paper_pdf, find_local_paper_pdf


def test_build_paper_filename_replaces_all_slashes():
    assert build_paper_filename("10.1000/foo/bar") == "10.1000_foo_bar.pdf"


def test_find_local_paper_pdf_supports_flattened_pattern_fallback(tmp_path):
    normalized = tmp_path / "10.1000_foo_bar_extra.pdf"
    normalized.write_bytes(b"%PDF-1.4\n%normalized\n")

    resolved = find_local_paper_pdf(doi="10.1000/foo/bar", papers_dir=tmp_path)

    assert resolved == normalized.resolve()


def test_ensure_local_paper_pdf_returns_existing_local_file(tmp_path):
    pdf_path = tmp_path / "10.1000_demo.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%existing\n")

    resolved = ensure_local_paper_pdf(doi="10.1000/demo", papers_dir=tmp_path)

    assert resolved == pdf_path.resolve()


def test_find_local_paper_pdf_accepts_encoded_or_path_like_doi(tmp_path):
    pdf_path = tmp_path / "10.1000_foo_bar.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%encoded\n")

    resolved_from_encoded = find_local_paper_pdf(doi="10.1000%2Ffoo%2Fbar.pdf", papers_dir=tmp_path)
    resolved_from_path = find_local_paper_pdf(doi=f"{tmp_path}/10.1000_foo_bar.pdf", papers_dir=tmp_path)

    assert resolved_from_encoded == pdf_path.resolve()
    assert resolved_from_path == pdf_path.resolve()
