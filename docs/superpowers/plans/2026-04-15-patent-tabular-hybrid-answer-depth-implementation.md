# Patent Tabular and Hybrid QA Answer Depth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `patent` 后端 `tabular_qa` 与 `hybrid_qa` 答案过短问题，让表格问答走真实 LLM 路径、让混合问答优先走统一 synthesis 路径，并保留兼容的 fallback、streaming、cache 和 API contract。

**Architecture:** 保留现有 `PatentExecutor -> dispatch_patent_file_route -> pdf/tabular/hybrid` 主结构，不重写文件路由。先给 `PatentTabularService` 接入 app-owned OpenAI-compatible answer client，并抽出面向问答/合成的表格上下文 builder；再新增 `PatentHybridSynthesisClient` 和内部 `_hybrid_internal_state` 传递载体，让 file-only hybrid 与 executor merge hybrid 共用同一套最终 synthesis 服务，规则 synthesis 只保留为降级路径。所有对外 API 仍只暴露 compact metadata，完整 `*_synthesis_context` 只在服务内部流转。

**Tech Stack:** Python 3, FastAPI, httpx, pytest, patent executor/file-route services, existing process-local upstream pool, OpenAI-compatible chat completions

---

## Source Documents

- Spec: `docs/superpowers/specs/2026-04-15-patent-tabular-hybrid-answer-depth-design.md`
- Relevant production files:
  - `patent/server/patent/tabular_service.py`
  - `patent/server/patent/file_routes.py`
  - `patent/server/patent/executor.py`
  - `patent/server/patent/cache_keys.py`
  - `patent/server/patent/pdf_service.py`
  - `patent/server_fastapi/app.py`
- Existing regression suites:
  - `patent/tests/test_patent_executor.py`
  - `patent/tests/test_patent_file_routes.py`
  - `patent/tests/test_patent_pdf_contract.py`
  - `patent/tests/fastapi_contract/test_ask_contract.py`
  - `patent/tests/fastapi_contract/test_health_contract.py`
  - `patent/tests/test_runtime_controls.py`

## Hard Rules

1. `tabular_qa` 修复后必须区分 `llm`、`fallback`、`unavailable` 后端状态，不能继续把所有路径都伪装成同一种 `answer_mode`。
2. `PatentHybridSynthesisClient` 的唯一 owner 是 app bootstrap，通过 `PatentExecutor` 显式下传；不能在 `file_routes.py` 或 `executor.py` 里隐式 new 一套私有 client。
3. 完整 `pdf_synthesis_context`、`table_synthesis_context`、`kb_synthesis_context` 只能走内部 `_hybrid_internal_state`；不能直接进入最终 FastAPI 响应的 `metadata`。
4. `_hybrid_internal_state` 如果用于 file-route cache，也必须在最终 API payload 返回前剥离；不能让前端或 contract test 看到内部字段。
5. file-route cache fingerprint 必须纳入 tabular/hybrid 新 runtime signature、prompt version 和 context budget，避免命中旧短答案缓存。
6. 第一阶段不做真实 token-level streaming；允许 buffer 完整答案后再按现有 `emit_text_chunks()` 输出，但不能破坏 preview/final stream 顺序。
7. 每个 task 都先写红灯测试，再做最小实现，再跑目标测试，再做 subagent review，直到 pass 才能 commit。
8. 如果测试受沙箱限制，实施阶段必须申请提权；未提权成功前不得声称验证完成。

## Per-Task Review Gate

每个 task 完成后都必须执行同一条流程：

1. 写红灯测试
2. 运行目标测试并确认失败
3. 实现最小改动
4. 重跑目标测试并确认转绿
5. 发给 reviewer subagent 做 review
6. 根据 reviewer 反馈修正并重跑目标测试
7. reviewer pass 后再 commit

## File Map

### Tabular Answer Path

- Modify: `patent/server/patent/tabular_service.py`
  Purpose: 新增 `PatentTabularAnswerClient`、`answer_client` 注入位、`answer_backend` metadata、`table_answer_context` / `table_synthesis_context` 使用点。
- Create: `patent/server/patent/tabular_context.py`
  Purpose: 统一构建 compact evidence、answer context、synthesis context 和基础统计摘要，避免 `tabular_service.py` 继续膨胀。
- Create: `patent/tests/test_patent_tabular_service.py`
  Purpose: 锁定 tabular answer client ownership、service 调用顺序、fallback 降级和 metadata。
- Create: `patent/tests/test_patent_tabular_context.py`
  Purpose: 锁定 context bundle 的内容、预算和统计摘要行为。

### Hybrid Synthesis Path

- Create: `patent/server/patent/hybrid_synthesis.py`
  Purpose: 定义 `PatentHybridSynthesisClient`、internal synthesis contract builder、prompt builder、可复用的 runtime signature 和最小调用接口。
- Modify: `patent/server/patent/pdf_service.py`
  Purpose: 暴露 richer `pdf_synthesis_context`，并保持对外 `pdf_evidence_context` 仍是 compact preview。
- Modify: `patent/server/patent/file_routes.py`
  Purpose: file-only hybrid wiring、内部 `_hybrid_internal_state` 生成、规则 fallback 调整、cache runtime signature 扩展。
- Modify: `patent/server/patent/executor.py`
  Purpose: merge 阶段复用同一个 synthesis service、消费并剥离 `_hybrid_internal_state`。
- Modify: `patent/server/patent/cache_keys.py`
  Purpose: file-route cache fingerprint 纳入新的 runtime signature 和版本字段。
- Create: `patent/tests/test_patent_hybrid_synthesis.py`
  Purpose: 锁定 hybrid prompt、internal contract shape、service ownership、internal state/public metadata 边界。
- Modify: `patent/tests/test_patent_pdf_contract.py`
  Purpose: 锁定 `pdf_synthesis_context` 与 `pdf_evidence_context` 的 internal/public 边界。

### Bootstrap And Contracts

- Modify: `patent/server_fastapi/app.py`
  Purpose: app bootstrap 注入 `PatentTabularService(answer_client=...)` 和 `PatentHybridSynthesisClient`。
- Modify: `patent/config.shared.env.example`
  Purpose: 新增 tabular/hybrid 相关 env vars 示例。
- Modify: `patent/tests/test_patent_executor.py`
  Purpose: executor 层 tabular/hybrid regression、merge 逻辑、internal state 剥离、stream 顺序。
- Modify: `patent/tests/test_patent_file_routes.py`
  Purpose: file-route 层 tabular/hybrid regression、fallback、runtime signature、cache behavior。
