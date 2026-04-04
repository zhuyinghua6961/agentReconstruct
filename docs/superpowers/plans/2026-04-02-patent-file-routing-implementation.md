# Patent File Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改前端和不复制文件判定逻辑的前提下，让 `gateway` 按 `requested_mode` 选择文件问答执行 backend，并让 `patent` 从 `kb_only` 扩展为可承接 `pdf_qa / tabular_qa / hybrid_qa` 的专利文件问答服务。

**Architecture:** 保持 `frontend -> gateway -> fastQA/highThinkingQA/patent` 的总体结构不变，文件意图继续只在 `gateway` 判定。第一阶段先把 `gateway` 从“一刀切 actual_mode=fast”改成 mode-aware backend 选择；第二阶段扩容 `patent` 的请求/响应合同与 route dispatch；第三阶段补齐 `patent` 的 PDF、表格、混合执行能力，并在验证通过后打开 `requested_mode=patent` 的文件流量。

**Tech Stack:** FastAPI, Python, pytest, gateway routing, fastQA file route contract, patent FastAPI service, SSE ask contract

---

## Execution Rule

`gateway` 的 mode-aware backend matrix 与 patent file gate 必须作为同一 rollout batch 落地。

允许在同一开发分支里先做 Task 1 再做 Task 2，但不允许只合并或只部署 Task 1。

这样可以避免出现 “`requested_mode=patent` 的文件流量已经被切到 `patent`，但 gate 还没挡住” 的中间态。

---

## Source Documents

- Spec: `docs/superpowers/specs/2026-04-02-patent-file-routing-design.md`
- Related current docs:
  - `docs/file_hybrid_qa_protocol_spec.md`
  - `docs/superpowers/specs/2026-03-30-gateway-qa-routing-design.md`
  - `patent/docs/2026-03-25-patent-phase1-service-design.md`
  - `patent/docs/2026-03-25-patent-phase1-service-implementation-plan.md`

## Current-State Implementation Notes

- `gateway` 已经能稳定产出文件 route contract，但当前 `turn_mode in {file_only, mixed}` 会统一压成 `actual_mode=fast`。
- `thinking` 文件问答当前就是通过 compatibility routing 进入 `fastQA`，本计划保持该行为。
- `fastQA` 当前已经拥有 `kb_qa / pdf_qa / tabular_qa / hybrid_qa` 执行能力，且 request adapter 已要求显式 route contract。
- `patent` 目前的 request / response / mode profile 仍只支持 `kb_only`，是本计划最大的协议缺口。
- 本计划不要求前端变更，也不要求 `highThinkingQA` 新增文件执行器。

## Conflict-Isolation Strategy

当前有并行开发正在修改 `patentQA` 的普通 QA 链路，因此任务顺序必须优先减少对共享入口层的早期冲击。

本计划采用以下隔离策略：

1. 前两阶段先只动 `gateway`
2. `patent` 侧先新增文件专用模块与测试，尽量不先改普通 QA 入口
3. 只有在文件专用模块成型后，才扩 `patent` 的共享 schema / ask / executor 分发层
4. `kb_qa` 现有行为保持不变，直到最后联调阶段才打开 `patent` 文件流量

这只改变实施顺序，不减少最终功能范围。

## File Structure Lock-In

### Gateway
- Modify: `gateway/app/services/route_decision.py`
- Modify: `gateway/app/routers/qa.py`
- Modify if needed for explicit backend selection metadata: `gateway/app/models/routing.py`
- Test: `gateway/tests/test_route_decision.py`
- Test: `gateway/tests/test_qa_proxy.py`

### Patent Contract And Routing
- Modify: `patent/server/schemas/request_models.py`
- Modify: `patent/server/schemas/response_models.py`
- Modify: `patent/server/schemas/authority_models.py`
- Modify: `patent/server/services/mode_profiles.py`
- Modify: `patent/server_fastapi/routers/ask.py`
- Modify: `patent/server/services/ask_service.py`
- Modify: `patent/server/services/chat_persistence.py`
- Modify: `patent/server/services/conversation_authority_client.py`
- Modify: `patent/server/patent/executor.py`
- Modify: `patent/server/patent/kb_service.py`
- Modify: `patent/server/patent/result_builder.py`
- Modify: `patent/server/services/conversation_context_builder.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`
- Test: `patent/tests/test_chat_persistence.py`
- Test: `patent/tests/test_conversation_authority_client.py`
- Test: `patent/tests/test_patent_executor.py`
- Test: `patent/tests/test_patent_kb_service.py`
- Test: `patent/tests/test_conversation_context_builder.py`

