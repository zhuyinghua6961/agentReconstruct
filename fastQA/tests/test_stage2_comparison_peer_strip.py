"""Tests for comparison-mode embedding must_include peer stripping."""

from __future__ import annotations

from app.modules.generation_pipeline.stage2_retrieval import (
    _strip_peer_comparison_terms,
)


def test_strip_peer_terms_drops_other_route_focus():
    plan = {
        "enabled": True,
        "objects": [
            {"label": "铁红", "aliases": ["Fe2O3"]},
            {"label": "磷酸铁", "aliases": ["FePO4"]},
            {"label": "草酸亚铁", "aliases": ["FeC2O4"]},
        ],
    }
    focus = ["铁红路线", "磷酸铁路线", "草酸亚铁路线", "反应机理", "性能特点"]
    kept, dropped = _strip_peer_comparison_terms(
        focus,
        current_object="铁红",
        comparison_plan=plan,
    )
    assert "铁红路线" in kept
    assert "反应机理" in kept
    assert "磷酸铁路线" in dropped
    assert "草酸亚铁路线" in dropped


def test_strip_skips_when_current_not_in_plan():
    plan = {
        "enabled": True,
        "objects": [{"label": "A", "aliases": []}, {"label": "B", "aliases": []}],
    }
    focus = ["A", "B", "x"]
    kept, dropped = _strip_peer_comparison_terms(
        focus,
        current_object="未知对象",
        comparison_plan=plan,
    )
    assert kept == focus
    assert dropped == []