- Modify: `patent/tests/test_patent_pdf_contract.py`
  Purpose: PDF 输出 contract 在增加 synthesis context 后仍保持对外 compact。
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`
  Purpose: FastAPI ask contract 下 tabular/hybrid 最终 answer、metadata、streaming 行为不回归。
- Modify: `patent/tests/fastapi_contract/test_health_contract.py`
  Purpose: app-owned patent clients 的 degraded status、bootstrap fallback 和 lifespan cleanup 不回归。

## Lock Decisions

1. `PatentTabularAnswerClient` 第一阶段跟随 `PatentPdfAnswerClient` 的结构风格，先放在 `tabular_service.py` 中，不抽 shared base class。
2. `tabular_context.py` 只做上下文组织与统计摘要，不负责调用 LLM。
3. `PatentHybridSynthesisClient` 放在新文件 `patent/server/patent/hybrid_synthesis.py`，避免继续扩大 `file_routes.py`。
4. `PatentExecutor` 新增 `hybrid_synthesis_service=` 注入位，并向 `dispatch_patent_file_route()` 与 `_merge_file_and_kb_results()` 显式传递。
5. `metadata.synthesis_contract` 继续保留 compact/public 字段；完整 `*_synthesis_context` 只出现在 `_hybrid_internal_state`。
6. file-only hybrid 不需要把完整 synthesis context 返回到对外 payload；只有 include-KB 路径允许暂时携带 `_hybrid_internal_state` 供 merge 阶段消费。
7. `answer_backend`、`hybrid_synthesis_backend` 是新 metadata 字段；旧 `answer_mode` 暂时保留，避免扩大 API 破坏面。
8. tabular 和 hybrid 的新 env vars 统一遵循现有 `PATENT_OPENAI_*` 命名风格。

## Task Order

1. Tabular answer client 与 app wiring
2. Tabular context bundle 与 prompt/input 扩展
3. Hybrid synthesis module 与 prompt contract
4. Hybrid wiring、internal state、cache 和 merge 逻辑
5. End-to-end contract hardening、streaming/caching regression 和 env example
6. Overall review、final acceptance 和 review-driven polish

---

### Task 1: Tabular Answer Client And App Wiring

**Files:**
- Modify: `patent/server/patent/tabular_service.py`
- Modify: `patent/server_fastapi/app.py`
- Create: `patent/tests/test_patent_tabular_service.py`
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `patent/tests/test_patent_file_routes.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`
- Modify: `patent/tests/fastapi_contract/test_health_contract.py`

**Testing Requirement:**
- 锁死 `PatentTabularAnswerClient` 的 injected-client ownership。
- 锁死 `PatentTabularService` 优先使用 `answer_question_fn`，其次 `answer_client`，最后 fallback。
- 锁死已配置 answer client 失败时会降级返回 fallback answer，并标记 `answer_backend=unavailable`。
- 锁死生产 bootstrap 会把 app-owned tabular service 注入给 `PatentExecutor`。
- 锁死 tabular client bootstrap 失败不会让 app 启动崩掉，health contract 会暴露 degraded 状态。
- 锁死 app-owned tabular client 会在 lifespan shutdown 或 bootstrap 失败回滚时被正确关闭。
- 锁死 metadata 中 `answer_backend` 对 `llm`、`fallback`、`unavailable` 的区分。
- 必跑命令：
  - `bash patent/scripts/test.sh tests/test_patent_tabular_service.py tests/test_patent_executor.py tests/test_patent_file_routes.py tests/fastapi_contract/test_ask_contract.py tests/fastapi_contract/test_health_contract.py -q`

- [ ] **Step 1: 写红灯测试**

新增或补充测试，至少覆盖：

```python
def test_tabular_answer_client_from_env_uses_injected_http_client_without_taking_ownership():
    shared = _FakeHttpClient()
    client = PatentTabularAnswerClient(
        api_key="key",
        base_url="https://example.com",
        model="tabular-model",
        http_client=shared,
    )
    client.close()
    assert shared.closed is False


def test_tabular_service_uses_answer_client_before_fallback():
    service = PatentTabularService(answer_client=_FakeTabularClient(answer_text="LLM answer"))
    result = service.execute(...)
    assert result["metadata"]["answer_backend"] == "llm"
    assert "LLM answer" in result["answer_text"]


def test_tabular_service_marks_fallback_backend_when_client_missing():
    service = PatentTabularService(answer_client=None, answer_question_fn=None)
    result = service.execute(...)
    assert result["metadata"]["answer_backend"] == "fallback"


def test_tabular_service_marks_unavailable_when_client_errors_and_returns_fallback_answer():
    service = PatentTabularService(answer_client=_ExplodingTabularClient(), answer_question_fn=None)
    result = service.execute(...)
    assert result["metadata"]["answer_backend"] == "unavailable"
    assert result["answer_text"]


def test_create_app_injects_app_owned_tabular_service_when_env_available():
    app = create_app_for_test(...)
    assert isinstance(app.state.ask_service._patent_executor._tabular_service, PatentTabularService)


def test_create_app_degrades_when_tabular_client_bootstrap_fails_and_health_reports_it():
    app = create_app_for_test(force_tabular_client_bootstrap_error=True, ...)
    assert app.state.component_status["patent_tabular_answer_client"]["status"] == "degraded"
```

同时补 FastAPI contract 与 health-contract case，证明 HTTP 返回中能看到 `answer_backend`，且 final answer 不是空字符串；app shutdown 或 bootstrap 回滚时会关闭 app-owned tabular client。

- [ ] **Step 2: 运行 Task 1 红灯测试**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_service.py tests/test_patent_executor.py tests/test_patent_file_routes.py tests/fastapi_contract/test_ask_contract.py tests/fastapi_contract/test_health_contract.py -q
```

Expected:
- FAIL
- 失败点集中在 `PatentTabularAnswerClient` 不存在、`answer_backend` 还未暴露、`unavailable` 路径未定义、以及 app bootstrap/health contract 还没有覆盖 tabular client lifecycle

- [ ] **Step 3: 最小实现 tabular answer client 与 bootstrap**

在 `patent/server/patent/tabular_service.py` 里新增：

```python
class PatentTabularAnswerClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 30.0,
        top_p: float = 0.95,
        max_tokens: int = 2500,
        http_client: Any | None = None,
    ) -> None:
        ...

    @classmethod
    def from_env(cls, *, http_client: Any | None = None) -> "PatentTabularAnswerClient | None":
        ...

    def answer(... ) -> str:
        ...

    def runtime_signature(self) -> dict[str, Any]:
        ...

    def close(self) -> None:
        ...
```

并修改：

```python
class PatentTabularService:
    def __init__(..., answer_client: PatentTabularAnswerClient | Any | None = None, ...):
        self._answer_client = answer_client

    def _build_answer(...):
        if callable(self._answer_question_fn):
            ...
        elif self._answer_client is not None:
            ...
        else:
            ...
```

