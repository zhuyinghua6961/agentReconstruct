from __future__ import annotations

import traceback

from server.patent.pdf_extraction import exclude_references_section, extract_pdf_text


class _Logger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(str(message))

    def warning(self, message: str) -> None:
        self.messages.append(str(message))

    def error(self, message: str) -> None:
        self.messages.append(str(message))

    def debug(self, message: str) -> None:
        self.messages.append(str(message))


class _FakePage:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_text(self) -> str:
        return self._text


class _FakeDoc:
    def __init__(self, *, metadata: dict[str, str], pages: list[str]) -> None:
        self.metadata = metadata
        self._pages = [_FakePage(text) for text in pages]
        self.page_count = len(self._pages)
        self.closed = False

    def __getitem__(self, index: int) -> _FakePage:
        return self._pages[index]

    def close(self) -> None:
        self.closed = True


class _RaisingFitz:
    def open(self, _path: str) -> _FakeDoc:
        raise RuntimeError("boom")


class _FakeFitz:
    def __init__(self, document: _FakeDoc) -> None:
        self._document = document

    def open(self, _path: str) -> _FakeDoc:
        return self._document


def test_extract_pdf_text_preserves_page_boundaries_and_metadata():
    logger = _Logger()
    fake_doc = _FakeDoc(
        metadata={"title": "Sample Title", "author": "Sample Author"},
        pages=[
            "First page paragraph.\n\nStill page one." * 4,
            "Second page paragraph.\n\nStill page two." * 4,
            "Third page paragraph.",
        ],
    )
    fake_fitz = _FakeFitz(fake_doc)

    result = extract_pdf_text(
        "/tmp/mock.pdf",
        max_pages=2,
        fitz_module=fake_fitz,
        logger=logger,
        traceback_module=traceback,
    )

    assert "标题: Sample Title" in result
    assert "作者: Sample Author" in result
    assert "--- 第 1 页 ---" in result
    assert "--- 第 2 页 ---" in result
    assert "--- 第 3 页 ---" not in result
    assert "Second page paragraph." in result
    assert fake_doc.closed is True


def test_extract_pdf_text_excludes_reference_tail_when_signal_is_strong():
    logger = _Logger()

    kept = exclude_references_section(
        [
            (1, "正文内容"),
            (
                2,
                "References\n10.1000/1\n10.1000/2\n10.1000/3\nhttps://a\nhttps://b\nhttps://c\n2021\n2022\n2023",
            ),
        ],
        logger,
    )

    assert kept == [(1, "正文内容")]


def test_extract_pdf_text_keeps_suspected_reference_page_when_signal_is_weak():
    logger = _Logger()

    kept = exclude_references_section(
        [
            (1, "正文"),
            (2, "References\none citation only"),
        ],
        logger,
    )

    assert len(kept) == 2


def test_extract_pdf_text_returns_empty_string_on_extractor_failure():
    logger = _Logger()

    result = extract_pdf_text(
        "/tmp/mock.pdf",
        fitz_module=_RaisingFitz(),
        logger=logger,
        traceback_module=traceback,
    )

    assert result == ""


def test_extract_pdf_text_returns_empty_string_when_all_pages_are_blank_or_trimmed():
    logger = _Logger()

    blank_result = extract_pdf_text(
        "/tmp/blank.pdf",
        fitz_module=_FakeFitz(_FakeDoc(metadata={"title": "Blank"}, pages=["   ", "\n"])),
        logger=logger,
        traceback_module=traceback,
    )
    trimmed_result = extract_pdf_text(
        "/tmp/references.pdf",
        fitz_module=_FakeFitz(
            _FakeDoc(
                metadata={"title": "References Only"},
                pages=["References\n10.1000/a\n10.1000/b\n10.1000/c\nhttps://a\nhttps://b\nhttps://c"],
            )
        ),
        logger=logger,
        traceback_module=traceback,
    )

    assert blank_result == ""
    assert trimmed_result == ""
