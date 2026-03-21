from types import SimpleNamespace

from app.modules.file_context import resolve_request_file_context


def _logger():
    return SimpleNamespace(info=lambda *args, **kwargs: None, warning=lambda *args, **kwargs: None)


def _pdf_file(file_id: int = 1) -> dict:
    return {
        "file_id": file_id,
        "file_no": file_id,
        "display_no": file_id,
        "file_type": "pdf",
        "file_name": "10.1_demo.pdf",
        "file_status": "active",
        "local_path": f"/tmp/{file_id}.pdf",
        "storage_ref": "",
        "parse_status": "ready",
        "index_status": "ready",
        "processing_stage": "ready",
        "last_error": "",
        "deleted_at": None,
        "deleted_by": None,
        "file_meta": {},
    }


def test_file_context_selects_pdf_route_for_single_pdf_question():
    result = resolve_request_file_context(
        question="总结这篇文献",
        conversation_id=1,
        pdf_context={},
        current_pdf_path=None,
        list_uploaded_files_fn=lambda _cid: [_pdf_file()],
        logger=_logger(),
    )

    assert result["route_hint"] == "pdf_qa"
    assert result["turn_mode"] == "file_only"
    assert result["selected_file_ids"] == [1]


def test_file_context_can_fall_back_to_kb_when_selected_file_not_referenced():
    result = resolve_request_file_context(
        question="LFP 的主要应用方向有哪些",
        conversation_id=1,
        pdf_context={"selected_ids": [1]},
        current_pdf_path=None,
        list_uploaded_files_fn=lambda _cid: [_pdf_file()],
        logger=_logger(),
    )

    assert result["route_hint"] == "kb_qa"
    assert result["turn_mode"] == "kb_only"
    assert result["used_files"] == []
