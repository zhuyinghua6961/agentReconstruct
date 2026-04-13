from __future__ import annotations

from pathlib import Path

from app.modules.storage.paper_storage import find_local_paper_pdf
from app.modules.storage.service import storage_service


class _Logger:
    def warning(self, *args, **kwargs):
        return None


class _S3Error(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _MinioClient:
    def __init__(self) -> None:
        self.stat_calls: list[tuple[str, str]] = []
        self.download_calls: list[tuple[str, str, str]] = []
        self.available_objects: set[str] = set()
        self.download_payloads: dict[str, bytes] = {}

    def stat_object(self, bucket: str, object_name: str):
        self.stat_calls.append((bucket, object_name))
        if object_name not in self.available_objects:
            raise _S3Error("NoSuchKey")
        return object()

    def fget_object(self, bucket: str, object_name: str, local_path: str) -> None:
        self.download_calls.append((bucket, object_name, local_path))
        payload = self.download_payloads.get(object_name, b"%PDF-1.4\n%downloaded\n")
        Path(local_path).write_bytes(payload)



def test_build_paper_filename_replaces_all_slashes():
    assert storage_service.build_paper_filename("10.1000/foo/bar") == "10.1000_foo_bar.pdf"



def test_storage_service_normalize_doi_matches_current_fastqa_behavior():
    assert storage_service.normalize_doi("doi:10.1000_demo).") == "10.1000/demo"


def test_storage_service_normalize_doi_strips_equals_prefix():
    assert storage_service.normalize_doi("doi=10.1016/j.psep.2024.10.111") == "10.1016/j.psep.2024.10.111"



def test_storage_service_build_pdf_url_matches_current_contract():
    assert storage_service.build_pdf_url("10.1/demo") == "/api/v1/view_pdf/10.1/demo"



def test_storage_service_build_pdf_url_normalizes_doi_variants():
    assert storage_service.build_pdf_url("(10.2%2Fdemo.pdf)") == "/api/v1/view_pdf/10.2/demo"



def test_storage_service_build_pdf_url_encodes_route_reserved_characters():
    assert storage_service.build_pdf_url("10.2/demo?section#part") == "/api/v1/view_pdf/10.2/demo%3Fsection%23part"



def test_storage_service_build_pdf_links_preserves_order_and_shape():
    assert storage_service.build_pdf_links(["10.1/a", "10.2/b"]) == [
        {"doi": "10.1/a", "pdf_url": "/api/v1/view_pdf/10.1/a"},
        {"doi": "10.2/b", "pdf_url": "/api/v1/view_pdf/10.2/b"},
    ]



def test_find_local_paper_pdf_supports_flattened_pattern_fallback(tmp_path):
    normalized = tmp_path / "10.1000_foo_bar_extra.pdf"
    normalized.write_bytes(b"%PDF-1.4\n%normalized\n")

    resolved = find_local_paper_pdf(doi="10.1000/foo/bar", papers_dir=tmp_path)

    assert resolved == normalized.resolve()



def test_storage_service_ensure_local_paper_pdf_returns_existing_local_file(tmp_path):
    pdf_path = tmp_path / "10.1000_demo.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%existing\n")

    resolved = storage_service.ensure_local_paper_pdf(doi="10.1000/demo", papers_dir=tmp_path)

    assert resolved == pdf_path.resolve()



def test_find_local_paper_pdf_accepts_encoded_or_path_like_doi(tmp_path):
    pdf_path = tmp_path / "10.1000_foo_bar.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%encoded\n")

    resolved_from_encoded = find_local_paper_pdf(doi="10.1000%2Ffoo%2Fbar.pdf", papers_dir=tmp_path)
    resolved_from_path = find_local_paper_pdf(doi=f"{tmp_path}/10.1000_foo_bar.pdf", papers_dir=tmp_path)

    assert resolved_from_encoded == pdf_path.resolve()
    assert resolved_from_path == pdf_path.resolve()



def test_storage_service_paper_exists_checks_object_storage_when_local_missing(monkeypatch, tmp_path):
    client = _MinioClient()
    client.available_objects.add("papers/10.1000_demo.pdf")
    monkeypatch.setattr(storage_service, "_build_minio_client", lambda: (client, "agentcode", _S3Error))

    exists = storage_service.paper_exists(doi="10.1000/demo", papers_dir=tmp_path, logger=_Logger())

    assert exists is True
    assert client.stat_calls == [("agentcode", "papers/10.1000_demo.pdf")]



def test_storage_service_ensure_local_paper_pdf_downloads_from_object_storage(monkeypatch, tmp_path):
    client = _MinioClient()
    object_name = "papers/10.1000_demo.pdf"
    client.available_objects.add(object_name)
    client.download_payloads[object_name] = b"%PDF-1.4\n%downloaded\n"
    monkeypatch.setattr(storage_service, "_build_minio_client", lambda: (client, "agentcode", _S3Error))

    resolved = storage_service.ensure_local_paper_pdf(doi="10.1000/demo", papers_dir=tmp_path, logger=_Logger())

    assert resolved == (tmp_path / "10.1000_demo.pdf").resolve()
    assert resolved.read_bytes() == b"%PDF-1.4\n%downloaded\n"
    assert client.stat_calls == [("agentcode", object_name)]
    assert len(client.download_calls) == 1