要求：

1. `answer_question_fn` 仍优先于 `answer_client`，不能破坏现有测试注入路径。
2. 如果 client 是外部注入 shared client，则 `close()` 不应关闭 shared HTTP client。
3. 如果 answer client 已配置但调用失败，service 必须返回 fallback answer，并把 `answer_backend` 标记为 `unavailable`。
4. `app.py` bootstrap 新增 tabular client 构造，并把 `PatentTabularService(answer_client=tabular_answer_client)` 注入到 `PatentExecutor`。
5. tabular client bootstrap 失败时，app 仍要以 degraded 状态启动，而不是抛错中断。
6. app-owned tabular client 必须在 lifespan shutdown、bootstrap 失败回滚或重复初始化清理时被正确关闭。
7. metadata 新增 `answer_backend`，值为 `llm`、`fallback`、`unavailable`。
8. `PatentTabularAnswerClient.runtime_signature()` 必须暴露 model、max_tokens、top_p 等关键参数，供 file-route cache fingerprint 使用。

- [ ] **Step 4: 重跑 Task 1 目标测试**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_service.py tests/test_patent_executor.py tests/test_patent_file_routes.py tests/fastapi_contract/test_ask_contract.py tests/fastapi_contract/test_health_contract.py -q
```

Expected:
- PASS
- tabular answer client wiring、ownership、bootstrap 和 metadata backend 被锁住

- [ ] **Step 5: 发起 Task 1 review，修到 pass**

review 重点：

1. `answer_question_fn` / `answer_client` / fallback 三层优先级
2. shared HTTP client ownership
3. `app.py` bootstrap 的 degraded-mode 语义与 health-contract 暴露
4. app-owned tabular client 的 shutdown / rollback cleanup
5. `answer_backend` 是否足够区分生产路径

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/tabular_service.py patent/server_fastapi/app.py patent/tests/test_patent_tabular_service.py patent/tests/test_patent_executor.py patent/tests/test_patent_file_routes.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/fastapi_contract/test_health_contract.py
git commit -m "feat(patent): add tabular answer client wiring"
```

---

### Task 2: Tabular Context Bundle And Prompt Input Expansion

**Files:**
- Create: `patent/server/patent/tabular_context.py`
- Modify: `patent/server/patent/tabular_service.py`
- Create: `patent/tests/test_patent_tabular_context.py`
- Modify: `patent/tests/test_patent_tabular_service.py`
- Modify: `patent/tests/test_patent_executor.py`

**Testing Requirement:**
- 锁死 compact evidence、answer context、synthesis context 的区分。
- 锁死统计摘要、top rows、planner/executor 结果都能进入 answer/synthesis context。
- 锁死 summary 和定向问题的 context budget 不一样但都不泄露到对外 API。
- 锁死 `PatentTabularService` 会通过 private/internal transport 暴露 `table_synthesis_context` 给 hybrid 组装使用，但不会出现在对外 metadata。
- 锁死 `_build_patent_tabular_prompt()` 对 summary 与普通问答分别输出文献总结结构和四段式结构。
- 必跑命令：
  - `bash patent/scripts/test.sh tests/test_patent_tabular_context.py tests/test_patent_tabular_service.py tests/test_patent_executor.py -q`

- [ ] **Step 1: 写红灯测试**

新增测试，至少覆盖：

```python
def test_build_tabular_context_bundle_returns_compact_and_rich_contexts():
    bundle = build_tabular_context_bundle(...)
    assert bundle["compact_evidence_context"]
    assert bundle["answer_context"]
    assert bundle["synthesis_context"]
    assert len(bundle["answer_context"]) >= len(bundle["compact_evidence_context"])
    assert len(bundle["synthesis_context"]) >= len(bundle["compact_evidence_context"])


def test_build_tabular_context_bundle_includes_summary_stats_and_top_rows():
    bundle = build_tabular_context_bundle(...)
    assert "统计摘要" in bundle["answer_context"]
    assert "代表性行" in bundle["answer_context"]


def test_tabular_service_keeps_compact_context_public_and_rich_context_internal():
    result = service.execute(...)
    assert "table_evidence_context" in result["metadata"]
    assert "统计摘要" not in str(result["metadata"]["table_evidence_context"])


def test_tabular_service_exposes_private_synthesis_context_for_hybrid_consumers():
    result = service.execute(...)
    assert result["_table_synthesis_context"]
    assert "table_synthesis_context" not in result["metadata"]


def test_tabular_prompt_requires_summary_shape_for_summary_questions():
    prompt = _build_patent_tabular_prompt(question="请总结", ...)
    assert "## 研究目的和背景" in prompt
    assert "## 局限性" in prompt


def test_tabular_prompt_requires_four_block_shape_for_normal_questions():
    prompt = _build_patent_tabular_prompt(question="哪个指标更高", ...)
    assert "## 结论" in prompt
    assert "## 证据" in prompt
    assert "## 对比" in prompt
    assert "## 限制" in prompt
```

还要补一个 summary 问题 case，证明传给 answer client 的 `table_text` 不再只是旧 `execution_context`，而是 richer answer context。

- [ ] **Step 2: 运行 Task 2 红灯测试**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_context.py tests/test_patent_tabular_service.py tests/test_patent_executor.py -q
```

Expected:
- FAIL
- 失败点集中在缺少 `build_tabular_context_bundle()`、缺少统计摘要、以及 service 仍只使用旧 `execution_context`

- [ ] **Step 3: 实现表格上下文 builder**

创建 `patent/server/patent/tabular_context.py`，至少提供：

```python
def build_tabular_context_bundle(
    *,
    question: str,
    workbook: dict[str, Any],
    plan: dict[str, Any],
    result: dict[str, Any],
    file_name: str,
    compact_limit: int,
    answer_limit: int,
    synthesis_limit: int,
) -> dict[str, str]:
    return {
        "compact_evidence_context": ...,
        "answer_context": ...,
        "synthesis_context": ...,
    }
