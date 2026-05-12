from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _disable_stage_cache_by_default(monkeypatch):
    from server.services.redis_client import RedisService, reset_redis_runtime_cache
    from server.services import redis_client as redis_client_module
    from server.services import stage_cache as stage_cache_module
    from retriever import vector_retriever

    reset_redis_runtime_cache()
    unavailable_service = RedisService.from_prefix(client=None, key_prefix="highthinkingqa-test")
    monkeypatch.setattr(redis_client_module, "get_redis_service", lambda: unavailable_service)
    monkeypatch.setattr(stage_cache_module, "get_redis_service", lambda: unavailable_service)
    monkeypatch.setattr(vector_retriever, "get_redis_service", lambda: unavailable_service)
    yield
    reset_redis_runtime_cache()
