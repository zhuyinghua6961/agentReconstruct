from __future__ import annotations


def test_summary_formatting_module_exposes_deterministic_predicates():
    from server.patent.summary_formatting import (  # type: ignore[attr-defined]
        classify_summary_answer,
        count_primary_summary_headings,
        extract_support_points,
        has_legacy_four_block_structure,
        is_degraded_summary_answer,
    )

    assert is_degraded_summary_answer("未拿到可读的 PDF") is True
    assert is_degraded_summary_answer("## 研究目的和背景\n- 原文给出了研究动机。") is False
    assert extract_support_points("短句", max_items=4, min_chars=10) == []
    assert extract_support_points("- 这是足够长的模型要点内容。", max_items=4, min_chars=10) == ["这是足够长的模型要点内容。"]
    assert extract_support_points("这里是一条足够长的第一句。这里是一条足够长的第二句。", max_items=4, min_chars=10) == [
        "这里是一条足够长的第一句。",
        "这里是一条足够长的第二句。",
    ]
    assert (
        count_primary_summary_headings(
            "\n".join(
                    [
                        "## 研究目的和背景",
                        "- 这里提供足够长的背景要点内容。",
                        "",
                        "## 研究方法/实验设计",
                        "- 这里提供足够长的方法要点内容。",
                        "",
                        "## 主要发现和结果",
                        "- 这里提供足够长的结果要点内容。",
                        "",
                        "## 结论和意义",
                        "- 这里提供足够长的结论要点内容。",
                    ]
                )
            )
            == 4
    )
    assert count_primary_summary_headings("研究目的和背景是在原文引言里展开说明的。") == 0
    assert count_primary_summary_headings("## 研究目的和背景是在原文引言里展开说明的。") == 0
    assert has_legacy_four_block_structure("## 结论\n- A\n\n## 证据\n- B\n\n## 对比\n- C\n\n## 限制\n- D") is True
    assert (
        classify_summary_answer(
            "\n".join(
                    [
                        "## 研究目的和背景",
                        "- 这里提供足够长的背景要点内容。",
                        "",
                        "## 研究方法/实验设计",
                        "- 这里提供足够长的方法要点内容。",
                        "",
                        "## 主要发现和结果",
                        "- 这里提供足够长的结果要点内容。",
                        "",
                        "## 结论和意义",
                        "- 这里提供足够长的结论要点内容。",
                    ]
                ),
                prepared_text="这里提供足够长的补充证据文本内容。",
        )
        == "preserve"
    )
    assert (
        classify_summary_answer(
            "\n".join(
                [
                    "## 结论",
                    "- 这里提供足够长的结论正文内容。",
                    "",
                    "## 证据",
                    "- 这里提供足够长的证据正文内容。",
                    "",
                    "## 对比",
                    "- 这里提供足够长的对比正文内容。",
                    "",
                    "## 限制",
                    "- 这里提供足够长的限制正文内容。",
                ]
            ),
            prepared_text="这里提供足够长的背景补充内容。这里提供足够长的方法补充内容。这里提供足够长的结果补充内容。这里提供足够长的结论补充内容。",
        )
        == "light_repair"
    )
    assert (
        classify_summary_answer(
            "LMFP/LFP 复配改善了高倍率充电安全性。\n长循环验证仍然有限且需要继续补充。",
            prepared_text="这里提供足够长的研究背景描述。\n这里提供足够长的方法描述。\n这里提供足够长的结果描述。\n这里提供足够长的局限性描述。",
        )
        == "conservative_repair"
    )
    assert classify_summary_answer("暂时无法生成，请稍后重试。", prepared_text="这里提供足够长的研究背景描述。") == "fallback"