```

要求：

1. `compact_evidence_context` 继续兼容当前 metadata 用途。
2. `answer_context` 用于 `PatentTabularAnswerClient`。
3. `synthesis_context` 用于 hybrid synthesis。
4. summary 问题可以包含更多 top rows；lookup/aggregate/compare 问题优先保留命中行和聚合结果。
5. 基础统计摘要只用现有 workbook/result 数据，不引入新依赖。

并修改 `PatentTabularService.execute()`：

1. 使用 context bundle。
2. 更新 `_build_patent_tabular_prompt()`，让 summary 问题输出文献总结结构、普通问答输出 `结论 / 证据 / 对比 / 限制`，并要求覆盖关键字段、统计和样例差异。
3. `answer_client` 调用时传 `answer_context`。
4. tabular prompt version 保持 code constant，并通过 service/client runtime signature 暴露给 cache fingerprint。
5. metadata 保留 `table_evidence_context`，新增 `table_answer_context_chars`、`table_synthesis_context_chars`。
6. 通过 transient private field 暴露 `_table_synthesis_context` 或等价内部字段，供 file-route 在 hybrid 组装时立即转写进 `_hybrid_internal_state["synthesis_contract"]["table_synthesis_context"]`。
7. pure `tabular_qa` 返回前必须剥离 `_table_synthesis_context`，不能让它成为第二套长期存在的 internal payload contract。
8. 对外结果不暴露完整 rich context 文本。

- [ ] **Step 4: 重跑 Task 2 目标测试**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_context.py tests/test_patent_tabular_service.py tests/test_patent_executor.py -q
```

Expected:
- PASS
- rich context 与 public compact context 边界被锁住

- [ ] **Step 5: 发起 Task 2 review，修到 pass**

review 重点：

1. `tabular_context.py` 是否职责单一
2. summary / lookup / aggregate / compare 的上下文选择是否可实现
3. `_build_patent_tabular_prompt()` 的 summary/four-block 输出合同是否落地
4. 对外 metadata 是否仍保持 compact

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/tabular_context.py patent/server/patent/tabular_service.py patent/tests/test_patent_tabular_context.py patent/tests/test_patent_tabular_service.py patent/tests/test_patent_executor.py
git commit -m "feat(patent): expand tabular answer context"
```

---

### Task 3: Hybrid Synthesis Module And Prompt Contract

**Files:**
- Create: `patent/server/patent/hybrid_synthesis.py`
- Modify: `patent/server/patent/pdf_service.py`
- Create: `patent/tests/test_patent_hybrid_synthesis.py`
- Modify: `patent/tests/test_patent_pdf_contract.py`
- Modify: `patent/config.shared.env.example`

**Testing Requirement:**
- 锁死 hybrid 普通问答与 summary prompt 的输出合同。
- 锁死 internal hybrid synthesis contract 的字段形状和来源元信息。
- 锁死 `PatentHybridSynthesisClient` 的 injected-client ownership 与 env 读取逻辑。
- 锁死 `pdf_synthesis_context` 比 public `pdf_evidence_context` 更完整，但不会进入对外 payload。
- 锁死 synthesis fallback 入口与规则 synthesis 的降级边界。
- 必跑命令：
  - `bash patent/scripts/test.sh tests/test_patent_hybrid_synthesis.py tests/test_patent_pdf_contract.py -q`

- [ ] **Step 1: 写红灯测试**

新增测试，至少覆盖：

```python
def test_hybrid_synthesis_prompt_requires_file_precedence_and_source_boundaries():
    prompt = build_patent_hybrid_synthesis_prompt(...)
    assert "文件证据优先" in prompt
    assert "知识库只能作为补充验证" in prompt


def test_hybrid_summary_prompt_requires_five_section_summary_shape():
    prompt = build_patent_hybrid_synthesis_prompt(question="请总结", ...)
    assert "## 研究目的和背景" in prompt
    assert "## 局限性" in prompt


def test_hybrid_synthesis_client_does_not_close_injected_http_client():
    shared = _FakeHttpClient()
    client = PatentHybridSynthesisClient(..., http_client=shared)
    client.close()
    assert shared.closed is False


def test_hybrid_synthesis_prompt_rejects_raw_execution_markers():
    prompt = build_patent_hybrid_synthesis_prompt(...)
    assert "匹配工作表:" not in prompt


def test_build_hybrid_synthesis_contract_includes_internal_contexts_and_source_metadata():
    contract = build_patent_hybrid_synthesis_contract(...)
    assert contract["pdf_synthesis_context"]
    assert contract["table_synthesis_context"]
    assert contract["synthesis_prompt_version"]
    assert contract["available_sources"] == ["pdf", "table"]
    assert contract["source_answer_modes"]["pdf"]


def test_build_hybrid_synthesis_contract_uses_richer_pdf_context_than_public_preview():
    pdf_result = PatentPdfService(...).execute(...)
    contract = build_patent_hybrid_synthesis_contract(pdf_result=pdf_result, ...)
    assert contract["pdf_synthesis_context"]
    assert len(contract["pdf_synthesis_context"]) > len(pdf_result["metadata"]["pdf_evidence_context"])
```

同时把新的 env vars 写进 `patent/config.shared.env.example` 的测试快照或文本断言中：

1. `PATENT_TABULAR_MAX_TOKENS`
2. `PATENT_TABULAR_TOP_P`
3. `PATENT_TABULAR_MAX_CONTEXT_CHARS`
4. `PATENT_HYBRID_TABLE_CONTEXT_CHARS`
5. `PATENT_HYBRID_MAX_TOKENS`

- [ ] **Step 2: 运行 Task 3 红灯测试**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_hybrid_synthesis.py tests/test_patent_pdf_contract.py -q
```

Expected:
- FAIL
- 因为 `hybrid_synthesis.py`、internal contract builder、`pdf_synthesis_context` 暴露位和 env example 还不存在

- [ ] **Step 3: 实现 hybrid synthesis 模块**

创建 `patent/server/patent/hybrid_synthesis.py`，至少提供：

```python
class PatentHybridSynthesisClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 30.0,
        top_p: float = 0.95,
        max_tokens: int = 3000,
        http_client: Any | None = None,
    ) -> None:
        ...

    @classmethod
    def from_env(cls, *, http_client: Any | None = None) -> "PatentHybridSynthesisClient | None":
        ...

    def answer(self, *, synthesis_contract: dict[str, Any]) -> str:
        ...

    def runtime_signature(self) -> dict[str, Any]:
        ...
```

以及：

```python
def build_patent_hybrid_synthesis_prompt(*, synthesis_contract: dict[str, Any]) -> str:
    ...
```

以及：

```python
def build_patent_hybrid_synthesis_contract(...) -> dict[str, Any]:
    ...
```

要求：

1. 普通问答 prompt 输出 `结论 / 证据 / 对比 / 限制`。
2. summary prompt 输出五段文献总结结构加 `注*`。
3. prompt 中要明确禁止把 KB 写成 PDF/表格事实。
4. `runtime_signature()` 要包含 code-constant prompt version 和关键预算配置。
5. internal contract 必须显式包含 `pdf_synthesis_context`、`table_synthesis_context`、`kb_synthesis_context`、`available_sources`、`source_answer_modes`、`synthesis_prompt_version`。
6. `pdf_service.py` 需要新增或暴露 richer `pdf_synthesis_context` 生成逻辑，供 hybrid synthesis 使用；public `pdf_evidence_context` 继续保持 compact。
7. `config.shared.env.example` 要补齐新 env vars 示例。

