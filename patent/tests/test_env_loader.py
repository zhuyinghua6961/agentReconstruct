from __future__ import annotations

import importlib
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config as patent_config  # noqa: E402


def _env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].removeprefix("export ").strip()
        keys.add(key)
    return keys


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


def test_patent_resource_config_surface_removes_fixed_switches_and_uses_shared_endpoints():
    repo_root = ROOT_DIR.parent
    resource_keys = _env_keys(repo_root / "resource/config/services/patent/config.shared.env")
    example_keys = _env_keys(ROOT_DIR / "config.shared.env.example")

    retired = {
        "PATENT_STAGE2_RERANK_ENABLED",
        "PATENT_STAGE2_RERANK_PROVIDER",
        "PATENT_STAGE2_RERANK_BASE_URL",
        "PATENT_STAGE2_RERANK_MODEL",
        "PATENT_STAGE2_RERANK_TIMEOUT_SECONDS",
        "PATENT_STAGE2_RERANK_ENDPOINT_FAMILY",
        "PATENT_REDIS_ENABLED",
        "PATENT_OPENAI_TIMEOUT_SECONDS",
        "PATENT_LLM_HTTP_SHARED_POOL_ENABLED",
        "PATENT_PLANNING_HOT_POOL_ENABLED",
        "PATENT_PLANNING_HOT_POOL_WARMUP_ENABLED",
        "PATENT_PLANNING_HOT_POOL_WARM_INTERVAL_SECONDS",
        "PATENT_PLANNING_HOT_POOL_WARM_TIMEOUT_SECONDS",
        "PATENT_PLANNING_HOT_POOL_WARM_JITTER_SECONDS",
        "PATENT_PLANNING_HOT_POOL_WARM_ACTIVE_START_HOUR",
        "PATENT_PLANNING_HOT_POOL_WARM_ACTIVE_END_HOUR",
        "PATENT_PLANNING_UPSTREAM_GATE_ENABLED",
        "PATENT_EMBEDDING_API_TIMEOUT_SECONDS",
        "PATENT_EMBEDDING_MODEL_PATH",
    }

    assert retired.isdisjoint(resource_keys)
    assert retired.isdisjoint(example_keys)
    assert {
        "PATENT_ASK_STREAM_MAX_CONCURRENT",
        "PATENT_STAGE2_RERANK_CANDIDATES",
        "PATENT_STAGE2_RERANK_TOP_PATENTS",
        "PATENT_PLANNING_HOT_POOL_LANE_COUNT",
        "PATENT_PLANNING_HOT_POOL_LANE_DEGRADED_AFTER_SECONDS",
        "PATENT_PLANNING_UPSTREAM_GATE_LIMIT",
    } <= example_keys
