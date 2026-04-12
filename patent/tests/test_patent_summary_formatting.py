from __future__ import annotations


def test_summary_formatting_module_exposes_deterministic_predicates():
    from server.patent.summary_formatting import (  # type: ignore[attr-defined]
        count_primary_summary_headings,
        extract_support_points,
        is_degraded_summary_answer,
    )

    assert is_degraded_summary_answer("未拿到可读的 PDF") is True
    assert is_degraded_summary_answer("## 研究目的和背景\n- 原文给出了研究动机。") is False
    assert extract_support_points("短句", max_items=4, min_chars=10) == []
    assert extract_support_points("- 足够长的模型要点。", max_items=4, min_chars=10) == ["足够长的模型要点。"]
    assert (
        count_primary_summary_headings(
            "\n".join(
                [
                    "## 研究目的和背景",
                    "- 背景要点",
                    "",
                    "## 研究方法/实验设计",
                    "- 方法要点",
                    "",
                    "## 主要发现和结果",
                    "- 结果要点",
                    "",
                    "## 结论和意义",
                    "- 结论要点",
                ]
            )
        )
        == 4
    )