- [ ] **Step 4: 重跑 Task 3 目标测试**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_hybrid_synthesis.py tests/test_patent_pdf_contract.py -q
```

Expected:
- PASS
- hybrid prompt contract、internal contract shape、PDF internal/public context 边界和 env surface 被锁住

- [ ] **Step 5: 发起 Task 3 review，修到 pass**

review 重点：

1. prompt 是否足够约束文件优先和 KB 边界
2. internal contract shape 是否足够统一 file-only 与 KB-merge 两条路径
3. env var surface 是否最小化
4. `runtime_signature()` 是否适合进入 file-route cache fingerprint

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/hybrid_synthesis.py patent/server/patent/pdf_service.py patent/config.shared.env.example patent/tests/test_patent_hybrid_synthesis.py patent/tests/test_patent_pdf_contract.py
git commit -m "feat(patent): add hybrid synthesis client"
```

---

### Task 4: Hybrid Wiring, Internal State, Cache And Merge Logic

**Files:**
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/server/patent/file_routes.py`
- Modify: `patent/server/patent/executor.py`
- Modify: `patent/server/patent/cache_keys.py`
- Modify: `patent/server_fastapi/app.py`
- Modify: `patent/tests/test_patent_file_routes.py`
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `patent/tests/test_patent_hybrid_synthesis.py`
- Modify: `patent/tests/test_patent_pdf_contract.py`
- Modify: `patent/tests/fastapi_contract/test_health_contract.py`

**Testing Requirement:**
- 锁死 file-only hybrid 与 KB merge hybrid 都复用同一个 injected synthesis service。
- 锁死 hybrid file branch 会生成 richer `pdf_synthesis_context`，而不是继续复用 compact preview context。
- 锁死 file-only 与 KB-merge 共用同一份 internal synthesis contract shape：`pdf/table/kb_synthesis_context`、`available_sources`、`source_answer_modes`、`synthesis_prompt_version`。
- 锁死 `_hybrid_internal_state` 会在 merge 阶段被消费并在最终返回前剥离。
- 锁死 file-route cache fingerprint 因 hybrid runtime signature/prompt version 改变而变化。
- 锁死 file-route cache fingerprint 也会因 tabular backend、tabular runtime signature、tabular context budget 改变而变化。
- 锁死 synthesis client 失败时仍回退到规则 synthesis。
- 锁死 hybrid fallback 会继续过滤 shell placeholder 和 raw execution markers。
- 锁死无 usable evidence 时返回明确不可用说明，并标记 hybrid step error / unavailable-style degraded result。
- 锁死 hybrid client bootstrap 失败不会让 app 启动崩掉，health contract 会暴露 degraded 状态。
- 锁死 app-owned hybrid client 会在 lifespan shutdown 或 bootstrap 失败回滚时被正确关闭。
- 必跑命令：
  - `bash patent/scripts/test.sh tests/test_patent_hybrid_synthesis.py tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py tests/test_patent_executor.py tests/fastapi_contract/test_health_contract.py -q`

- [ ] **Step 1: 写红灯测试**

新增或补充测试，至少覆盖：

```python
def test_file_only_hybrid_uses_injected_hybrid_synthesis_service():
    service = _FakeHybridSynthesisService(answer_text="final answer")
    result = dispatch_patent_file_route(..., hybrid_synthesis_service=service)
    assert service.calls == 1
    assert result["answer_text"] == "final answer"


def test_executor_kb_merge_uses_same_hybrid_synthesis_service_and_strips_internal_state():
    executor = PatentExecutor(hybrid_synthesis_service=_FakeHybridSynthesisService(...), ...)
    result = executor.execute(...)
    assert "_hybrid_internal_state" not in result
    assert "_hybrid_internal_state" not in result["metadata"]
    assert result["metadata"]["synthesis_contract"]["available_sources"]


def test_file_route_cache_fingerprint_changes_when_hybrid_prompt_version_changes():
    left = build_file_route_cache_fingerprint(..., runtime_signature={"hybrid_prompt_version": "v1"})
    right = build_file_route_cache_fingerprint(..., runtime_signature={"hybrid_prompt_version": "v2"})
    assert left != right


def test_file_route_cache_fingerprint_changes_when_hybrid_backend_changes():
    left = build_file_route_cache_fingerprint(..., runtime_signature={"hybrid_synthesis_backend": "fallback_rules"})
    right = build_file_route_cache_fingerprint(..., runtime_signature={"hybrid_synthesis_backend": "llm"})
    assert left != right


def test_file_route_cache_fingerprint_changes_when_tabular_backend_changes():
    left = build_file_route_cache_fingerprint(..., runtime_signature={"tabular_answer_backend": "fallback"})
    right = build_file_route_cache_fingerprint(..., runtime_signature={"tabular_answer_backend": "llm"})
    assert left != right


def test_file_route_cache_fingerprint_changes_when_tabular_context_budget_changes():
    left = build_file_route_cache_fingerprint(..., runtime_signature={"tabular_max_context_chars": 6000})
    right = build_file_route_cache_fingerprint(..., runtime_signature={"tabular_max_context_chars": 12000})
    assert left != right


def test_file_route_cache_fingerprint_changes_when_tabular_prompt_version_changes():
    left = build_file_route_cache_fingerprint(..., runtime_signature={"tabular_prompt_version": "v1"})
    right = build_file_route_cache_fingerprint(..., runtime_signature={"tabular_prompt_version": "v2"})
    assert left != right


def test_file_route_cache_fingerprint_changes_when_tabular_runtime_signature_changes():
    left = build_file_route_cache_fingerprint(..., runtime_signature={"tabular_runtime_signature": {"model": "m1", "top_p": 0.8}})
    right = build_file_route_cache_fingerprint(..., runtime_signature={"tabular_runtime_signature": {"model": "m2", "top_p": 0.8}})
    assert left != right


def test_hybrid_internal_contract_contains_required_synthesis_context_fields():
    result = dispatch_patent_file_route(..., hybrid_synthesis_service=_FakeHybridSynthesisService(), include_kb=True)
    state = result["_hybrid_internal_state"]
    assert state["synthesis_contract"]["pdf_synthesis_context"]
    assert state["synthesis_contract"]["table_synthesis_context"]
    assert "kb_synthesis_context" in state["synthesis_contract"]
    assert state["synthesis_contract"]["available_sources"]
    assert state["synthesis_contract"]["source_answer_modes"]