### Patent File Executors
- Create: `patent/server/patent/file_routes.py`
- Create: `patent/server/patent/pdf_service.py`
- Create: `patent/server/patent/tabular_service.py`
- Create if needed for request/result shaping: `patent/server/patent/file_models.py`
- Test: `patent/tests/test_patent_file_routes.py`
- Test: `patent/tests/test_patent_executor.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`

### Docs
- Modify: `docs/file_hybrid_qa_protocol_spec.md`
- Modify: `patent/docs/2026-03-25-patent-phase1-service-design.md`
- Modify if implementation reveals drift: `docs/superpowers/specs/2026-04-02-patent-file-routing-design.md`

---

### Task 1: Make Gateway Backend Selection Mode-Aware

**Files:**
- Modify: `gateway/app/services/route_decision.py`
- Modify: `gateway/app/routers/qa.py`
- Modify if required: `gateway/app/models/routing.py`
- Test: `gateway/tests/test_route_decision.py`
- Test: `gateway/tests/test_qa_proxy.py`

- [ ] **Step 1: Write failing gateway tests for the new backend matrix**

Cover:
- `requested_mode=fast + kb_qa -> actual_mode=fast`
- `requested_mode=thinking + kb_qa -> actual_mode=thinking`
- `requested_mode=thinking + pdf_qa/tabular_qa/hybrid_qa -> actual_mode=fast`
- `requested_mode=patent + kb_qa -> actual_mode=patent`
- `requested_mode=patent + pdf_qa/tabular_qa/hybrid_qa -> actual_mode=patent`
- router proxy:
  - `thinking + file route -> fastQA`
  - `patent + file route` selects the patent backend target in the routing matrix
  - `patent + hybrid route` also selects the patent backend target in both sync and stream proxy cases
  - forwarded payload preserves canonical `route / source_scope / turn_mode / execution_files`
  - forwarded payload also preserves `requested_mode` and `actual_mode` exactly as selected by the matrix
  - canonical file-route metadata remains identical between sync and stream payloads

- [ ] **Step 2: Run targeted gateway tests to verify failure**

Run: `pytest gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py -q`

Expected: FAIL on current `patent + file route -> actual_mode=fast` behavior.

- [ ] **Step 3: Refactor `RouteDecisionService` to separate route normalization from backend selection**

Implement:
- keep current file route normalization logic
- replace the global `file_only/mixed -> actual_mode=fast` shortcut with explicit matrix rules
- keep `thinking + file route -> fast`
- change `patent + file route -> patent`

- [ ] **Step 4: Update gateway proxy dispatch to use the new backend selection result**

Requirements:
- thinking file turns must continue proxying to `fastQA`
- plain thinking turns must continue proxying to `highThinkingQA`
- gateway must preserve both `requested_mode` and `actual_mode` in forwarded payloads
- actual backend target selection must be explicit and testable
- no frontend contract changes

- [ ] **Step 5: Re-run targeted gateway tests**

Run: `pytest gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py -q`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add gateway/app/services/route_decision.py gateway/app/routers/qa.py gateway/app/models/routing.py gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py
git commit -m "refactor: make gateway file backend selection mode aware"
```

### Task 2: Add Gateway-Side Patent File Rollout Guard

**Files:**
- Modify: `gateway/app/routers/qa.py`
- Modify if needed: `gateway/app/services/route_decision.py`
- Test: `gateway/tests/test_qa_proxy.py`
- Test: `gateway/tests/test_route_decision.py`

- [ ] **Step 1: Write failing tests for the disabled-gate behavior**

Cover:
- `requested_mode=patent + pdf_qa/tabular_qa/hybrid_qa` returns an explicit gated/disabled response while the patent file gate is off
- `requested_mode=patent + kb_qa` remains unaffected
- `requested_mode=thinking + file route` still goes to `fastQA`
- response/log payload preserves canonical `requested_mode / actual_mode / route / source_scope`
- JSON 和 SSE disabled payload 都显式暴露 `retriable=false`

- [ ] **Step 2: Run the focused gateway gate tests**

Run: `pytest gateway/tests/test_qa_proxy.py gateway/tests/test_route_decision.py -q -k patent`

Expected: FAIL because patent file traffic is not explicitly gate-controlled yet.

- [ ] **Step 3: Implement deterministic gateway-side patent file gating**

Requirements:
- gate-off behavior must be explicit and testable, not fallback-by-accident
- no change to `fast` or `thinking` file traffic
- no change to `patent kb_qa`

- [ ] **Step 4: Re-run the focused gateway gate tests**

Run: `pytest gateway/tests/test_qa_proxy.py gateway/tests/test_route_decision.py -q -k patent`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gateway/app/routers/qa.py gateway/app/services/route_decision.py gateway/tests/test_qa_proxy.py gateway/tests/test_route_decision.py
git commit -m "feat: gate patent file traffic at gateway"
```

