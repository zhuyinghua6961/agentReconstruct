from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import app.modules.documents.reference_preview as reference_preview
import app.modules.qa_kb.streaming as kb_streaming
import app.modules.qa_pdf.common as qa_pdf_common
import app.routers.qa as qa_router
from app.main import app
from app.modules.qa_kb.models import QaKbExecutionMetadata, QaKbExecutionResult
from app.routers.qa import AskRequest, ask, ask_stream


class _StubStorageService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def build_pdf_links(self, references):
        refs = tuple(str(item) for item in references)
        self.calls.append(("build_pdf_links", refs))
        return [{"doi": "stub-doi", "pdf_url": "/stub/pdf"}]

    def build_pdf_url(self, doi: str) -> str:
        self.calls.append(("build_pdf_url", (str(doi),)))
        return "/stub/pdf"

    def build_paper_filename(self, doi: str) -> str:
        return f"{doi}.pdf"

    def paper_exists(self, **_kwargs) -> bool:
        return True


class _FakeRequest:
    def __init__(self, app_instance, path: str = "/api/ask"):
        self.app = app_instance
        self.headers = {}
        self.url = SimpleNamespace(path=path)

    async def is_disconnected(self) -> bool:
        return False


async def _collect_streaming_body(response) -> str:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, bytes):
            chunks.append(chunk.decode("utf-8"))
        else:
            chunks.append(str(chunk))
    return "".join(chunks)


def _decode_sse_frames(body: str) -> list[dict]:
    frames: list[dict] = []
    for line in body.splitlines():
        if not line.startswith("data: "):
            continue
        frames.append(json.loads(line[6:]))
    return frames


def test_kb_streaming_uses_storage_service_for_reference_links(monkeypatch):
    stub = _StubStorageService()
    monkeypatch.setattr(kb_streaming, "storage_service", stub, raising=False)
    result = QaKbExecutionResult(
        success=True,
        final_answer="answer",
        metadata=QaKbExecutionMetadata(route="kb_qa", query_mode="kb_qa"),
        raw={"synthesis_result": {"references": [{"doi": "10.1/a"}]}},
    )

    events = list(kb_streaming.iter_result_events(result=result, sse_event=lambda payload: payload))

    assert events[-1]["reference_links"] == [{"doi": "stub-doi", "pdf_url": "/stub/pdf"}]
    assert events[-1]["pdf_links"] == [{"doi": "stub-doi", "pdf_url": "/stub/pdf"}]
    assert stub.calls == [("build_pdf_links", ("10.1/a",))]


def test_pdf_common_uses_storage_service_for_pdf_links(monkeypatch):
    stub = _StubStorageService()
    monkeypatch.setattr(qa_pdf_common, "storage_service", stub, raising=False)

    payload = qa_pdf_common.build_done_event_payload(["10.1/a"])

    assert payload["pdf_links"] == [{"doi": "stub-doi", "pdf_url": "/stub/pdf"}]
    assert stub.calls == [("build_pdf_links", ("10.1/a",))]


def test_router_done_event_uses_storage_service_for_links(monkeypatch):
    stub = _StubStorageService()
    monkeypatch.setattr(qa_router, "storage_service", stub, raising=False)

    payload = qa_router._done_event(
        route="kb_qa",
        used_files=[],
        trace_id="trace-1",
        references=[{"doi": "10.1/a"}],
    )

    assert payload["reference_links"] == [{"doi": "stub-doi", "pdf_url": "/stub/pdf"}]
    assert payload["pdf_links"] == [{"doi": "stub-doi", "pdf_url": "/stub/pdf"}]
    assert stub.calls == [("build_pdf_links", ("10.1/a",))]


def test_router_sync_done_payload_uses_storage_service_for_links(monkeypatch):
    stub = _StubStorageService()
    monkeypatch.setattr(qa_router, "storage_service", stub, raising=False)

    payload, status_code = qa_router._collect_sync_result(
        [{"type": "done", "route": "kb_qa", "references": [{"doi": "10.1/a"}]}],
        trace_id="trace-1",
        requested_mode="fast",
        actual_mode="fast",
        route="kb_qa",
        used_files=[],
    )

    assert status_code == 200
    assert payload["reference_links"] == [{"doi": "stub-doi", "pdf_url": "/stub/pdf"}]
    assert payload["pdf_links"] == [{"doi": "stub-doi", "pdf_url": "/stub/pdf"}]
    assert stub.calls == [("build_pdf_links", ("10.1/a",))]


def test_reference_preview_item_uses_storage_service_for_pdf_url(monkeypatch, tmp_path):
    stub = _StubStorageService()
    monkeypatch.setattr(reference_preview, "storage_service", stub, raising=False)

    item = reference_preview.build_reference_preview_item(
        doi="10.1/a",
        metadata={},
        papers_dir=Path(tmp_path),
        logger=SimpleNamespace(warning=lambda *_args, **_kwargs: None),
    )

    assert item["pdf_url"] == "/stub/pdf"
    assert ("build_pdf_url", ("10.1/a",)) in stub.calls


