# FastQA Local Rerank Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `local` rerank provider to `fastQA` so Stage2 rerank can call a local OpenAI/xinference-compatible `/v1/rerank` service while preserving the existing DashScope behavior.

**Architecture:** Keep `rerank_documents()` as the single runtime adapter for rerank requests, with provider-specific request construction and shared result normalization. Make rerank env resolution provider-aware so local mode does not inherit DashScope credentials or DashScope default URLs. Update Stage2 rerank hot-lane warmup to call the same provider contract as normal rerank traffic, preventing the hot pool from degrading when local mode is enabled.

**Tech Stack:** Python 3, requests-compatible sessions, FastAPI runtime bootstrap, pytest

---

## Source Documents

- Requirements draft: `fastQA/docs/rerank-local-migration-plan.md`
- Main rerank adapter: `fastQA/app/modules/generation_pipeline/rerank_service.py`
- Env wiring: `fastQA/app/modules/microscopic_expert.py`
- Hot-lane warmup: `fastQA/app/core/runtime.py`
- Existing tests:
  - `fastQA/tests/test_microscopic_expert.py`
  - `fastQA/tests/test_stage2_hot_connection_runtime.py`

## Locked Decisions

1. Implement `provider == "local"` as an OpenAI/xinference-compatible text rerank call:

```json
POST /v1/rerank
{
  "model": "qwen3-vl-rerank",
  "query": "user query",
  "documents": ["doc1", "doc2"],
  "top_n": 2
}
```

2. Parse local responses from top-level `results` using `index` and `relevance_score`.
3. Do not implement the legacy custom `/rerank` object protocol in this pass. That is a separate provider/protocol if needed later.
4. Local mode must not require an API key. If `QA_RETRIEVAL_RERANK_API_KEY` is empty, omit `Authorization`.
5. Local mode must not silently fall back to `DASHSCOPE_API_KEY`.
6. Local mode should default `base_url` to `http://localhost:8084` when `QA_RETRIEVAL_RERANK_BASE_URL` is unset or empty.
7. DashScope mode keeps the current endpoint, payload, API-key requirement, and fallback behavior.
8. Runtime hot-lane warmup must use the same provider-specific protocol as normal rerank calls.
9. Runtime hot-lane warmup must skip outbound HTTP calls for disabled providers (`none`, `off`, `disabled`) and unsupported providers, matching the main rerank path instead of sending DashScope-shaped traffic.
10. Rerank output must be capped to `top_n` after parsing, even if an upstream returns more rows than requested.
11. Invalid result indexes are skipped; if all rows are invalid or empty, fall back to vector order.

## External Prerequisite

This implementation plan covers the `fastQA` side only. End-to-end live validation requires the local reranker at `localhost:8084` to expose an OpenAI/xinference-compatible `POST /v1/rerank` endpoint that accepts:

```json
{
  "model": "qwen3-vl-rerank",
  "query": "user query",
  "documents": ["doc1", "doc2"],
  "top_n": 2
}
```

and returns:

```json
{
  "results": [
    {"index": 0, "relevance_score": 0.92}
  ]
}
```

If the deployed local service still only supports the custom `POST /rerank` contract described in `fastQA/docs/rerank-local-migration-plan.md`, the `fastQA` unit tests can pass but live local rerank will still fail until that service adds `/v1/rerank`. Do not adapt `fastQA` to the custom `/rerank` protocol in this implementation.

## Acceptance Criteria

1. `QA_RETRIEVAL_RERANK_PROVIDER=local` calls `{base_url}/v1/rerank` with string `query`, string-array `documents`, and clamped `top_n`.
2. Local request headers include `Content-Type: application/json` and include `Authorization: Bearer ...` only when a local rerank API key is explicitly configured.
3. DashScope request shape and tests continue to pass unchanged.
4. `provider` values in `{"none", "off", "disabled"}` still return graceful fallback without HTTP calls.
5. Unknown providers still return `provider_unsupported`.
6. Local HTTP errors, JSON errors, and empty valid results return graceful fallback with `fallback_reason="request_failed"` or `empty_rerank_result` as appropriate.
7. `MicroscopicSemanticExpert._rerank_documents()` passes an empty `api_key` in local mode when only `DASHSCOPE_API_KEY` is configured.
8. Runtime rerank warmup calls `/v1/rerank` in local mode and DashScope endpoint in DashScope mode.
9. Runtime rerank warmup makes no outbound request for disabled or unsupported providers.
10. Targeted pytest commands pass.

