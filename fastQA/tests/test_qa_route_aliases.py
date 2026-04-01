from __future__ import annotations

import asyncio

from app.main import app
from app.routers.qa import AskRequest, ask, ask_stream


class _FakeRequest:
    def __init__(self, path: str):
        self.app = app
        self.headers = {}
        self.url = type("_Url", (), {"path": path})()

    async def is_disconnected(self) -> bool:
        return False


async def _collect_streaming_body(response) -> str:
    chunks: list[str] = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
    return "".join(chunks)


def test_v1_ask_alias_uses_json_aggregation(monkeypatch):
    monkeypatch.setattr(
        "app.routers.qa._iter_route_frames",
        lambda **_kwargs: iter(
            [
                {"type": "metadata", "route": "pdf_qa"},
                {"type": "content", "content": "hello"},
                {"type": "done", "route": "pdf_qa", "references": ["10.1/demo"]},
            ]
        ),
    )

    response = ask(
        AskRequest(
            question="总结这篇文献",
            requested_mode="fast",
            route="pdf_qa",
            source_scope="pdf",
            turn_mode="file_only",
            execution_files=[{"file_id": 1, "file_type": "pdf", "local_path": "/tmp/demo.pdf"}],
        ),
        _FakeRequest("/api/v1/ask"),
    )

    assert response.status_code == 200
    assert b'"route":"pdf_qa"' in response.body
    assert b'"final_answer":"hello"' in response.body


def test_v1_ask_stream_adapter_error_returns_json_400():
    response = ask_stream(
        AskRequest(question="hello", requested_mode="thinking"),
        _FakeRequest("/api/v1/ask_stream"),
    )

    assert response.status_code == 400
    assert b'"code":"MODE_NOT_SUPPORTED"' in response.body
