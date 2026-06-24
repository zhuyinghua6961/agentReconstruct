from __future__ import annotations

import json

import pytest

from server.patent.archive_loader import PatentArchiveLoader
from server.patent.browse_search_cache import (
    build_patent_search_cache_key,
    get_patent_search_cache,
    set_patent_search_cache,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def delete(self, key):
        return 1 if self.store.pop(key, None) is not None else 0


def test_build_patent_search_cache_key_is_stable():
    first = build_patent_search_cache_key(
        query="磷酸铁锂",
        query_type="topic",
        sources="both",
        limit=20,
    )
    second = build_patent_search_cache_key(
        query="  磷酸铁锂 ",
        query_type="auto",
        sources="both",
        limit=20,
    )
    assert first == second


def test_patent_search_cache_roundtrip(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setenv("PATENT_SEARCH_REDIS_CACHE_ENABLED", "1")
    monkeypatch.setattr("server.patent.browse_search_cache._REDIS_RESOLVED", False)
    monkeypatch.setattr("server.patent.browse_search_cache._get_redis_client", lambda: fake)

    key = build_patent_search_cache_key(query="battery", query_type="topic", sources="abstract", limit=5)
    assert get_patent_search_cache(key) is None
    set_patent_search_cache(
        key,
        {
            "items": [{"canonical_patent_id": "CN123456789A"}],
            "count": 1,
        },
    )
    cached = get_patent_search_cache(key)
    assert cached is not None
    assert cached["count"] == 1
    assert cached["cache_meta"]["hit"] is True


def test_archive_loader_extracts_applicant_and_ipc(tmp_path):
    patent_dir = tmp_path / "CN123456789A"
    patent_dir.mkdir()
    (patent_dir / "著录项目.json").write_text(
        json.dumps(
            {
                "data": [
                    {
                        "pn": "CN123456789A",
                        "bibliographic_data": {
                            "publication_reference": {"country": "CN", "kind": "A", "doc_number": "123456789"},
                            "application_reference": {"doc_number": "CN202410001234X"},
                            "invention_title": [{"text": "一种电池系统"}],
                            "abstracts": [{"text": "摘要文本"}],
                            "applicants": [{"name": "示例电池公司"}],
                            "inventors": [{"name": "张三"}],
                            "classifications_ipc": [{"text": "H01M10/613"}],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    record = PatentArchiveLoader(tmp_path).load_catalog_record("CN123456789A")
    assert record.applicant_names == ["示例电池公司"]
    assert record.inventor_names == ["张三"]
    assert record.ipc_codes == ["H01M10/613"]
