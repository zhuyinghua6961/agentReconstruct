from types import SimpleNamespace

from app.modules.documents.api import view_pdf


class _FakeRequest:
    def __init__(self, papers_dir):
        self.app = SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(papers_dir=papers_dir), logger=None), logger=None)
        self.method = "GET"


def test_view_pdf_returns_inline_file_response(tmp_path):
    pdf_path = tmp_path / "10.1_demo.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%test\n")
    request = _FakeRequest(tmp_path)
    response = view_pdf("10.1/demo", request)
    request.method = "HEAD"
    head_response = view_pdf("10.1/demo", request)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.headers["content-disposition"].startswith("inline;")
    assert head_response.status_code == 200
    assert head_response.headers["content-disposition"].startswith("inline;")
