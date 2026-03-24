from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _disable_stage_cache_by_default(monkeypatch):
    monkeypatch.setenv("REDIS_ENABLED", "0")
    from server.services.redis_client import reset_redis_runtime_cache

    reset_redis_runtime_cache()
    yield
    reset_redis_runtime_cache()
