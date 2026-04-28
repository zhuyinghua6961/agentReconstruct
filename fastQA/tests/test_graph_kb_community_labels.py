from __future__ import annotations

from app.modules.graph_kb.community_labels import build_community_label


def test_builds_label_from_title_and_method_without_raw_id():
    label = build_community_label(
        community_id=585242,
        titles=("High performance LiFePO4 cathode material",),
        materials=("LiFePO4/C",),
        methods=("LiFePO4 solvothermal synthesis",),
    )

    assert "LiFePO4" in label
    assert "solvothermal" in label.lower() or "synthesis" in label.lower()
    assert "585242" not in label


def test_builds_generic_label_when_representatives_are_sparse():
    label = build_community_label(community_id=1, titles=(), materials=(), methods=())

    assert label
    assert "1" not in label
