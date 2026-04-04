from __future__ import annotations

import pytest

from server.patent.cache_keys import PatentKeyFactory
from server.patent.original_models import OriginalViewResult
from server.patent.original_service import (
    OriginalViewService,
    build_original_anchor,
    build_original_viewer_uri,
    parse_original_request,
)
from server.services.execution_cache import ExecutionCache


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.expiry: dict[str, int | None] = {}

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        self.expiry[key] = ex
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        removed = self.store.pop(key, None)
        self.expiry.pop(key, None)
        return 1 if removed is not None else 0


def test_parse_original_request_normalizes_anchor_forms():
    claim_request = parse_original_request(
        canonical_patent_id="CN123456789A",
        section="claim",
        claim_number="1",
        paragraph_id=None,
        response_format="json",
    )
    paragraph_request = parse_original_request(
        canonical_patent_id="CN123456789A",
        section="description",
        claim_number=None,
        paragraph_id="p-12",
        response_format="html",
    )
    abstract_request = parse_original_request(
        canonical_patent_id="CN123456789A",
        section="abstract",
        claim_number=None,
        paragraph_id=None,
        response_format="text",
    )
    description_request = parse_original_request(
        canonical_patent_id="CN123456789A",
        section="description",
        claim_number=None,
        paragraph_id=None,
        response_format="text",
    )
    figure_request = parse_original_request(
        canonical_patent_id="CN123456789A",
        section="figure",
        claim_number=None,
        paragraph_id=None,
        response_format="redirect",
    )
    fulltext_request = parse_original_request(
        canonical_patent_id="CN123456789A",
        section="fulltext",
        claim_number=None,
        paragraph_id=None,
        response_format="html",
    )

    assert claim_request.anchor == "claim:1"
    assert paragraph_request.anchor == "paragraph:p-12"
    assert abstract_request.anchor == "section:abstract"
    assert description_request.anchor == "section:description"
    assert figure_request.anchor == "section:figure"
    assert fulltext_request.anchor == "fulltext"


def test_build_original_viewer_uri_uses_gateway_relative_path():
    request = parse_original_request(
        canonical_patent_id="CN123456789A",
        section="claim",
        claim_number="1",
        paragraph_id=None,
        response_format="html",
    )

    viewer_uri = build_original_viewer_uri(request)

    assert viewer_uri == "/api/patent/original/CN123456789A?section=claim&claim_number=1&format=html"


def test_original_service_cache_hit_rewrites_trace_id_for_current_request():
    redis = _FakeRedis()
    execution_cache = ExecutionCache(redis, PatentKeyFactory(env="test"))
    provider_calls: list[str] = []

    def _provider(request, trace_id, head_only):
        provider_calls.append(trace_id)
        assert head_only is False
        return OriginalViewResult(
            kind="content",
            status_code=200,
            headers={},
            payload={
                "success": True,
                "canonical_patent_id": request.canonical_patent_id,
                "trace_id": trace_id,
            },
        )

    service = OriginalViewService(execution_cache=execution_cache, provider=_provider)

    first = service.get_original_view(
        canonical_patent_id="CN123456789A",
        section="fulltext",
        claim_number=None,
        paragraph_id=None,
        response_format="json",
        head_only=False,
        trace_id="req_original_1",
    )
    second = service.get_original_view(
        canonical_patent_id="CN123456789A",
        section="fulltext",
        claim_number=None,
        paragraph_id=None,
        response_format="json",
        head_only=False,
        trace_id="req_original_2",
    )

    assert provider_calls == ["req_original_1"]
    assert first["payload"]["trace_id"] == "req_original_1"
    assert second["payload"]["trace_id"] == "req_original_2"


def test_original_service_passes_head_only_flag_to_provider():
    provider_calls: list[tuple[str, bool]] = []

    def _provider(request, trace_id, head_only):
        provider_calls.append((trace_id, head_only))
        return OriginalViewResult(
            kind="redirect",
            status_code=302,
            headers={"Cache-Control": "public, max-age=300"},
            redirect_url=f"https://provider.example/patent/{request.canonical_patent_id}",
        )

    service = OriginalViewService(provider=_provider)

    response = service.get_original_view(
        canonical_patent_id="CN123456789A",
        section="fulltext",
        claim_number=None,
        paragraph_id=None,
        response_format="redirect",
        head_only=True,
        trace_id="req_head_1",
    )

    assert provider_calls == [("req_head_1", True)]
    assert response["kind"] == "redirect"
    assert response["redirect_url"] == "https://provider.example/patent/CN123456789A"


