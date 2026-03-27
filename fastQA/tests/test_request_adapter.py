import pytest

from app.services.request_adapter import RequestAdapterError, adapt_gateway_ask_payload


def test_adapter_builds_kb_request_from_gateway_payload():
    request = adapt_gateway_ask_payload(
        {
            "question": "  explain LFP aging  ",
            "conversation_id": "12",
            "chat_history": [{"role": "user", "content": "prev"}, "bad"],
            "requested_mode": "fast",
            "actual_mode": "fast",
            "route": "kb_qa",
            "turn_mode": "kb_only",
            "allow_kb_verification": False,
            "trace_id": "trace-1",
            "use_generation_driven": True,
            "n_results_per_claim": 8,
            "options": {
                "n_results_per_claim": 6,
                "active_stream_count": 3,
            },
        }
    )

    assert request.question == "explain LFP aging"
    assert request.conversation_id == 12
    assert request.chat_history == [{"role": "user", "content": "prev"}]
    assert request.route == "kb_qa"
    assert request.route_was_explicit is True
    assert request.request_use_generation_driven is True
    assert request.n_results_per_claim == 8
    assert request.active_stream_count == 3
    assert request.to_qakb_payload()["route_hint"] == "kb_qa"
    assert request.to_qakb_payload()["use_generation_driven"] is True


def test_adapter_accepts_pdf_route_and_execution_files():
    request = adapt_gateway_ask_payload(
        {
            "question": "总结这篇文献",
            "requested_mode": "fast",
            "route": "pdf_qa",
            "source_scope": "pdf",
            "execution_files": [{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}],
            "turn_mode": "file_only",
        }
    )

    assert request.route == "pdf_qa"
    assert request.source_scope == "pdf"
    assert request.execution_files[0]["file_id"] == 1
    assert request.turn_mode == "file_only"


def test_adapter_infers_file_routes_when_route_not_provided():
    pdf_request = adapt_gateway_ask_payload(
        {
            "question": "总结上传文件",
            "requested_mode": "fast",
            "execution_files": [{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}],
        }
    )
    assert pdf_request.route == "pdf_qa"
    assert pdf_request.route_was_explicit is False
    assert pdf_request.source_scope == "pdf"

    hybrid_request = adapt_gateway_ask_payload(
        {
            "question": "结合这些文件分析",
            "requested_mode": "fast",
            "execution_files": [
                {"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"},
                {"file_id": 2, "file_type": "excel", "local_path": "/tmp/demo.xlsx"},
            ],
        }
    )
    assert hybrid_request.route == "hybrid_qa"
    assert hybrid_request.source_scope == "pdf+table"


def test_adapter_uses_route_hint_fallback_and_defaults():
    request = adapt_gateway_ask_payload(
        {
            "question": "hello",
            "route_hint": "kb_qa",
            "requested_mode": "fast",
        }
    )

    assert request.actual_mode == "fast"
    assert request.route == "kb_qa"
    assert request.turn_mode == "kb_only"
    assert request.n_results_per_claim == 10
    assert request.active_stream_count is None


def test_adapter_accepts_legacy_top_level_generation_fields():
    request = adapt_gateway_ask_payload(
        {
            "question": "hello",
            "requested_mode": "fast",
            "use_generation_driven": True,
            "n_results_per_claim": 8,
            "active_stream_count": 4,
        }
    )

    assert request.request_use_generation_driven is True
    assert request.n_results_per_claim == 8
    assert request.active_stream_count == 4


def test_adapter_preserves_gateway_file_selection_contract():
    request = adapt_gateway_ask_payload(
        {
            "question": "结合文件和知识库分析",
            "requested_mode": "fast",
            "route": "hybrid_qa",
            "source_scope": "pdf+table+kb",
            "kb_enabled": True,
            "selected_file_ids": ["11", 12],
            "primary_file_id": "11",
            "file_selection": {
                "strategy": "gateway",
                "selection_semantic": "upstream_selected",
            },
            "execution_files": [
                {"file_id": 11, "file_type": "pdf", "local_path": "/tmp/demo.pdf"},
                {"file_id": 12, "file_type": "excel", "local_path": "/tmp/demo.xlsx"},
            ],
        }
    )

    assert request.source_scope == "pdf+table+kb"
    assert request.kb_enabled is True
    assert request.selected_file_ids == [11, 12]
    assert request.primary_file_id == 11
    assert request.file_selection == {
        "strategy": "gateway",
        "selection_semantic": "upstream_selected",
        "source_scope": "pdf+table+kb",
        "kb_enabled": True,
        "selected_file_ids": [11, 12],
        "primary_file_id": 11,
    }
    assert request.to_qakb_payload()["source_scope"] == "pdf+table+kb"
    assert request.to_qakb_payload()["kb_enabled"] is True
    assert request.to_qakb_payload()["selected_file_ids"] == [11, 12]
    assert request.to_qakb_payload()["primary_file_id"] == 11
    assert request.to_qakb_payload()["file_selection"]["strategy"] == "gateway"


def test_adapter_rejects_non_fast_mode():
    try:
        adapt_gateway_ask_payload({"question": "hello", "requested_mode": "thinking"})
    except RequestAdapterError as exc:
        assert exc.code == "mode_not_supported"
    else:
        raise AssertionError("expected RequestAdapterError")


