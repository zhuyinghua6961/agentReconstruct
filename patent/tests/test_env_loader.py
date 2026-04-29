from __future__ import annotations

import importlib
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config as patent_config  # noqa: E402


def test_patent_loads_resource_shared_and_service_config(tmp_path, monkeypatch):
    resource_root = tmp_path / "resource"
    shared_root = resource_root / "config" / "shared"
    service_root = resource_root / "config" / "services" / "patent"
    shared_root.mkdir(parents=True)
    service_root.mkdir(parents=True)
    (shared_root / "infrastructure.shared.env").write_text("PATENT_PORT=19010\n", encoding="utf-8")
    (shared_root / "model-endpoints.shared.env").write_text(
        "LLM_BASE_URL=http://llm.test/v1\nLLM_MODEL=shared-model\n",
        encoding="utf-8",
    )
    (shared_root / "graph.shared.env").write_text(
        "PATENT_NEO4J_URL=bolt://graph.test:8687\nPATENT_NEO4J_DATABASE=neo4j\n",
        encoding="utf-8",
    )
    (service_root / "config.shared.env").write_text("PATENT_STAGE4_MIN_CITATIONS=9\n", encoding="utf-8")

    for name in (
        "PATENT_PORT",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "PATENT_NEO4J_URL",
        "PATENT_NEO4J_DATABASE",
        "PATENT_STAGE4_MIN_CITATIONS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RESOURCE_ROOT", str(resource_root))

    reloaded = importlib.reload(patent_config)
    settings = reloaded.get_settings()

    assert settings.http.port == 19010
    assert settings.graph_kb.neo4j_url == "bolt://graph.test:8687"
