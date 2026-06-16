from app.modules.qa_pdf.pdf_context import build_merged_pdf_context


def test_build_merged_pdf_context_uses_literature_headers_and_truncates():
    long_body = "正文段落。" * 5000

    def _loader(**kwargs):
        _ = kwargs
        return long_body, None

    pdf_context, references, loaded_count = build_merged_pdf_context(
        pdf_files=[
            {"file_id": 1, "file_name": "10.1_demo.pdf", "local_path": "/tmp/a.pdf"},
            {"file_id": 2, "file_name": "10.2_demo.pdf", "local_path": "/tmp/b.pdf"},
        ],
        load_pdf_content_fn=_loader,
        question="对比一下这些文献和表格",
        max_pdf_chars=12000,
        logger=None,
    )

    assert loaded_count == 2
    assert "===== 文献 #1:" in pdf_context
    assert "===== 文献 #2:" in pdf_context
    assert len(pdf_context) <= 12000
    assert references == ["10.1/demo", "10.2/demo"]


def test_build_merged_pdf_context_falls_back_to_preview_when_load_fails():
    pdf_context, references, loaded_count = build_merged_pdf_context(
        pdf_files=[
            {
                "file_id": 2,
                "file_name": "10.1_demo.pdf",
                "local_path": "/tmp/demo.pdf",
                "file_meta": {"parsed_preview": "文献提到电压窗口为 3.0-4.2 V。"},
            }
        ],
        load_pdf_content_fn=lambda **kwargs: (None, "unavailable"),
        question="结合文献和表格给结论",
        max_pdf_chars=12000,
        logger=None,
    )

    assert loaded_count == 0
    assert "电压窗口" in pdf_context
    assert references == ["10.1/demo"]