## File Map

- Create: `fastQA/tests/test_rerank_service.py`
  - Unit coverage for provider-specific rerank request/response behavior.
- Modify: `fastQA/app/modules/generation_pipeline/rerank_service.py`
  - Add provider-specific request builders and local result parsing.
- Modify: `fastQA/app/modules/microscopic_expert.py`
  - Make rerank API key and base URL resolution provider-aware.
- Modify: `fastQA/app/core/runtime.py`
  - Make rerank hot-lane warmup provider-aware and avoid DashScope fallback credentials in local mode.
- Modify: `fastQA/tests/test_microscopic_expert.py`
  - Add local env-resolution coverage.
- Modify: `fastQA/tests/test_stage2_hot_connection_runtime.py`
  - Add local hot-lane warmup coverage and update existing warmup expectations if helper signatures change.
- Optional Modify: `fastQA/docs/rerank-local-migration-plan.md`
  - Correct the earlier "only one file" statement after implementation is complete.

## Task 1: Add Rerank Service Tests

**Files:**
- Create: `fastQA/tests/test_rerank_service.py`
- Reference: `fastQA/app/modules/generation_pipeline/rerank_service.py`

- [ ] **Step 1: Write failing tests for local provider success**

Create `fastQA/tests/test_rerank_service.py` with a fake requests-compatible module:

```python
from __future__ import annotations

from app.modules.generation_pipeline.rerank_service import rerank_documents


class _Response:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.raise_called = False

    def raise_for_status(self) -> None:
        self.raise_called = True

    def json(self) -> dict:
        return self._payload


class _Requests:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def post(self, endpoint, headers, json, timeout):
        self.calls.append({"endpoint": endpoint, "headers": headers, "json": json, "timeout": timeout})
        return _Response(self.payload)


def test_local_rerank_posts_openai_compatible_payload_without_auth():
    req = _Requests({"results": [{"index": 1, "relevance_score": 0.92}, {"index": 0, "relevance_score": 0.51}]})

    result = rerank_documents(
        query="lfp query",
        documents=["doc-a", "doc-b"],
        metadatas=[{"id": "a"}, {"id": "b"}],
        top_n=2,
        provider="local",
        api_key="",
        model="qwen3-vl-rerank",
        base_url="http://localhost:8084",
        timeout_seconds=7.0,
        requests_module=req,
    )

    assert req.calls == [
        {
            "endpoint": "http://localhost:8084/v1/rerank",
            "headers": {"Content-Type": "application/json"},
            "json": {
                "model": "qwen3-vl-rerank",
                "query": "lfp query",
                "documents": ["doc-a", "doc-b"],
                "top_n": 2,
            },
            "timeout": 7.0,
        }
    ]
    assert result == {
        "documents": ["doc-b", "doc-a"],
        "metadatas": [{"id": "b"}, {"id": "a"}],
        "rerank_scores": [0.92, 0.51],
        "fallback": False,
        "fallback_reason": "",
        "provider": "local",
    }
```

- [ ] **Step 2: Add tests for local auth, top_n capping, invalid indexes, and request failure**

Add these cases to the same file:

