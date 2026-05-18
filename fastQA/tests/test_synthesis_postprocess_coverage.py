"""Unit tests for Stage4 top-k citation coverage logging."""

from __future__ import annotations

from app.modules.generation_pipeline.synthesis_postprocess import log_topk_coverage


class _CapturingLogger:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.infos: list[str] = []

    def warning(self, msg: str, *args) -> None:
        self.warnings.append(msg % args if args else msg)

    def info(self, msg: str, *args) -> None:
        self.infos.append(msg % args if args else msg)


def test_log_topk_coverage_matches_slash_cited_to_underscore_top_target():
    """Filesystem-style DOI keys must not false-alarm vs canonical answer DOIs."""
    cited = {"10.1016/j.example.2020.01.001"}
    top_refs = [("10.1016_j.example.2020.01.001", 1.0)]
    logger = _CapturingLogger()

    log_topk_coverage(set(cited), top_refs, logger, label="top-k")

    assert not logger.warnings
    assert any("成功引用了全部 1 篇" in line for line in logger.infos)


def test_log_topk_coverage_warns_when_genuinely_missing():
    cited = {"10.1016/j.other.2021.02.002"}
    top_refs = [
        ("10.1016_j.example.2020.01.001", 1.0),
        ("10.1016_j.other.2021.02.002", 0.9),
    ]
    logger = _CapturingLogger()

    log_topk_coverage(set(cited), top_refs, logger, label="top-k")

    assert any("未引用以下 1 篇" in w for w in logger.warnings)
    assert any("10.1016_j.example.2020.01.001" in w for w in logger.warnings)
    assert any("引用了 1/2" in line for line in logger.infos)