### Task 3: Build Patent File Modules In Isolation

**Files:**
- Create: `patent/server/patent/file_models.py`
- Create: `patent/server/patent/file_routes.py`
- Create: `patent/server/patent/pdf_service.py`
- Create: `patent/server/patent/tabular_service.py`
- Create if needed for shared file payload helpers: `patent/server/patent/file_contract.py`
- Test: `patent/tests/test_patent_file_routes.py`

- [ ] **Step 1: Write failing unit tests for isolated patent file modules**

Cover:
- file contract helpers only validate and consume `gateway` 已下发的 canonical selected files / `source_scope`
- PDF route helper chooses patent PDF execution path
- tabular route helper chooses patent tabular execution path
- hybrid route helper chooses among:
  - `pdf+kb`
  - `table+kb`
  - `pdf+table`
  - `pdf+table+kb`
- file helpers do not depend on `PatentKbService` or existing ask-path state

- [ ] **Step 2: Run the isolated patent file-module tests**

Run: `pytest patent/tests/test_patent_file_routes.py -q`

Expected: FAIL because these modules do not exist yet.

- [ ] **Step 3: Implement patent-local file modules without touching shared ask flow**

Requirements:
- keep these modules self-contained
- do not wire them into `patent` ask/executor yet
- keep all dependencies patent-local
- do not re-infer file intent from question text
- do not locally recanonicalize `source_scope`; consume the gateway contract directly

- [ ] **Step 4: Re-run the isolated patent file-module tests**

Run: `pytest patent/tests/test_patent_file_routes.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/file_models.py patent/server/patent/file_routes.py patent/server/patent/pdf_service.py patent/server/patent/tabular_service.py patent/server/patent/file_contract.py patent/tests/test_patent_file_routes.py
git commit -m "feat: add isolated patent file qa modules"
```

### Task 4: Expand Patent Ask Contract Beyond `kb_only`

**Files:**
- Modify: `patent/server/schemas/request_models.py`
- Modify: `patent/server/schemas/response_models.py`
- Modify: `patent/server/schemas/authority_models.py`
- Modify: `patent/server/services/mode_profiles.py`
- Modify: `patent/server/services/conversation_context_builder.py`
- Modify: `patent/server/patent/result_builder.py`
- Modify: `patent/server/services/chat_persistence.py`
- Modify: `patent/server/services/conversation_authority_client.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`
- Test: `patent/tests/test_conversation_context_builder.py`
- Test: `patent/tests/test_chat_persistence.py`
- Test: `patent/tests/test_conversation_authority_client.py`

- [ ] **Step 1: Write failing patent contract tests for file-aware routes**

Cover:
- request parsing accepts:
  - `route=pdf_qa`, `turn_mode=file_only`, `source_scope=pdf`
  - `route=tabular_qa`, `turn_mode=file_only`, `source_scope=table`
  - `route=hybrid_qa`, `turn_mode=mixed`, `source_scope=pdf+kb`
  - `route=hybrid_qa`, `turn_mode=mixed`, `source_scope=pdf+table`
- request parsing still rejects illegal combinations:
  - `pdf_qa + source_scope=table`
  - `hybrid_qa + source_scope=kb`
  - file route without `execution_files`
- response models accept non-kb `route/source_scope` and non-empty `used_files/file_selection`
- authority payloads and durable persistence accept non-kb `route/source_scope/actual_mode`
- conversation context builder preserves canonical `source_scope` instead of collapsing to `kb`

- [ ] **Step 2: Run targeted patent contract tests to confirm failure**

Run: `pytest patent/tests/fastapi_contract/test_ask_contract.py patent/tests/test_conversation_context_builder.py patent/tests/test_chat_persistence.py patent/tests/test_conversation_authority_client.py -q`

Expected: FAIL because current patent schemas only support `kb_only`.

- [ ] **Step 3: Expand `PatentAskRequest` and parser validation**