```python
def test_local_rerank_adds_auth_only_when_api_key_is_present():
    req = _Requests({"results": [{"index": 0, "relevance_score": 0.8}]})

    rerank_documents(
        query="q",
        documents=["doc"],
        provider="local",
        api_key="local-key",
        model="m",
        base_url="http://reranker",
        requests_module=req,
    )

    assert req.calls[0]["headers"]["Authorization"] == "Bearer local-key"


def test_local_rerank_caps_returned_rows_to_top_n_and_skips_invalid_indexes():
    req = _Requests(
        {
            "results": [
                {"index": 99, "relevance_score": 1.0},
                {"index": 2, "relevance_score": 0.9},
                {"index": 1, "relevance_score": 0.8},
                {"index": 0, "relevance_score": 0.7},
            ]
        }
    )

    result = rerank_documents(
        query="q",
        documents=["doc-a", "doc-b", "doc-c"],
        top_n=2,
        provider="local",
        model="m",
        base_url="http://reranker",
        requests_module=req,
    )

    assert result["documents"] == ["doc-c", "doc-b"]
    assert result["rerank_scores"] == [0.9, 0.8]


def test_local_rerank_falls_back_when_request_fails():
    class _FailingRequests:
        def post(self, endpoint, headers, json, timeout):
            raise RuntimeError("boom")

    result = rerank_documents(
        query="q",
        documents=["doc-a", "doc-b"],
        top_n=1,
        provider="local",
        model="m",
        base_url="http://reranker",
        requests_module=_FailingRequests(),
    )

    assert result["documents"] == ["doc-a"]
    assert result["fallback"] is True
    assert result["fallback_reason"] == "request_failed"
    assert result["provider"] == "local"


def test_local_rerank_falls_back_when_response_has_no_valid_rows():
    req = _Requests({"results": [{"index": 99, "relevance_score": 1.0}]})

    result = rerank_documents(
        query="q",
        documents=["doc-a"],
        top_n=1,
        provider="local",
        model="m",
        base_url="http://reranker",
        requests_module=req,
    )

    assert result["documents"] == ["doc-a"]
    assert result["fallback"] is True
    assert result["fallback_reason"] == "empty_rerank_result"


def test_local_rerank_falls_back_when_json_parsing_fails():
    class _BadResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            raise ValueError("bad json")

    class _RequestsWithBadJson:
        def post(self, endpoint, headers, json, timeout):
            return _BadResponse()

    result = rerank_documents(
        query="q",
        documents=["doc-a"],
        top_n=1,
        provider="local",
        model="m",
        base_url="http://reranker",
        requests_module=_RequestsWithBadJson(),
    )

    assert result["fallback"] is True
    assert result["fallback_reason"] == "request_failed"
```

- [ ] **Step 3: Add regression tests for DashScope and unsupported providers**

Add one DashScope success test that asserts the existing endpoint and payload remain unchanged, one unsupported provider test, and one disabled provider test:

```python
def test_dashscope_rerank_request_shape_is_preserved():
    req = _Requests({"output": {"results": [{"index": 0, "relevance_score": 0.77}]}})

    result = rerank_documents(
        query="q",
        documents=["doc"],
        top_n=1,
        provider="dashscope",
        api_key="dash-key",
        model="dash-model",
        base_url="https://dashscope.example",
        requests_module=req,
    )

    assert req.calls[0]["endpoint"] == "https://dashscope.example/api/v1/services/rerank/text-rerank/text-rerank"
    assert req.calls[0]["headers"]["Authorization"] == "Bearer dash-key"
    assert req.calls[0]["json"] == {
        "model": "dash-model",
        "input": {"query": "q", "documents": ["doc"]},
        "parameters": {"return_documents": False, "top_n": 1},
    }
    assert result["fallback"] is False


def test_unknown_rerank_provider_falls_back_without_http_call():
    req = _Requests({"results": []})

    result = rerank_documents(
        query="q",
        documents=["doc"],
        provider="bogus",
        requests_module=req,
    )

    assert req.calls == []
    assert result["fallback"] is True
    assert result["fallback_reason"] == "provider_unsupported"
    assert result["provider"] == "bogus"


def test_disabled_rerank_provider_falls_back_without_http_call():
    req = _Requests({"results": []})

    result = rerank_documents(
        query="q",
        documents=["doc"],
        provider="disabled",
        requests_module=req,
    )

    assert req.calls == []
    assert result["fallback"] is True
    assert result["fallback_reason"] == "provider_disabled"
    assert result["provider"] == "disabled"
```

