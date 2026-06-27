from __future__ import annotations

import sys
from pathlib import Path

import pytest


PATENT_ROOT = Path(__file__).resolve().parents[1]
if str(PATENT_ROOT) not in sys.path:
    sys.path.insert(0, str(PATENT_ROOT))


@pytest.fixture(autouse=True)
def _patent_tests_allow_local_file_paths(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Most file-route fixtures use local_path; strict MinIO-only tests opt in explicitly."""
    if request.node.get_closest_marker("strict_minio_only"):
        return
    monkeypatch.setenv("PATENT_ORIGINAL_MINIO_ONLY", "false")