def test_hybrid_synthesis_failure_falls_back_to_rule_synthesis():
    result = dispatch_patent_file_route(..., hybrid_synthesis_service=_ExplodingHybridSynthesisService())
    assert result["answer_text"]
    assert result["metadata"]["hybrid_synthesis_backend"] == "fallback_rules"


def test_hybrid_fallback_filters_shell_placeholders_and_raw_markers():
    result = dispatch_patent_file_route(..., hybrid_synthesis_service=_ExplodingHybridSynthesisService())
    assert "$(" not in result["answer_text"]
    assert "source_scope=" not in result["answer_text"]


def test_hybrid_without_usable_evidence_returns_explicit_unavailable_and_step_error():
    result = dispatch_patent_file_route(..., hybrid_synthesis_service=_ExplodingHybridSynthesisService(), no_usable_evidence=True)
    assert result["answer_text"]
    assert has_hybrid_step_error(result["steps"]) is True


def test_create_app_degrades_when_hybrid_client_bootstrap_fails_and_health_reports_it():
    app = create_app_for_test(force_hybrid_client_bootstrap_error=True, ...)
    assert app.state.component_status["patent_hybrid_synthesis_client"]["status"] == "degraded"
```

同时加一个 include-KB case，证明 file route 可以携带 `_hybrid_internal_state`，而最终 FastAPI/executor 返回不会泄漏它；并补 health-contract case，证明 app shutdown 或 bootstrap 回滚时会关闭 app-owned hybrid client。

- [ ] **Step 2: 运行 Task 4 红灯测试**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_hybrid_synthesis.py tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py tests/test_patent_executor.py tests/fastapi_contract/test_health_contract.py -q
```

Expected:
- FAIL
- 失败点集中在 `dispatch_patent_file_route()` / `_build_hybrid_result()` / `_merge_file_and_kb_results()` 还没有 synthesis service 注入位、没有 richer `pdf_synthesis_context`、也没有完整 internal contract/cached runtime signature

- [ ] **Step 3: 实现 hybrid wiring 与 internal/public 边界**

要求把以下签名改清楚：

```python
def dispatch_patent_file_route(..., hybrid_synthesis_service: Any | None = None, ...) -> dict[str, Any]:
    ...


def _build_hybrid_result(..., hybrid_synthesis_service: Any | None = None, ...) -> dict[str, Any]:
    ...


class PatentExecutor:
    def __init__(..., hybrid_synthesis_service: Any | None = None, ...) -> None:
        ...

    def _merge_file_and_kb_results(..., hybrid_synthesis_service: Any | None = None) -> dict[str, Any]:
        ...
```

实现要求：

1. `app.py` bootstrap 创建 `PatentHybridSynthesisClient` 并注入 `PatentExecutor`。
2. PDF branch 必须生成 richer `pdf_synthesis_context`，且明显区别于对外 `pdf_evidence_context` 的 compact preview。
3. file-only hybrid 优先直接调用 synthesis service，不需要对外暴露 `_hybrid_internal_state`。
4. include-KB hybrid 在 file route 结果里允许暂存 `_hybrid_internal_state` 供 merge 使用。
5. `_hybrid_internal_state["synthesis_contract"]` 必须统一包含 `pdf_synthesis_context`、`table_synthesis_context`、`kb_synthesis_context`、`available_sources`、`source_answer_modes`、`synthesis_prompt_version`。
6. `table_synthesis_context` 必须来自 `PatentTabularService` 的 transient private field，例如 `_table_synthesis_context`，并在 file-route 边界立即归并进 `_hybrid_internal_state["synthesis_contract"]["table_synthesis_context"]`；不能从 public `table_evidence_context` 重新截短拼接。
7. `_merge_file_and_kb_results()` 消费 `_hybrid_internal_state` 后必须剥离它。
8. `metadata.synthesis_contract` 仍只保留 compact/public 字段。
9. `hybrid_synthesis_backend` 标记 `llm` 或 `fallback_rules`。
10. 规则 fallback 必须继续过滤 shell placeholder 和 raw execution markers，不把中间标签带到最终答案。
11. 无 usable evidence 时必须返回明确不可用说明，并标记 hybrid step error / unavailable-style degraded result。
12. `build_file_route_cache_fingerprint()` 的 `runtime_signature` 必须纳入 tabular/hybrid 新字段，并区分 tabular `fallback` / `llm`、tabular prompt version、tabular runtime signature、hybrid `fallback_rules` / `llm`、hybrid prompt version 和关键 context budget。
13. hybrid client bootstrap 失败时，app 仍要以 degraded 状态启动，而不是抛错中断。
14. app-owned hybrid client 必须在 lifespan shutdown、bootstrap 失败回滚或重复初始化清理时被正确关闭。

- [ ] **Step 4: 重跑 Task 4 目标测试**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_hybrid_synthesis.py tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py tests/test_patent_executor.py tests/fastapi_contract/test_health_contract.py -q
```

Expected:
- PASS
- synthesis service wiring、PDF richer context、internal contract shape、internal state 剥离和 cache fingerprint 行为被锁住

- [ ] **Step 5: 发起 Task 4 review，修到 pass**

review 重点：

1. `PatentHybridSynthesisClient` owner 是否只有 app bootstrap 一个
2. `pdf_synthesis_context` / `pdf_evidence_context` 边界是否足够清晰
3. `table_synthesis_context` 是否在 file-route 边界被立即归并进 `_hybrid_internal_state`，没有形成第二套权威 internal carrier
4. `_hybrid_internal_state` 生命周期是否足够清晰
5. app-owned hybrid client 的 shutdown / rollback cleanup
6. fallback/no-evidence degraded path 是否仍符合 spec
7. cache runtime signature 是否真的避免旧缓存复用，尤其是 tabular runtime signature / prompt version 分流与 hybrid `fallback_rules` / `llm` 分流

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/pdf_service.py patent/server/patent/file_routes.py patent/server/patent/executor.py patent/server/patent/cache_keys.py patent/server_fastapi/app.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py patent/tests/test_patent_hybrid_synthesis.py patent/tests/fastapi_contract/test_health_contract.py
git commit -m "feat(patent): wire hybrid synthesis through executor and file routes"
```

---

### Task 5: End-To-End Contract Hardening, Streaming And Regression Coverage

**Files:**
- Modify: `patent/server/patent/tabular_service.py`
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/server/patent/file_routes.py`
- Modify: `patent/server/patent/executor.py`
- Modify: `patent/tests/test_patent_tabular_service.py`
- Modify: `patent/tests/test_patent_pdf_contract.py`
- Modify: `patent/tests/test_patent_file_routes.py`
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`
- Modify: `patent/tests/fastapi_contract/test_health_contract.py`