- [ ] **Step 4: Run tests to verify they fail**

Run:

```bash
cd fastQA && pytest tests/test_rerank_service.py -q
```

Expected: local provider tests fail because current code returns `provider_unsupported`; DashScope regression should pass or fail only if the new test needs adjustment to match exact current behavior.

- [ ] **Step 5: Keep the failing tests uncommitted until Task 2 passes**

Do not commit a known-red repository state. Leave `fastQA/tests/test_rerank_service.py` in the worktree and continue directly to Task 2.

## Task 2: Implement Local Provider in Rerank Service

**Files:**
- Modify: `fastQA/app/modules/generation_pipeline/rerank_service.py`
- Test: `fastQA/tests/test_rerank_service.py`

- [ ] **Step 1: Add small provider helpers**

Add helpers near `_fallback_result()`:

```python
def _clamp_top_n(top_n: int, document_count: int) -> int:
    return min(max(int(top_n), 1), document_count)


def _build_headers(*, api_key: str, include_auth: bool) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if include_auth and api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers
```

- [ ] **Step 2: Replace the single-provider guard with provider dispatch**

After `req is None`, allow only `dashscope` and `local`:

```python
if provider_norm not in {"dashscope", "local"}:
    return _fallback_result(
        documents=documents,
        metadatas=metadatas,
        top_n=top_n,
        reason="provider_unsupported",
        provider=provider_norm,
    )

if provider_norm == "dashscope" and not api_key:
    return _fallback_result(
        documents=documents,
        metadatas=metadatas,
        top_n=top_n,
        reason="api_key_missing",
        provider=provider_norm,
    )
```

- [ ] **Step 3: Build provider-specific endpoint, payload, headers, and result list**

Before the `try`, build common docs/metas and clamped `requested_top_n`:

```python
docs_to_rerank = list(documents)
metas_to_rerank = list(metadatas or [])
requested_top_n = _clamp_top_n(top_n, len(docs_to_rerank))

if provider_norm == "local":
    endpoint = str(base_url or "http://localhost:8084").rstrip("/") + "/v1/rerank"
    payload = {
        "model": model,
        "query": query,
        "documents": docs_to_rerank,
        "top_n": requested_top_n,
    }
    headers = _build_headers(api_key=api_key, include_auth=bool(api_key))
else:
    endpoint = str(base_url or "https://dashscope.aliyuncs.com").rstrip("/") + "/api/v1/services/rerank/text-rerank/text-rerank"
    payload = {
        "model": model,
        "input": {"query": query, "documents": docs_to_rerank},
        "parameters": {"return_documents": False, "top_n": requested_top_n},
    }
    headers = _build_headers(api_key=api_key, include_auth=True)
```

Inside the `try`, parse the provider-specific result list:

```python
data = response.json() if hasattr(response, "json") else {}
items = data.get("results", []) if provider_norm == "local" else data.get("output", {}).get("results", [])
```

- [ ] **Step 4: Cap ranked output to requested top_n**

When appending valid rows, stop once `requested_top_n` documents are collected:

```python
for item in items:
    idx = int(item.get("index", -1))
    if idx < 0 or idx >= len(docs_to_rerank):
        continue
    ranked_docs.append(docs_to_rerank[idx])
    if idx < len(metas_to_rerank):
        ranked_metas.append(metas_to_rerank[idx])
    ranked_scores.append(float(item.get("relevance_score", 0.0)))
    if len(ranked_docs) >= requested_top_n:
        break
```

- [ ] **Step 5: Run rerank service tests**

Run:

