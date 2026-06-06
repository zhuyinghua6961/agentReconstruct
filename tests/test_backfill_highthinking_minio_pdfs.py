from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "deploy" / "scripts" / "backfill_highthinking_minio_pdfs.py"


def load_script_module():
    spec = importlib.util.spec_from_file_location("backfill_highthinking_minio_pdfs", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_upload_plan_targets_each_configured_minio(tmp_path: Path) -> None:
    source_pdf = tmp_path / "10.7538_yzk.2017.51.02.0354.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    report = tmp_path / "found.tsv"
    report.write_text(
        "\t".join(["match_type", "doi", "expected_key", "matched_basename", "local_path"]) + "\n"
        + "\t".join(
            [
                "exact",
                "10.7538/yzk.2017.51.02.0354",
                "papers/10.7538_yzk.2017.51.02.0354.pdf",
                source_pdf.name,
                str(source_pdf),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    module = load_script_module()
    endpoints = [
        module.MinioTarget("local9101", "http://127.0.0.1:9101", "agentcode"),
        module.MinioTarget("local19001", "http://127.0.0.1:19001", "agentcode"),
    ]

    plan = module.build_upload_plan(report, endpoints)

    assert len(plan.items) == 2
    assert [item.target.alias for item in plan.items] == ["local9101", "local19001"]
    assert {item.object_key for item in plan.items} == {"papers/10.7538_yzk.2017.51.02.0354.pdf"}
    assert plan.items[0].source_path == source_pdf
    assert plan.skipped == []


def test_build_upload_plan_skips_rows_without_local_pdf(tmp_path: Path) -> None:
    missing_pdf = tmp_path / "missing.pdf"
    report = tmp_path / "found.tsv"
    report.write_text(
        "\t".join(["match_type", "doi", "expected_key", "matched_basename", "local_path"]) + "\n"
        + "\t".join(["exact", "10.1002/foo", "papers/10.1002_foo.pdf", "missing.pdf", str(missing_pdf)])
        + "\n",
        encoding="utf-8",
    )

    module = load_script_module()
    endpoints = [module.MinioTarget("local9101", "http://127.0.0.1:9101", "agentcode")]

    plan = module.build_upload_plan(report, endpoints)

    assert plan.items == []
    assert len(plan.skipped) == 1
    assert "local file missing" in plan.skipped[0]