**Testing Requirement:**
- 锁死 tabular/hybrid 最终 answer 变长但对外 contract 不膨胀。
- 锁死 PDF public/compact contract 不会因为 richer synthesis context 回归。
- 锁死 file-only hybrid preview -> final 顺序不变。
- 锁死 include-KB hybrid final 只在 merge 后输出，且最终 payload 不泄漏内部 state。
- 锁死 fallback/unavailable 路径仍可解释。
- 锁死 hybrid fallback 对 shell placeholder / raw markers 的过滤不会回归。
- 锁死无 usable evidence 时仍返回明确不可用说明，并保留可解释 step error。
- 锁死 health contract 对 patent owned clients 的状态暴露和 cleanup 行为不回归。
- 锁死 hybrid public metadata 仍保留 `hybrid_synthesis_prompt_version` 与 `hybrid_synthesis_context_chars`。
- 锁死 tabular streamed chunks 拼接后仍等于 `answer_text`。
- 必跑命令：
  - `bash patent/scripts/test.sh tests/test_patent_tabular_service.py tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py tests/fastapi_contract/test_health_contract.py -q`

- [ ] **Step 1: 写红灯回归测试**

新增或补充测试，至少覆盖：

```python
def test_tabular_fastapi_response_exposes_backend_but_not_rich_context():
    body = ask_client(...)
    assert body["metadata"]["answer_backend"] in {"llm", "fallback", "unavailable"}
    assert "table_answer_context" not in body["metadata"]
    assert "table_synthesis_context" not in body["metadata"]


def test_hybrid_final_answer_contains_multiple_file_evidence_points():
    result = executor.execute(...)
    assert result["answer_text"].count("- ") >= 4


def test_hybrid_preview_streams_arrive_before_final_and_internal_state_is_hidden():
    streamed = run_structured_stream(...)
    assert preview_events_before_final(streamed) is True
    assert "_hybrid_internal_state" not in final_payload


def test_hybrid_fallback_path_still_returns_explained_answer_when_synthesis_client_fails():
    result = executor.execute(...)
    assert result["metadata"]["hybrid_synthesis_backend"] == "fallback_rules"
    assert result["answer_text"]


def test_hybrid_final_answer_filters_shell_placeholders_and_no_usable_evidence_is_explicit():
    result = executor.execute(...)
    assert "$(" not in result["answer_text"]
    empty_result = executor.execute(...)
    assert has_hybrid_step_error(empty_result["steps"]) is True


def test_hybrid_public_metadata_keeps_prompt_version_and_context_chars():
    result = executor.execute(...)
    assert result["metadata"]["hybrid_synthesis_prompt_version"]
    assert result["metadata"]["hybrid_synthesis_context_chars"] > 0


def test_tabular_streamed_chunks_reassemble_to_answer_text():
    streamed = run_structured_stream(...)
    assert join_text_chunks(streamed) == extract_final_answer_text(streamed)
```

补充一个 ask-contract case，证明 `hybrid_qa` 的最终 HTTP 响应中没有 `_hybrid_internal_state`，但仍保留 compact `synthesis_contract` 和 compact evidence context。

- [ ] **Step 2: 运行 Task 5 红灯测试**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_service.py tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py tests/fastapi_contract/test_health_contract.py -q
```

Expected:
- FAIL
- 失败点集中在最终 contract 还没收口、stream 顺序或内部 state 剥离仍有遗漏

- [ ] **Step 3: 做最小收口实现**

实现要求：

1. 收口所有对外 metadata，只保留 compact/public 字段。
2. 保留 `hybrid_synthesis_prompt_version` 与 `hybrid_synthesis_context_chars` 这两个 public metadata 字段。
3. 保证 `emit_text_chunks()` 的最终输出仍与 `answer_text` 一致。
4. file-only hybrid 维持 preview stream，再 emit final hybrid answer。
5. include-KB hybrid 仍在 merge 后发 final answer。
6. tabular/hybrid fallback 路径都要保留明确 backend 标记。

- [ ] **Step 4: 运行 Task 5 目标测试**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_service.py tests/test_patent_pdf_contract.py tests/test_patent_file_routes.py tests/test_patent_executor.py tests/fastapi_contract/test_ask_contract.py tests/fastapi_contract/test_health_contract.py -q
```

Expected:
- PASS
- tabular/hybrid 对外 contract、streaming、fallback 行为全部稳定

- [ ] **Step 5: 跑本次改动的整体验证命令**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_service.py tests/test_patent_tabular_context.py tests/test_patent_hybrid_synthesis.py tests/test_patent_pdf_contract.py tests/test_patent_executor.py tests/test_patent_file_routes.py tests/fastapi_contract/test_ask_contract.py tests/fastapi_contract/test_health_contract.py -q
```

Expected:
- PASS
- 本计划新增/修改的核心回归全部转绿

- [ ] **Step 6: 发起 Task 5 review，修到 pass**

review 重点：

1. 对外 contract 是否真的没有泄漏 rich context / internal state
2. preview/final stream 顺序是否仍符合现有前端假设
3. tabular stream 拼接是否仍与 `answer_text` 一致
4. `hybrid_synthesis_prompt_version` / `hybrid_synthesis_context_chars` 是否仍保留在 public metadata
5. health contract 中 owned clients 的状态与 cleanup 是否仍可解释
6. fallback backend 标记是否足够支持后续排查

- [ ] **Step 7: Commit**

```bash
git add patent/server/patent/tabular_service.py patent/server/patent/pdf_service.py patent/server/patent/file_routes.py patent/server/patent/executor.py patent/tests/test_patent_tabular_service.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/fastapi_contract/test_health_contract.py
git commit -m "test(patent): harden tabular and hybrid answer depth contracts"
```

---

### Task 6: Overall Review, Final Acceptance And Review-Driven Polish

**Files:**
- Modify as needed: `patent/server/patent/tabular_service.py`
- Modify as needed: `patent/server/patent/tabular_context.py`
- Modify as needed: `patent/server/patent/hybrid_synthesis.py`
- Modify as needed: `patent/server/patent/pdf_service.py`
- Modify as needed: `patent/server/patent/file_routes.py`
- Modify as needed: `patent/server/patent/executor.py`
- Modify as needed: `patent/server/patent/cache_keys.py`
- Modify as needed: `patent/server_fastapi/app.py`
- Modify as needed: `patent/config.shared.env.example`
- Modify as needed: `patent/tests/test_patent_tabular_service.py`
- Modify as needed: `patent/tests/test_patent_tabular_context.py`
- Modify as needed: `patent/tests/test_patent_hybrid_synthesis.py`
- Modify as needed: `patent/tests/test_patent_pdf_contract.py`
- Modify as needed: `patent/tests/test_patent_file_routes.py`
- Modify as needed: `patent/tests/test_patent_executor.py`
- Modify as needed: `patent/tests/fastapi_contract/test_ask_contract.py`
- Modify as needed: `patent/tests/fastapi_contract/test_health_contract.py`

**Testing Requirement:**
- 必须以整体验证命令作为 review baseline，不能只跑单点测试就宣称完成。
- 必须发起一次针对完整实现 diff 的整体 reviewer 审阅，而不是只看某一个 task。
- 如果整体验证或 reviewer 找到问题，必须先补聚焦红灯测试，再做最小修复，再重跑整体验证。
- 如果测试受沙箱限制，必须申请提权；未提权成功前不得声称最终验收完成。

- [ ] **Step 1: 跑整体验证，建立最终 review baseline**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_service.py tests/test_patent_tabular_context.py tests/test_patent_hybrid_synthesis.py tests/test_patent_pdf_contract.py tests/test_patent_executor.py tests/test_patent_file_routes.py tests/fastapi_contract/test_ask_contract.py tests/fastapi_contract/test_health_contract.py -q
```