```bash
cd fastQA && pytest tests/test_rerank_service.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit tests and implementation**

```bash
git add fastQA/app/modules/generation_pipeline/rerank_service.py fastQA/tests/test_rerank_service.py
git commit -m "feat: add fastqa local rerank provider"
```

## Task 3: Make Microscopic Expert Rerank Env Resolution Provider-Aware

**Files:**
- Modify: `fastQA/app/modules/microscopic_expert.py`
- Modify: `fastQA/tests/test_microscopic_expert.py`

- [ ] **Step 1: Add failing env-resolution test**

Append to `fastQA/tests/test_microscopic_expert.py`:

```python
def test_microscopic_expert_local_rerank_does_not_inherit_dashscope_key_or_url(monkeypatch):
    calls = {}

    def _fake_rerank_documents(**kwargs):
        calls.update(kwargs)
        return {"documents": ["doc"], "metadatas": [], "rerank_scores": [0.9], "fallback": False, "provider": "local"}

    monkeypatch.setattr("app.modules.microscopic_expert.rerank_documents_impl", _fake_rerank_documents)
    monkeypatch.setenv("QA_RETRIEVAL_RERANK_PROVIDER", "local")
    monkeypatch.delenv("QA_RETRIEVAL_RERANK_API_KEY", raising=False)
    monkeypatch.delenv("QA_RETRIEVAL_RERANK_BASE_URL", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")

    expert = MicroscopicSemanticExpert.__new__(MicroscopicSemanticExpert)
    expert.rerank_session_pool = None

    result = expert._rerank_documents(query="lfp", documents=["doc1"], metadatas=[], top_n=1)

    assert result["provider"] == "local"
    assert calls["provider"] == "local"
    assert calls["api_key"] == ""
    assert calls["base_url"] == "http://localhost:8084"
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd fastQA && pytest tests/test_microscopic_expert.py::test_microscopic_expert_local_rerank_does_not_inherit_dashscope_key_or_url -q
```

Expected: FAIL because current code falls back to `DASHSCOPE_API_KEY` and DashScope base URL.

- [ ] **Step 3: Implement provider-aware resolution**

In `MicroscopicSemanticExpert._rerank_documents()`, normalize provider before resolving the key and URL:

```python
provider = str(os.getenv("QA_RETRIEVAL_RERANK_PROVIDER", "dashscope") or "dashscope").strip()
provider_norm = provider.lower()
raw_rerank_api_key = str(os.getenv("QA_RETRIEVAL_RERANK_API_KEY", "") or "").strip()
if provider_norm == "local":
    api_key = raw_rerank_api_key
    default_base_url = "http://localhost:8084"
else:
    api_key = raw_rerank_api_key or str(os.getenv("DASHSCOPE_API_KEY", "") or "").strip()
    default_base_url = "https://dashscope.aliyuncs.com"
base_url = str(os.getenv("QA_RETRIEVAL_RERANK_BASE_URL", default_base_url) or default_base_url).strip()
```

Keep passing `provider=provider` to `rerank_documents_impl()` so downstream metadata preserves the configured provider spelling behavior already normalized by the service.

- [ ] **Step 4: Run microscopic expert targeted tests**

Run:

```bash
cd fastQA && pytest tests/test_microscopic_expert.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit env wiring**

```bash
git add fastQA/app/modules/microscopic_expert.py fastQA/tests/test_microscopic_expert.py
git commit -m "fix: make fastqa local rerank env resolution provider aware"
```

## Task 4: Make Runtime Rerank Warmup Provider-Aware

**Files:**
- Modify: `fastQA/app/core/runtime.py`
- Modify: `fastQA/tests/test_stage2_hot_connection_runtime.py`

- [ ] **Step 1: Add failing local warmup test**

Append to `fastQA/tests/test_stage2_hot_connection_runtime.py`:

```python
def test_bootstrap_generation_runtime_uses_local_rerank_warmup_protocol(monkeypatch):
    runtime = SimpleNamespace(
        settings=SimpleNamespace(
            generation_runtime_enabled=True,
            llm_http_shared_pool_enabled=False,
            stage2_rerank_hot_pool_enabled=True,
            stage2_rerank_hot_lane_count=1,
            stage2_rerank_warmup_enabled=True,
            stage2_rerank_warm_interval_seconds=300,
            stage2_rerank_warm_timeout_seconds=420.0,
            stage2_bootstrap_warm_max_parallel=1,
            stage2_bootstrap_warm_jitter_seconds=0,
            stage2_warm_jitter_seconds=0,
            stage2_lane_degraded_after_seconds=900,
        ),
        generation_runtime=None,
        generation_runtime_ready=False,
        component_status={},
        health_flags={},
        shared_llm_http_pool=None,
    )
    calls: dict[str, object] = {}

    monkeypatch.setenv("QA_RETRIEVAL_RERANK_PROVIDER", "local")
    monkeypatch.delenv("QA_RETRIEVAL_RERANK_API_KEY", raising=False)
    monkeypatch.delenv("QA_RETRIEVAL_RERANK_BASE_URL", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setenv("QA_RETRIEVAL_RERANK_MODEL", "rerank-model")
    monkeypatch.setattr(
        "app.modules.generation_pipeline.runtime_bootstrap.resolve_generation_runtime_inputs",
        lambda **kwargs: SimpleNamespace(api_key="chat-key", base_url="https://example.com/v1", model="m"),
    )

    def _fake_rerank_pool(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(snapshot=lambda: {})

    monkeypatch.setattr("app.core.runtime.RerankSessionPool", _fake_rerank_pool)
    monkeypatch.setattr(
        "app.modules.generation_pipeline.generation_driven_rag_facade.GenerationDrivenRAG",
        lambda **kwargs: SimpleNamespace(model="m", base_url="https://example.com/v1", literature_expert=SimpleNamespace(available=True)),
    )

    bootstrap_generation_runtime(runtime)

    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"ok": True})
    fake_session = SimpleNamespace(
        post=lambda endpoint, headers, json, timeout: (
            calls.update({"endpoint": endpoint, "headers": headers, "payload": json, "timeout": timeout}) or response
        )
    )
    calls["warm_lane_fn"](lane=SimpleNamespace(session=fake_session), timeout_seconds=12.0, reason="bootstrap")

    assert calls["endpoint"] == "http://localhost:8084/v1/rerank"
    assert calls["headers"] == {"Content-Type": "application/json"}
    assert calls["payload"] == {
        "model": "rerank-model",
        "query": "warm",
        "documents": ["warm doc one", "warm doc two"],
        "top_n": 1,
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
cd fastQA && pytest tests/test_stage2_hot_connection_runtime.py::test_bootstrap_generation_runtime_uses_local_rerank_warmup_protocol -q
```

Expected: FAIL because warmup currently posts DashScope payload to DashScope path and includes a bearer key.

- [ ] **Step 3: Add failing disabled/unsupported warmup tests**

Append to `fastQA/tests/test_stage2_hot_connection_runtime.py`:

```python
def test_stage2_rerank_warmup_skips_disabled_provider_without_http_call():
    from app.core.runtime import _warm_stage2_rerank_lane

    class _Session:
        def post(self, endpoint, headers, json, timeout):
            raise AssertionError("disabled provider should not call upstream")

    _warm_stage2_rerank_lane(
        lane=SimpleNamespace(session=_Session()),
        provider="disabled",
        api_key="",
        model="m",
        base_url="http://reranker",
        timeout_seconds=1.0,
        reason="test",
    )


def test_stage2_rerank_warmup_skips_unsupported_provider_without_http_call():
    from app.core.runtime import _warm_stage2_rerank_lane

    class _Session:
        def post(self, endpoint, headers, json, timeout):
            raise AssertionError("unsupported provider should not call upstream")

    _warm_stage2_rerank_lane(
        lane=SimpleNamespace(session=_Session()),
        provider="bogus",
        api_key="",
        model="m",
        base_url="http://reranker",
        timeout_seconds=1.0,
        reason="test",
    )
```

- [ ] **Step 4: Run the disabled/unsupported tests to verify they fail**

Run:

```bash
cd fastQA && pytest \
  tests/test_stage2_hot_connection_runtime.py::test_stage2_rerank_warmup_skips_disabled_provider_without_http_call \
  tests/test_stage2_hot_connection_runtime.py::test_stage2_rerank_warmup_skips_unsupported_provider_without_http_call \
  -q
```

Expected: FAIL because `_warm_stage2_rerank_lane()` does not yet accept `provider`.

- [ ] **Step 5: Extend warmup helper signature**

Change `_warm_stage2_rerank_lane()` to accept `provider` and choose endpoint/payload/headers:

```python
def _warm_stage2_rerank_lane(
    *,
    lane: Any,
    provider: str,
    api_key: str,
    model: str,
    base_url: str,
    timeout_seconds: float,
    reason: str = "manual",
) -> None:
    _ = reason
    provider_norm = str(provider or "dashscope").strip().lower()
    session = getattr(lane, "session", None)
    if session is None:
        raise RuntimeError("rerank lane session unavailable")
    if provider_norm in {"none", "off", "disabled"}:
        return
    if provider_norm not in {"dashscope", "local"}:
        return
    if provider_norm == "local":
        endpoint = str(base_url or "http://localhost:8084").rstrip("/") + "/v1/rerank"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": model,
            "query": "warm",
            "documents": ["warm doc one", "warm doc two"],
            "top_n": 1,
        }
    else:
        endpoint = str(base_url or "https://dashscope.aliyuncs.com").rstrip("/")
        endpoint = endpoint + "/api/v1/services/rerank/text-rerank/text-rerank"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "input": {"query": "warm", "documents": ["warm doc one", "warm doc two"]},
            "parameters": {"return_documents": False, "top_n": 1},
        }
    response = session.post(endpoint, headers=headers, json=payload, timeout=timeout_seconds)
    response.raise_for_status()
    parse_json = getattr(response, "json", None)
    if callable(parse_json):
        parse_json()
```

Unknown and disabled provider handling intentionally returns without making an upstream HTTP call. The current pool treats a no-exception warmup function return as success, which is acceptable here because disabled/unsupported providers should not depend on upstream readiness.

- [ ] **Step 6: Resolve provider-aware runtime env**

In `bootstrap_generation_runtime()`, before creating `RerankSessionPool`, resolve:

```python
rerank_provider = str(os.getenv("QA_RETRIEVAL_RERANK_PROVIDER", "dashscope") or "dashscope").strip()
rerank_provider_norm = rerank_provider.lower()
raw_rerank_api_key = str(os.getenv("QA_RETRIEVAL_RERANK_API_KEY", "") or "").strip()
if rerank_provider_norm == "local":
    rerank_api_key = raw_rerank_api_key
    rerank_default_base_url = "http://localhost:8084"
else:
    rerank_api_key = (
        raw_rerank_api_key
        or str(os.getenv("DASHSCOPE_API_KEY", "") or "").strip()
        or str(resolved.api_key or "").strip()
    )
    rerank_default_base_url = "https://dashscope.aliyuncs.com"
rerank_base_url = str(os.getenv("QA_RETRIEVAL_RERANK_BASE_URL", rerank_default_base_url) or rerank_default_base_url).strip()
```

Pass `provider=rerank_provider` into `_warm_stage2_rerank_lane()`.

- [ ] **Step 7: Update existing dedicated-key warmup test if needed**

The existing `test_bootstrap_generation_runtime_uses_dedicated_rerank_api_key_for_warmup` should continue asserting DashScope endpoint and `Bearer rerank-key`. If env leakage from the new local test affects it, add explicit:

```python
monkeypatch.setenv("QA_RETRIEVAL_RERANK_PROVIDER", "dashscope")
```

- [ ] **Step 8: Run runtime hot connection tests**

Run:

```bash
cd fastQA && pytest tests/test_stage2_hot_connection_runtime.py -q
```

Expected: PASS.

- [ ] **Step 9: Commit runtime warmup changes**

```bash
git add fastQA/app/core/runtime.py fastQA/tests/test_stage2_hot_connection_runtime.py
git commit -m "fix: align fastqa rerank warmup with local provider"
```

## Task 5: Update Migration Documentation

**Files:**
- Modify: `fastQA/docs/rerank-local-migration-plan.md`

- [ ] **Step 1: Correct the changed-file scope**

Update section `4.1` from "only one file" to:

```markdown
核心改动覆盖：

- `fastQA/app/modules/generation_pipeline/rerank_service.py`
- `fastQA/app/modules/microscopic_expert.py`
- `fastQA/app/core/runtime.py`
- `fastQA/tests/test_rerank_service.py`
- `fastQA/tests/test_microscopic_expert.py`
- `fastQA/tests/test_stage2_hot_connection_runtime.py`
```

- [ ] **Step 2: Correct local auth and base URL notes**

Document that local provider:

```markdown
- `QA_RETRIEVAL_RERANK_API_KEY` 为空时不发送 `Authorization`。
- `QA_RETRIEVAL_RERANK_PROVIDER=local` 且未设置 `QA_RETRIEVAL_RERANK_BASE_URL` 时，默认 `http://localhost:8084`。
- local provider 不回退到 `DASHSCOPE_API_KEY`。
```

- [ ] **Step 3: Add hot-pool warmup note**

Add a runtime note:

```markdown
如果 `FASTQA_STAGE2_RERANK_HOT_POOL_ENABLED=1`，热池 warmup 必须使用和当前 provider 一致的协议。否则 local provider 主调用可用，但 lane warmup 仍会向 DashScope endpoint 发送请求并导致热池降级。
```

- [ ] **Step 4: Commit docs update**

```bash
git add fastQA/docs/rerank-local-migration-plan.md
git commit -m "docs: refine fastqa local rerank migration scope"
```

## Task 6: Final Verification

**Files:**
- All files touched in Tasks 1-5

- [ ] **Step 1: Run targeted pytest suite**

Run:

```bash
cd fastQA && pytest tests/test_rerank_service.py tests/test_microscopic_expert.py tests/test_stage2_hot_connection_runtime.py -q
```

Expected: PASS.

- [ ] **Step 2: Run a no-network smoke test for local provider request shape**

Use the unit tests as the no-network contract. Do not call `localhost:8084` from CI/unit tests.

- [ ] **Step 3: Optionally verify against a running local reranker**

Only when the developer has the local service running:

```bash
QA_RETRIEVAL_RERANK_PROVIDER=local \
QA_RETRIEVAL_RERANK_BASE_URL=http://localhost:8084 \
QA_RETRIEVAL_RERANK_MODEL=qwen3-vl-rerank \
pytest -q fastQA/tests/test_rerank_service.py
```

Expected: Unit tests still do not require the live service. Manual integration testing should be done through the FastQA service path after unit tests pass.

- [ ] **Step 4: Inspect git diff**

Run:

```bash
git status --short
git log --oneline --stat -5
```

Expected: Only planned files are changed in the latest task commits, aside from unrelated pre-existing worktree files.

- [ ] **Step 5: Final commit if Task 5 was skipped earlier**

If docs were not committed in Task 5, commit them now:

```bash
git add fastQA/docs/rerank-local-migration-plan.md
git commit -m "docs: refine fastqa local rerank migration scope"
```

## Rollout Notes

1. Configure local mode:

```bash
QA_RETRIEVAL_RERANK_PROVIDER=local
QA_RETRIEVAL_RERANK_BASE_URL=http://localhost:8084
QA_RETRIEVAL_RERANK_MODEL=qwen3-vl-rerank
QA_RETRIEVAL_RERANK_API_KEY=
```

2. If local service does not support `/v1/rerank`, either add that endpoint to the local service or create a separate future provider for the current custom `/rerank` contract.
3. If hot-lane warmup causes local startup pressure during rollout, temporarily set `FASTQA_STAGE2_RERANK_WARMUP_ENABLED=0`; do not disable local provider behavior in `rerank_service.py`.
4. Watch Stage2 logs for `rerank_provider=local`, `rerank_applied=1`, and absence of sustained `rerank_fallback=1`.

## Out Of Scope

1. Implementing the custom local `/rerank` object protocol.
2. Changing rerank model selection beyond existing `QA_RETRIEVAL_RERANK_MODEL`.
3. Adding live integration tests that require `localhost:8084`.
4. Changing Stage2 retrieval strategy or candidate generation.
5. Changing frontend behavior or gateway routing.