Implement:
- supported routes: `kb_qa / pdf_qa / tabular_qa / hybrid_qa`
- supported turn modes: `kb_only / file_only / mixed`
- required route-to-turn-mode pairs:
  - `kb_qa -> kb_only`
  - `pdf_qa -> file_only`
  - `tabular_qa -> file_only`
  - `hybrid_qa -> mixed`
- required route-to-source-scope pairs:
  - `kb_qa -> kb`
  - `pdf_qa -> pdf`
  - `tabular_qa -> table`
  - `hybrid_qa -> one of {pdf+kb, table+kb, pdf+table, pdf+table+kb}`
- canonical `source_scope` validation:
  - `kb`
  - `pdf`
  - `table`
  - `pdf+kb`
  - `table+kb`
  - `pdf+table`
  - `pdf+table+kb`
- shared contract fields:
  - `kb_enabled`
  - `allow_kb_verification`
  - `selected_file_ids`
  - `primary_file_id`
  - `execution_files`
  - `file_selection`
- authority / persistence contract fields:
  - file-aware `route`
  - file-aware `source_scope`
  - unchanged `requested_mode=patent`
  - file turns keep `actual_mode=patent`

- [ ] **Step 4: Expand sync / SSE response schemas and mode profiles**

Implement:
- file-aware `route/source_scope` literals
- `used_files` and `file_selection` support
- route-aware mode profile lookup or equivalent route profile mapping for patent
- result builder / SSE metadata builder preserve the canonical `source_scope` and file usage metadata without collapsing to `kb`
- durable persistence and authority payloads preserve file-aware `route/source_scope/actual_mode`

- [ ] **Step 5: Re-run targeted patent contract tests**

Run: `pytest patent/tests/fastapi_contract/test_ask_contract.py patent/tests/test_conversation_context_builder.py patent/tests/test_chat_persistence.py patent/tests/test_conversation_authority_client.py -q`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add patent/server/schemas/request_models.py patent/server/schemas/response_models.py patent/server/schemas/authority_models.py patent/server/services/mode_profiles.py patent/server/services/conversation_context_builder.py patent/server/patent/result_builder.py patent/server/services/chat_persistence.py patent/server/services/conversation_authority_client.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/test_conversation_context_builder.py patent/tests/test_chat_persistence.py patent/tests/test_conversation_authority_client.py
git commit -m "feat: expand patent ask contract for file-aware routes"
```

### Task 5: Add Patent Route Dispatch Scaffold And Keep Rollout Gated

**Files:**
- Modify: `patent/server_fastapi/routers/ask.py`
- Modify: `patent/server/services/ask_service.py`
- Modify: `patent/server/patent/executor.py`
- Modify if needed: `patent/server/patent/kb_service.py`
- Modify: `patent/server/patent/file_routes.py`
- Modify if needed: `patent/server/patent/file_models.py`
- Modify: `patent/server/services/chat_persistence.py`
- Modify: `patent/server/services/conversation_authority_client.py`
- Modify: `patent/server/patent/result_builder.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`
- Test: `patent/tests/test_chat_persistence.py`
- Test: `patent/tests/test_conversation_authority_client.py`
- Test: `patent/tests/test_patent_executor.py`
- Test: `patent/tests/test_patent_kb_service.py`

- [ ] **Step 1: Write failing tests for route-aware patent dispatch**

Cover:
- `kb_qa` continues to use the existing patent KB path
- `pdf_qa` dispatches to patent PDF path
- `tabular_qa` dispatches to patent tabular path
- `hybrid_qa` dispatches to patent hybrid path
- rollout gate keeps production-facing patent file routes disabled until executors are ready
- durable and ephemeral asks both honor the same route dispatch
- authority / persistence layers stop rewriting patent file turns into `kb_qa`
- existing `kb_qa` tests still pass unchanged

- [ ] **Step 2: Run the targeted dispatch tests**

Run: `pytest patent/tests/test_patent_executor.py patent/tests/test_patent_kb_service.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/test_chat_persistence.py patent/tests/test_conversation_authority_client.py -q`

Expected: FAIL because current executor only knows `PatentKbService`.

- [ ] **Step 3: Implement route-aware executor and ask-service wiring**

Implement:
- route dispatch in `PatentExecutor`
- file-route entry helpers in `patent/server/patent/file_routes.py`
- ask-service validation that route-aware results still satisfy response contract
- result builder / SSE event builder emit the executor-selected `route/source_scope`
- chat persistence / authority client preserve file-aware metadata for durable asks
- rollout gate behavior:
  - `kb_qa` stays enabled
  - patent file routes remain blocked unless the patent file route gate is explicitly enabled
  - patent-side gate acts as a second safety check even if gateway routing is misconfigured

- [ ] **Step 4: Keep `kb_qa` behavior unchanged while scaffolding file routes**

Requirements:
- no regression to current patent `kb_only` asks
- no change to authority ordering semantics
- no early production cutover for file traffic
- durable ask metadata for file turns must remain `requested_mode=patent` and `actual_mode=patent`

- [ ] **Step 5: Re-run targeted dispatch tests**

Run: `pytest patent/tests/test_patent_executor.py patent/tests/test_patent_kb_service.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/test_chat_persistence.py patent/tests/test_conversation_authority_client.py -q`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add patent/server_fastapi/routers/ask.py patent/server/services/ask_service.py patent/server/services/chat_persistence.py patent/server/services/conversation_authority_client.py patent/server/patent/executor.py patent/server/patent/file_routes.py patent/server/patent/file_models.py patent/server/patent/result_builder.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/test_chat_persistence.py patent/tests/test_conversation_authority_client.py patent/tests/test_patent_executor.py patent/tests/test_patent_kb_service.py
git commit -m "feat: add gated patent route-aware executor scaffold"
```

