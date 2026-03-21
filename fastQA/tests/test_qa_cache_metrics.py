from app.modules.qa_cache import increment_cache_metric, reset_cache_metrics, snapshot_cache_metrics


def test_cache_metrics_accumulate_by_namespace():
    reset_cache_metrics()

    increment_cache_metric("stage1", "cache_hit")
    increment_cache_metric("stage1", "cache_hit", 2)
    increment_cache_metric("stage2", "cache_write")

    snapshot = snapshot_cache_metrics()

    assert snapshot["all"]["cache_hit"] == 3
    assert snapshot["all"]["cache_write"] == 1
    assert snapshot["stage1"]["cache_hit"] == 3
    assert snapshot["stage2"]["cache_write"] == 1