def test_original_service_does_not_reuse_head_cached_payload_for_get():
    redis = _FakeRedis()
    execution_cache = ExecutionCache(redis, PatentKeyFactory(env="test"))
    provider_calls: list[tuple[str, bool]] = []

    def _provider(request, trace_id, head_only):
        provider_calls.append((trace_id, head_only))
        return OriginalViewResult(
            kind="content",
            status_code=200,
            headers={},
            payload={
                "success": True,
                "canonical_patent_id": request.canonical_patent_id,
                "trace_id": trace_id,
                "content": "" if head_only else "full body",
            },
        )

    service = OriginalViewService(execution_cache=execution_cache, provider=_provider)

    head_response = service.get_original_view(
        canonical_patent_id="CN123456789A",
        section="fulltext",
        claim_number=None,
        paragraph_id=None,
        response_format="json",
        head_only=True,
        trace_id="req_head_first",
    )
    get_response = service.get_original_view(
        canonical_patent_id="CN123456789A",
        section="fulltext",
        claim_number=None,
        paragraph_id=None,
        response_format="json",
        head_only=False,
        trace_id="req_get_second",
    )

    assert provider_calls == [("req_head_first", True), ("req_get_second", False)]
    assert head_response["payload"]["content"] == ""
    assert get_response["payload"]["content"] == "full body"


def test_original_service_cached_html_stub_does_not_replay_prior_trace_id():
    redis = _FakeRedis()
    execution_cache = ExecutionCache(redis, PatentKeyFactory(env="test"))
    service = OriginalViewService(execution_cache=execution_cache)

    first = service.get_original_view(
        canonical_patent_id="CN123456789A",
        section="claim",
        claim_number=1,
        paragraph_id=None,
        response_format="html",
        head_only=False,
        trace_id="req_html_first",
    )
    second = service.get_original_view(
        canonical_patent_id="CN123456789A",
        section="claim",
        claim_number=1,
        paragraph_id=None,
        response_format="html",
        head_only=False,
        trace_id="req_html_second",
    )

    assert isinstance(first["payload"], str)
    assert isinstance(second["payload"], str)
    assert "req_html_first" not in first["payload"]
    assert "req_html_second" not in second["payload"]


def test_original_service_original_cache_does_not_collapse_distinct_anchor_values():
    redis = _FakeRedis()
    execution_cache = ExecutionCache(redis, PatentKeyFactory(env="test"))
    provider_calls: list[str] = []

    def _provider(request, trace_id, head_only):
        provider_calls.append(request.anchor)
        return OriginalViewResult(
            kind="content",
            status_code=200,
            headers={},
            payload={
                "success": True,
                "anchor": request.anchor,
                "trace_id": trace_id,
            },
        )

    service = OriginalViewService(execution_cache=execution_cache, provider=_provider)

    first = service.get_original_view(
        canonical_patent_id="CN123456789A",
        section="description",
        claim_number=None,
        paragraph_id="p-1",
        response_format="json",
        head_only=False,
        trace_id="req_anchor_1",
    )
    second = service.get_original_view(
        canonical_patent_id="CN123456789A",
        section="description",
        claim_number=None,
        paragraph_id="p-1:",
        response_format="json",
        head_only=False,
        trace_id="req_anchor_2",
    )

    assert provider_calls == ["paragraph:p-1", "paragraph:p-1:"]
    assert first["payload"]["anchor"] == "paragraph:p-1"
    assert second["payload"]["anchor"] == "paragraph:p-1:"


@pytest.mark.parametrize(
    ("section", "claim_number", "paragraph_id", "expected"),
    [
        ("claim", 1, None, "claim:1"),
        ("description", None, "p-9", "paragraph:p-9"),
        ("abstract", None, None, "section:abstract"),
        ("description", None, None, "section:description"),
        ("figure", None, None, "section:figure"),
        ("fulltext", None, None, "fulltext"),
    ],
)
def test_build_original_anchor(section, claim_number, paragraph_id, expected):
    assert build_original_anchor(section=section, claim_number=claim_number, paragraph_id=paragraph_id) == expected