def test_pdf_route_sync_and_stream_share_same_storage_link_builder(monkeypatch):
    stub = _StubStorageService()
    monkeypatch.setattr(qa_router, "storage_service", stub, raising=False)

    def _events(**_kwargs):
        yield {"type": "metadata", "query_mode": "PDF文献查询", "route": "pdf_qa"}
        yield {"type": "content", "content": "pdf answer"}
        yield {"type": "done", "route": "pdf_qa", "references": ["10.1/test"]}

    monkeypatch.setattr(qa_router, "iter_pdf_route_events", _events)

    sync_response = ask(
        AskRequest(
            question="总结这篇文献",
            requested_mode="fast",
            route="pdf_qa",
            source_scope="pdf",
            execution_files=[{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}],
        ),
        _FakeRequest(app, "/api/ask"),
    )
    sync_payload = json.loads(sync_response.body)

    stream_response = ask_stream(
        AskRequest(
            question="总结这篇文献",
            requested_mode="fast",
            route="pdf_qa",
            source_scope="pdf",
            execution_files=[{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}],
        ),
        _FakeRequest(app, "/api/ask_stream"),
    )
    stream_body = asyncio.run(_collect_streaming_body(stream_response))
    stream_frames = _decode_sse_frames(stream_body)
    stream_done = next(frame for frame in stream_frames if frame.get("type") == "done")

    assert sync_payload["pdf_links"] == [{"doi": "stub-doi", "pdf_url": "/stub/pdf"}]
    assert sync_payload["reference_links"] == [{"doi": "stub-doi", "pdf_url": "/stub/pdf"}]
    assert stream_done["pdf_links"] == sync_payload["pdf_links"]
    assert stream_done["reference_links"] == sync_payload["reference_links"]
    assert stub.calls.count(("build_pdf_links", ("10.1/test",))) >= 2


def test_stream_done_overrides_upstream_reference_links_with_router_boundary(monkeypatch):
    stub = _StubStorageService()
    monkeypatch.setattr(qa_router, "storage_service", stub, raising=False)

    def _events(**_kwargs):
        yield {"type": "metadata", "query_mode": "PDF文献查询", "route": "pdf_qa"}
        yield {
            "type": "done",
            "route": "pdf_qa",
            "references": ["10.1/test"],
            "reference_links": [{"doi": "bad", "pdf_url": "/bad"}],
            "pdf_links": [{"doi": "bad", "pdf_url": "/bad"}],
        }

    monkeypatch.setattr(qa_router, "iter_pdf_route_events", _events)

    stream_response = ask_stream(
        AskRequest(
            question="总结这篇文献",
            requested_mode="fast",
            route="pdf_qa",
            source_scope="pdf",
            execution_files=[{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}],
        ),
        _FakeRequest(app, "/api/ask_stream"),
    )
    stream_body = asyncio.run(_collect_streaming_body(stream_response))
    stream_frames = _decode_sse_frames(stream_body)
    stream_done = next(frame for frame in stream_frames if frame.get("type") == "done")

    assert stream_done["reference_links"] == [{"doi": "stub-doi", "pdf_url": "/stub/pdf"}]
    assert stream_done["pdf_links"] == [{"doi": "stub-doi", "pdf_url": "/stub/pdf"}]


def test_router_done_event_builds_doi_locations_from_reference_objects(monkeypatch):
    stub = _StubStorageService()
    monkeypatch.setattr(qa_router, "storage_service", stub, raising=False)

    payload = qa_router._done_event(
        route="kb_qa",
        used_files=[],
        trace_id="trace-1",
        references=[
            {
                "doi": "10.1/a",
                "title": "Demo",
                "section_name": "Intro",
                "chunk_index": 7,
                "page": 3,
                "evidence_text": "厚电极在高倍率下会出现显著浓差极化。",
            }
        ],
    )

    assert payload["reference_objects"][0]["evidence_text"] == "厚电极在高倍率下会出现显著浓差极化。"
    assert payload["doi_locations"] == {
        "10.1/a": [
            {
                "page": 3,
                "section": "Intro",
                "chunk_index": 7,
                "source_text": "厚电极在高倍率下会出现显著浓差极化。",
                "source_preview": "厚电极在高倍率下会出现显著浓差极化。",
                "confidence": "page",
            }
        ]
    }


def test_router_done_event_keeps_chunk_evidence_when_page_missing(monkeypatch):
    stub = _StubStorageService()
    monkeypatch.setattr(qa_router, "storage_service", stub, raising=False)

    payload = qa_router._done_event(
        route="kb_qa",
        used_files=[],
        trace_id="trace-2",
        references=[
            {
                "doi": "10.1/a",
                "title": "Demo",
                "chunk_index": 7,
                "evidence_text": "厚电极在高倍率下会出现显著浓差极化。",
            }
        ],
    )

    assert payload["doi_locations"] == {
        "10.1/a": [
            {
                "chunk_index": 7,
                "source_text": "厚电极在高倍率下会出现显著浓差极化。",
                "source_preview": "厚电极在高倍率下会出现显著浓差极化。",
                "confidence": "chunk",
            }
        ]
    }


def test_router_done_event_splits_and_repairs_polluted_reference_dois(monkeypatch):
    stub = _StubStorageService()
    monkeypatch.setattr(qa_router, "storage_service", stub, raising=False)

    payload = qa_router._done_event(
        route="kb_qa",
        used_files=[],
        trace_id="trace-3",
        references=[
            {
                "doi": "10.1016j.jpowsour.2005.03.09910.1016j.jpowsour.2013.06.070",
                "sample_text": "evidence",
            }
        ],
    )

    assert payload["references"] == [
        "10.1016/j.jpowsour.2005.03.099",
        "10.1016/j.jpowsour.2013.06.070",
    ]
