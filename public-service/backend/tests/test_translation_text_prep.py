from __future__ import annotations

from app.modules.documents.translation_cache_impl import TranslationCache
from app.modules.documents.translation_text_prep import (
    assemble_document_translation_markdown,
    prepare_body_for_document_translation,
)


def test_prepare_body_strips_metadata_and_normalizes_page_markers():
    source = (
        "标题: Sample Paper\n"
        "作者: Alice; Bob\n"
        "============================================================\n\n"
        "--- 第 1 页 ---\n"
        "This is a long sentence that continues\n"
        "on the next line.\n\n"
        "Abstract\n"
        "We study materials.\n\n"
        "References\n"
        "1. Foo et al."
    )
    body, meta = prepare_body_for_document_translation(source)

    assert meta["title"] == "Sample Paper"
    assert meta["authors"] == "Alice; Bob"
    assert "标题:" not in body
    assert "[[PAGE:1]]" in body
    assert "--- 第 1 页 ---" not in body
    assert "continues on the next line." in body.replace("\n", " ")
    assert "References" not in body


def test_assemble_document_translation_markdown_adds_header_and_dedupes_headings():
    segments = [
        "## 引言\n\n第一段。",
        "## 引言\n\n第二段。",
    ]
    markdown = assemble_document_translation_markdown(
        segments,
        meta={"title": "示例论文", "authors": "张三", "doi": "10.1000/test"},
        document_id="10.1000/test",
    )

    assert markdown.startswith("# 示例论文")
    assert "> 作者：张三 | DOI：10.1000/test" in markdown
    assert markdown.count("## 引言") == 1
    assert "第一段。" in markdown
    assert "第二段。" in markdown


def test_translation_cache_key_includes_profile_and_version(monkeypatch, tmp_path):
    monkeypatch.setenv("TRANSLATION_PROMPT_VERSION", "2")
    cache = TranslationCache(cache_dir=str(tmp_path / "cache-v2"))

    assert cache._hash_text("hello", profile="snippet") != cache._hash_text("hello", profile="document")

    hash_v2 = cache._hash_text("hello", profile="document")

    monkeypatch.setenv("TRANSLATION_PROMPT_VERSION", "3")
    cache_v3 = TranslationCache(cache_dir=str(tmp_path / "cache-v3"))
    hash_v3 = cache_v3._hash_text("hello", profile="document")
    assert hash_v2 != hash_v3
