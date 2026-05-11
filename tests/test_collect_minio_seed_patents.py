from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "scripts" / "collect_minio_seed.sh"


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_collect_minio_seed_can_materialize_patent_original_objects(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    patent_dir = source_root / "CNTEST001A"
    (patent_dir / "摘要附图").mkdir(parents=True)
    (patent_dir / "全文附图").mkdir()
    (patent_dir / "CNTEST001A.pdf").write_bytes(b"%PDF-1.4 sample")
    (patent_dir / "摘要附图" / "CNTEST001A.png").write_bytes(b"summary-image")
    (patent_dir / "全文附图" / "CNTEST001A_1.png").write_bytes(b"fulltext-image")
    _write_json(
        patent_dir / "著录项目.json",
        {
            "data": [
                {
                    "pn": "CNTEST001A",
                    "bibliographic_data": {
                        "publication_reference": {"country": "CN", "doc_number": "TEST001", "kind": "A"},
                        "application_reference": {"doc_number": "APP001"},
                        "invention_title": [{"text": "测试专利"}],
                        "abstracts": [{"text": "摘要内容"}],
                    },
                }
            ]
        },
    )
    _write_json(
        patent_dir / "权利要求.json",
        {"data": [{"claims": [{"claim_text": '<div num="1">一种测试方法。</div>'}]}]},
    )
    _write_json(
        patent_dir / "说明书.json",
        {"data": [{"description": [{"text": '<b class="d_n">[0001]</b>说明书内容。'}]}]},
    )

    seed_root = tmp_path / "minio-seed"
    env = {
        **os.environ,
        "DEPLOY_MINIO_SEED_DIR": str(seed_root),
        "PATENT_ORIGINALS_SRC": str(source_root),
        "PUBLIC_SERVICE_PAPERS_SRC": str(tmp_path / "missing-public-papers"),
        "FASTQA_PAPERS_SRC": str(tmp_path / "missing-fast-papers"),
        "FASTQA_LOCAL_PAPERS_SRC": str(tmp_path / "missing-local-papers"),
        "HIGHTHINKINGQA_PAPERS_SRC": str(tmp_path / "missing-thinking-papers"),
    }

    subprocess.run(
        ["bash", str(SCRIPT), "agentcode", "--clean", "--patent-only"],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    target = seed_root / "agentcode" / "patent" / "originals" / "CNTEST001A"
    assert (target / "manifest.json").exists()
    assert (target / "structured" / "claims.json").exists()
    assert (target / "structured" / "description.json").exists()
    assert (target / "structured" / "bibliography.json").exists()
    assert (target / "fulltext" / "original.pdf").read_bytes() == b"%PDF-1.4 sample"
    assert (target / "figures" / "summary" / "CNTEST001A.png").read_bytes() == b"summary-image"
    assert (target / "figures" / "fulltext" / "CNTEST001A_1.png").read_bytes() == b"fulltext-image"

    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["canonical_patent_id"] == "CNTEST001A"
    assert manifest["objects"]["structured"]["claims"] == "patent/originals/CNTEST001A/structured/claims.json"
    assert manifest["objects"]["fulltext_pdf"] == "patent/originals/CNTEST001A/fulltext/original.pdf"


def test_collect_minio_seed_uses_default_bucket_when_first_arg_is_option(tmp_path: Path) -> None:
    seed_root = tmp_path / "minio-seed"
    env = {
        **os.environ,
        "DEPLOY_MINIO_SEED_DIR": str(seed_root),
        "MINIO_BUCKET": "agentcode",
        "PUBLIC_SERVICE_PAPERS_SRC": str(tmp_path / "missing-public-papers"),
        "FASTQA_PAPERS_SRC": str(tmp_path / "missing-fast-papers"),
        "FASTQA_LOCAL_PAPERS_SRC": str(tmp_path / "missing-local-papers"),
        "HIGHTHINKINGQA_PAPERS_SRC": str(tmp_path / "missing-thinking-papers"),
    }

    subprocess.run(
        ["bash", str(SCRIPT), "--papers-only"],
        cwd=ROOT,
        env=env,
        check=True,
        text=True,
        capture_output=True,
    )

    assert (seed_root / "agentcode" / "papers").is_dir()
    assert not (seed_root / "--papers-only").exists()