### Task 6: Integrate Patent PDF Route Into Shared Ask Flow

**Files:**
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/server/patent/file_routes.py`
- Modify: `patent/server/patent/executor.py`
- Modify: `patent/server/patent/result_builder.py`
- Test: `patent/tests/test_patent_file_routes.py`
- Test: `patent/tests/test_patent_executor.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Write failing tests for patent `pdf_qa`**

Cover:
- selected PDF file executes through patent PDF handler
- sync and stream payloads expose:
  - `route=pdf_qa`
  - `source_scope=pdf`
  - `used_files`
  - `file_selection`
- file contract is consumed directly, without re-inferring intent from question text
- patent PDF path uses patent-local knowledge/runtime dependencies only

- [ ] **Step 2: Run the patent PDF tests**

Run: `pytest patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_ask_contract.py -q -k pdf`

Expected: FAIL because patent has no PDF handler yet.

- [ ] **Step 3: Implement the patent PDF service with parity semantics**

Requirements:
- PDF content is the primary source
- route contract controls selected files
- result builder returns file-aware metadata
- no dependency on `fastQA` runtime objects

- [ ] **Step 4: Re-run the patent PDF tests**

Run: `pytest patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_ask_contract.py -q -k pdf`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/pdf_service.py patent/server/patent/file_routes.py patent/server/patent/executor.py patent/server/patent/result_builder.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_ask_contract.py
git commit -m "feat: add patent pdf qa route"
```

### Task 7: Integrate Patent Tabular And Hybrid Routes

**Files:**
- Modify: `patent/server/patent/tabular_service.py`
- Modify: `patent/server/patent/file_routes.py`
- Modify: `patent/server/patent/executor.py`
- Modify if needed: `patent/server/patent/kb_service.py`
- Modify: `patent/server/patent/result_builder.py`
- Test: `patent/tests/test_patent_file_routes.py`
- Test: `patent/tests/test_patent_executor.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`

- [ ] **Step 1: Write failing tests for patent `tabular_qa` and `hybrid_qa`**

Cover:
- `tabular_qa + source_scope=table` dispatches to patent tabular handler
- `hybrid_qa + pdf+kb` uses patent PDF primary path plus patent KB participation
- `hybrid_qa + table+kb` uses patent tabular primary path plus patent KB participation
- `hybrid_qa + pdf+table` remains legal without KB
- `hybrid_qa + pdf+table+kb` remains legal with both file families and patent KB
- result payloads preserve canonical `source_scope`

- [ ] **Step 2: Run the targeted tabular/hybrid tests**

Run: `pytest patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_ask_contract.py -q -k "tabular or hybrid"`

Expected: FAIL because these routes are not implemented in patent yet.

- [ ] **Step 3: Implement patent tabular and hybrid services**

Requirements:
- `tabular_qa` owns table-only execution
- `hybrid_qa` dispatches by `source_scope`
- patent knowledge participation is only sourced from patent-local retrieval/runtime
- `pdf+table` is treated as legal hybrid without forcing KB
- result payloads and SSE done metadata preserve the canonical `source_scope`

- [ ] **Step 4: Re-run the targeted tabular/hybrid tests**

Run: `pytest patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_ask_contract.py -q -k "tabular or hybrid"`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add patent/server/patent/tabular_service.py patent/server/patent/file_routes.py patent/server/patent/executor.py patent/server/patent/kb_service.py patent/server/patent/result_builder.py patent/tests/test_patent_file_routes.py patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_ask_contract.py
git commit -m "feat: add patent tabular and hybrid qa routes"
```