Expected:
- PASS
- 当前实现已有完整回归基线；如果失败，记录失败点并进入 Step 3

- [ ] **Step 2: 发起整体 reviewer 审阅完整 diff**

review 输入必须覆盖：

1. 当前实现 diff
2. `docs/superpowers/specs/2026-04-15-patent-tabular-hybrid-answer-depth-design.md`
3. `docs/superpowers/plans/2026-04-15-patent-tabular-hybrid-answer-depth-implementation.md`

review 重点：

1. tabular/hybrid 是否真的解决“答案过短”而不是只改 metadata
2. compact/public vs rich/internal 边界是否稳定
3. cache、streaming、fallback、bootstrap ownership 是否存在跨 task 回归
4. health contract 是否仍正确暴露 degraded status 和 owned-resource cleanup

- [ ] **Step 3: 如果整体 review 发现问题，先补红灯测试**

要求：

1. 针对 reviewer 或整体验证暴露的问题，补一条最小、可复现的失败测试。
2. 优先补到最贴近问题的测试文件，避免把跨层问题都塞进 contract test。
3. 没有发现问题则记录 no-op，直接进入 Step 5。

- [ ] **Step 4: 做最小修复并重跑聚焦测试 + 整体验证**

Run:

```bash
bash patent/scripts/test.sh tests/test_patent_tabular_service.py tests/test_patent_tabular_context.py tests/test_patent_hybrid_synthesis.py tests/test_patent_pdf_contract.py tests/test_patent_executor.py tests/test_patent_file_routes.py tests/fastapi_contract/test_ask_contract.py tests/fastapi_contract/test_health_contract.py -q
```

Expected:
- PASS
- review 发现的问题被最小修复，没有引入新的跨层回归

- [ ] **Step 5: 复审到 pass**

要求：

1. 把修复后的完整 diff 再发给 reviewer subagent 做一次 overall review。
2. reviewer 明确给出 pass / approved 后，才能进入最终提交。
3. 如果第二轮仍有阻塞问题，继续重复 Step 3 和 Step 4，直到 reviewer pass。

- [ ] **Step 6: Commit 最终 review-driven 调整**

```bash
git add patent/server/patent/tabular_service.py patent/server/patent/tabular_context.py patent/server/patent/hybrid_synthesis.py patent/server/patent/pdf_service.py patent/server/patent/file_routes.py patent/server/patent/executor.py patent/server/patent/cache_keys.py patent/server_fastapi/app.py patent/config.shared.env.example patent/tests/test_patent_tabular_service.py patent/tests/test_patent_tabular_context.py patent/tests/test_patent_hybrid_synthesis.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/fastapi_contract/test_health_contract.py
git commit -m "fix(patent): address final review findings for qa depth"
```

---

## Final Verification Checklist

- [ ] `PatentTabularAnswerClient` 使用 shared HTTP client 时不抢 ownership。
- [ ] `PatentTabularService` 生产路径优先走 LLM，而不是默认 fallback。
- [ ] `answer_backend` 在 `llm` / `fallback` / `unavailable` 三种状态下都有测试覆盖。
- [ ] 已配置但失败的 tabular answer client 会降级返回 fallback answer，并标记 `answer_backend=unavailable`。
- [ ] `tabular_context.py` 输出 compact/public 与 rich/internal 两类 context，并且只对外暴露 compact。
- [ ] `PatentTabularService` 只通过 transient private field 暴露 `table_synthesis_context` 给 file-route 组装，并在 file-route 边界归并进 `_hybrid_internal_state`；pure `tabular_qa` 返回前会剥离该字段。
- [ ] `_build_patent_tabular_prompt()` 对 summary 问题输出文献总结结构，对普通问题输出 `结论 / 证据 / 对比 / 限制`。
- [ ] `pdf_service.py` 输出 richer `pdf_synthesis_context` 供 hybrid 使用，但对外仍只暴露 compact `pdf_evidence_context`。
- [ ] `PatentHybridSynthesisClient` 只在 app bootstrap 持有一份，并通过 `PatentExecutor` 下传。
- [ ] file-only hybrid 与 include-KB hybrid 都复用同一个 synthesis service。
- [ ] hybrid internal synthesis contract 在 file-only 与 KB-merge 两条路径下都包含 `pdf/table/kb_synthesis_context`、`available_sources`、`source_answer_modes`、`synthesis_prompt_version`。
- [ ] `_hybrid_internal_state` 只在服务内部流转，不进入最终 FastAPI 响应。
- [ ] file-route cache fingerprint 因 prompt version / runtime signature 变化而变化。
- [ ] file-route cache fingerprint 能区分 tabular `fallback` / `llm` 路径，以及 tabular/hybrid runtime signature、prompt version、context budget 和 hybrid `fallback_rules` / `llm` 变化。
- [ ] app-owned tabular/hybrid clients 的 degraded status、shutdown cleanup 和 bootstrap rollback cleanup 都有 health-contract 覆盖。
- [ ] hybrid fallback 会继续过滤 shell placeholder / raw markers；无 usable evidence 时返回明确不可用说明并标记 step error。
- [ ] hybrid public metadata 保留 `hybrid_synthesis_prompt_version` 与 `hybrid_synthesis_context_chars`。
- [ ] `tabular_qa` streamed chunks 拼接后等于 `answer_text`。
- [ ] preview/final streaming 顺序不回归。
- [ ] 全部目标测试转绿；如果测试需要提权，实施阶段必须显式申请提权。