def test_adapter_accepts_gateway_rerouted_file_request_with_fast_actual_mode():
    request = adapt_gateway_ask_payload(
        {
            "question": "总结这篇文献",
            "requested_mode": "thinking",
            "actual_mode": "fast",
            "route": "pdf_qa",
            "source_scope": "pdf",
            "execution_files": [{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}],
        }
    )

    assert request.requested_mode == "thinking"
    assert request.actual_mode == "fast"
    assert request.route == "pdf_qa"


def test_adapter_rejects_unknown_route():
    try:
        adapt_gateway_ask_payload({"question": "hello", "requested_mode": "fast", "route": "unknown"})
    except RequestAdapterError as exc:
        assert exc.code == "route_invalid"
        assert exc.detail["route"] == "unknown"
    else:
        raise AssertionError("expected RequestAdapterError")


def test_adapter_rejects_pdf_route_without_pdf_input():
    try:
        adapt_gateway_ask_payload(
            {
                "question": "hello",
                "requested_mode": "fast",
                "route": "pdf_qa",
                "source_scope": "pdf",
                "execution_files": [{"file_id": 2, "file_type": "excel", "local_path": "/tmp/demo.xlsx"}],
            }
        )
    except RequestAdapterError as exc:
        assert exc.code == "execution_files_required"
        assert exc.detail["route"] == "pdf_qa"
    else:
        raise AssertionError("expected RequestAdapterError")


def test_adapter_rejects_hybrid_route_without_both_pdf_and_table():
    try:
        adapt_gateway_ask_payload(
            {
                "question": "hello",
                "requested_mode": "fast",
                "route": "hybrid_qa",
                "source_scope": "pdf+table",
                "execution_files": [{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}],
            }
        )
    except RequestAdapterError as exc:
        assert exc.code == "execution_files_required"
        assert exc.detail["route"] == "hybrid_qa"
    else:
        raise AssertionError("expected RequestAdapterError")


@pytest.mark.parametrize(
    ("route", "source_scope", "execution_files"),
    [
        ("pdf_qa", "table", [{"file_id": 2, "file_type": "excel", "local_path": "/tmp/demo.xlsx"}]),
        ("tabular_qa", "pdf", [{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}]),
        ("hybrid_qa", "pdf", [{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}]),
    ],
)
def test_adapter_rejects_route_and_source_scope_mismatch(route, source_scope, execution_files):
    with pytest.raises(RequestAdapterError) as exc_info:
        adapt_gateway_ask_payload(
            {
                "question": "hello",
                "requested_mode": "fast",
                "route": route,
                "source_scope": source_scope,
                "execution_files": execution_files,
            }
        )

    assert exc_info.value.code == "source_scope_invalid"
    assert exc_info.value.detail["route"] == route
    assert exc_info.value.detail["source_scope"] == source_scope


@pytest.mark.parametrize(
    ("source_scope", "execution_files"),
    [
        ("pdf+kb", [{"file_id": 1, "file_type": "excel", "local_path": "/tmp/demo.xlsx"}]),
        ("table+kb", [{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}]),
        ("pdf+table", [{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}]),
        ("pdf+table+kb", [{"file_id": 1, "file_type": "excel", "local_path": "/tmp/demo.xlsx"}]),
    ],
)
def test_adapter_rejects_hybrid_source_scope_when_required_file_types_are_missing(source_scope, execution_files):
    with pytest.raises(RequestAdapterError) as exc_info:
        adapt_gateway_ask_payload(
            {
                "question": "hello",
                "requested_mode": "fast",
                "route": "hybrid_qa",
                "source_scope": source_scope,
                "execution_files": execution_files,
            }
        )

    assert exc_info.value.code == "execution_files_required"
    assert exc_info.value.detail["route"] == "hybrid_qa"
    assert exc_info.value.detail["source_scope"] == source_scope


def test_adapter_rejects_invalid_primary_file_id():
    with pytest.raises(RequestAdapterError) as exc_info:
        adapt_gateway_ask_payload(
            {
                "question": "hello",
                "requested_mode": "fast",
                "route": "pdf_qa",
                "source_scope": "pdf",
                "selected_file_ids": [2],
                "primary_file_id": 1,
                "execution_files": [{"file_id": 2, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}],
            }
        )

    assert exc_info.value.code == "primary_file_invalid"
    assert exc_info.value.detail["primary_file_id"] == 1
    assert exc_info.value.detail["selected_file_ids"] == [2]


def test_adapter_truncates_chat_history_to_last_ten_messages():
    request = adapt_gateway_ask_payload(
        {
            "question": "hello",
            "requested_mode": "fast",
            "chat_history": [{"role": "user", "content": str(index)} for index in range(12)],
        }
    )

    assert len(request.chat_history) == 10
    assert request.chat_history[0]["content"] == "2"
    assert request.chat_history[-1]["content"] == "11"


def test_adapter_accepts_user_id_from_body_or_options():
    body_request = adapt_gateway_ask_payload(
        {
            "question": "hello",
            "requested_mode": "fast",
            "user_id": "7",
        }
    )
    option_request = adapt_gateway_ask_payload(
        {
            "question": "hello",
            "requested_mode": "fast",
            "options": {"user_id": 9},
        }
    )

    assert body_request.user_id == 7
    assert option_request.user_id == 9