### Task 8: Open Patent File Traffic And Refresh Docs

**Files:**
- Modify: `gateway/app/routers/qa.py`
- Modify: `patent/server_fastapi/routers/ask.py`
- Modify: `docs/file_hybrid_qa_protocol_spec.md`
- Modify: `patent/docs/2026-03-25-patent-phase1-service-design.md`
- Modify if needed: `docs/superpowers/specs/2026-04-02-patent-file-routing-design.md`
- Test: `gateway/tests/test_qa_proxy.py`
- Test: `patent/tests/fastapi_contract/test_ask_contract.py`
- Test: `patent/tests/test_patent_file_routes.py`
- Test: `patent/tests/test_chat_persistence.py`
- Test: `patent/tests/test_conversation_authority_client.py`

- [ ] **Step 1: Write failing end-to-end contract tests for patent file traffic opening**

Cover:
- `requested_mode=patent + pdf_qa` now proxies through `gateway` to `patent`
- `requested_mode=patent + hybrid_qa` now proxies through `gateway` to `patent`
- `requested_mode=thinking + file route` still proxies to `fastQA`
- patent file traffic is open in the rollout batch because both gateway and patent gates now default to enabled
- explicit `false` on either gate still closes patent file traffic deterministically
- durable patent file asks persist file-aware `route/source_scope/actual_mode`

- [ ] **Step 2: Run the end-to-end contract tests**

Run: `pytest gateway/tests/test_qa_proxy.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/test_patent_file_routes.py patent/tests/test_chat_persistence.py patent/tests/test_conversation_authority_client.py -q`

Expected: PASS after the rollout batch flips both gates to default-open while preserving explicit disable coverage.

- [ ] **Step 3: Open the patent file route gate and remove stale compatibility assumptions**

Implement:
- enable patent file routing after executors are present
- keep thinking-file compatibility to fastQA
- update docs so they no longer state “file_only and mixed patent turns still belong to fastQA”
- remove stale persistence / authority assumptions that collapse patent file turns back to `kb_qa`

- [ ] **Step 4: Re-run the end-to-end contract tests**

Run: `pytest gateway/tests/test_qa_proxy.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/test_patent_file_routes.py patent/tests/test_chat_persistence.py patent/tests/test_conversation_authority_client.py -q`

Expected: PASS

- [ ] **Step 5: Run focused regression verification**

Run: `pytest gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/test_patent_executor.py patent/tests/test_patent_kb_service.py patent/tests/test_patent_file_routes.py patent/tests/test_chat_persistence.py patent/tests/test_conversation_authority_client.py -q`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add gateway/app/routers/qa.py patent/server_fastapi/routers/ask.py docs/file_hybrid_qa_protocol_spec.md patent/docs/2026-03-25-patent-phase1-service-design.md docs/superpowers/specs/2026-04-02-patent-file-routing-design.md gateway/tests/test_qa_proxy.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/test_patent_file_routes.py patent/tests/test_chat_persistence.py patent/tests/test_conversation_authority_client.py
git commit -m "feat: route patent file qa traffic to patent backend"
```

---

## Verification Checklist

Before considering the rollout complete, verify all of the following:

1. `gateway` no longer applies a global `file -> actual_mode=fast` shortcut
2. `thinking` file routes still execute in `fastQA`
3. `patent` file routes execute in `patent`
4. `patent` accepts the shared file route contract without re-inferring file intent
5. `hybrid_qa` remains legal for:
   - `pdf+kb`
   - `table+kb`
   - `pdf+table`
   - `pdf+table+kb`
6. `source_scope` remains canonically serialized across `gateway`, `fastQA`, and `patent`
7. legacy patent `kb_qa` behavior remains intact
8. patent durable / authority metadata persists file-aware `route/source_scope/actual_mode` without compatibility rewrite

## Execution Notes

- Do not change frontend mode buttons or request paths.
- Do not add a second file intent classifier inside `patent`.
- Do not move thinking file execution into `highThinkingQA`.
- The first two tasks are gateway-only by design.
- The first patent-side task is isolated file-module creation, specifically to reduce collision with parallel work on ordinary patent QA.
- If implementation reveals that `patent` cannot reasonably reuse any existing file processing boundaries, prefer introducing focused patent-local modules over adding branching into unrelated Phase 1 files.
