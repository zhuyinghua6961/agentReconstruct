# Gateway QA Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `gateway` 的 QA 路由改造成“规则优先，歧义时轻量分类器二判”的统一体系，稳定覆盖 `kb_qa / pdf_qa / tabular_qa / hybrid_qa`，消除当前文件误路由问题。

**Architecture:** 保持 `frontend -> gateway -> fastQA/highThinkingQA/patent` 的总体结构不变，所有路由决策继续集中在 `gateway`。第一阶段先收敛 deterministic rule contract 和状态响应；第二阶段再接入轻量分类器，只处理歧义样本；下游 `fastQA` 只消费标准化后的 route contract，不再重算文件意图。

**Tech Stack:** FastAPI, Python, pytest, gateway routing, structured classifier JSON output, frontend Vue ask client

---

## Source Documents

- Spec: `docs/superpowers/specs/2026-03-30-gateway-qa-routing-design.md`
- Related current docs:
  - `docs/file_hybrid_qa_protocol_spec.md`
  - `docs/multi_mode_api_contract.md`
  - `docs/multi_mode_gateway_architecture.md`

## Current-State Implementation Notes

- 当前路由核心代码在：
  - `gateway/app/services/file_context_resolver.py`
  - `gateway/app/services/route_decision.py`
  - `gateway/app/routers/qa.py`

- 当前问题集中在：
  - `selected_ids` 语义过强或不一致
  - 单字级表格关键词误判
  - `last_focus` / `last_turn_route` 有跨问题污染风险
  - route decision 日志解释信息不完整
  - zero-ready 文件状态没有统一响应合同

- 第一阶段不做：
  - 大规模重写前端交互
  - 让 `fastQA` 重构内部执行
  - 让 `gateway` 读文件内容

## File Structure Lock-In

### Gateway Core
- Modify: `gateway/app/services/file_context_resolver.py`
- Modify: `gateway/app/services/route_decision.py`
- Modify: `gateway/app/routers/qa.py`
- Modify: `gateway/app/models/routing.py`
- Modify: `gateway/app/models/files.py` only if contract fields need explicit model updates

### Gateway Tests
- Modify: `gateway/tests/test_route_decision.py`
- Modify: `gateway/tests/test_qa_proxy.py`
- Create if absent: `gateway/tests/test_file_context_resolver.py`

### fastQA Contract Consumption
- Modify: `fastQA/app/services/request_adapter.py`
- Modify: `fastQA/app/routers/qa.py`
- Test: `fastQA/tests/test_request_adapter.py`

### Frontend
- Modify: `frontend-vue/src/services/api.js`
- Modify: `frontend-vue/src/views/Home.vue`
- Add/update frontend tests only if current test harness exists; otherwise rely on build + manual contract validation

### Docs
- Modify: `docs/superpowers/specs/2026-03-30-gateway-qa-routing-design.md` if implementation reveals spec drift
- Update: `docs/multi_mode_api_contract.md`
- Update: `docs/file_hybrid_qa_protocol_spec.md`

---

### Task 1: Freeze Gateway Route Contract

**Files:**
- Modify: `gateway/app/models/routing.py`
- Modify: `gateway/app/services/route_decision.py`
- Modify: `gateway/app/routers/qa.py`
- Test: `gateway/tests/test_route_decision.py`
- Test: `gateway/tests/test_qa_proxy.py`

- [x] **Step 1: Write failing tests for full route contract normalization**

Cover:
- `source_scope -> turn_mode` canonical mapping
- `kb` -> `kb_only`
- `pdf+table` -> `file_only`
- `pdf+kb` / `table+kb` / `pdf+table+kb` -> `mixed`
- `requested_mode=thinking + file route -> actual_mode=fast`
- `requested_mode=patent + file route -> actual_mode=fast`
- full normalized payload fields:
  - `requested_mode`
  - `actual_mode`
  - `route`
  - `turn_mode`
  - `source_scope`
  - `selected_file_ids`
  - `execution_files`
  - `strategy`
  - `needs_clarification`
  - `file_selection`
  - `route_reasons`
  - `route_confidence`
  - `classifier_used`
- router-level dispatch:
  - `thinking + file route -> fastQA`
  - `patent + file route -> fastQA`
  - forwarded payload includes full frozen contract

- [x] **Step 2: Run targeted tests to confirm failure**

Run: `pytest gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py -q`

- [x] **Step 3: Update routing models to support the full frozen contract**

Implement:
- explicit `requested_mode`
- explicit `actual_mode`
- explicit `route`
- explicit `source_scope`
- explicit `turn_mode`
- explicit `selected_file_ids`
- explicit `execution_files`
- explicit `strategy`
- explicit `needs_clarification`
- explicit `file_selection`
- `classifier_used`
- `route_confidence`
- `route_reasons`

- [x] **Step 4: Update RouteDecisionService to enforce the canonical mapping**

- [x] **Step 5: Re-run targeted tests**

Run: `pytest gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py -q`

- [x] **Step 6: Commit**

```bash
git add gateway/app/models/routing.py gateway/app/services/route_decision.py gateway/app/routers/qa.py gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py
git commit -m "refactor: freeze gateway qa route contract"
```

### Task 2: Rebuild File Intent Resolution Semantics

**Files:**
- Modify: `gateway/app/services/file_context_resolver.py`
- Test: `gateway/tests/test_file_context_resolver.py`

- [x] **Step 1: Write failing tests for explicit intent vs selection scope**

Cover:
- no files + plain question -> `kb_qa`
- pdf files exist + plain question -> `kb_qa`
- selected files only + plain question -> ambiguity path, not direct file route
- selected files + explicit file action -> file route
- selected files + explicit mixed intent -> `hybrid_qa`
- `last_focus_ids` without deictic reference does not force file route
- valid deictic `last_focus` reuse -> file route
- invalid `last_focus` reuse -> no force route
- `last_turn_route` 仅作弱状态，不单独决定 route
- `newly_uploaded` 只在显式“最新上传/刚上传”语义下复用

- [x] **Step 2: Run targeted tests to confirm failure**

Run: `pytest gateway/tests/test_file_context_resolver.py -q`

- [x] **Step 3: Refactor resolver into clear sub-steps**

Split logic conceptually:
- candidate universe normalization
- explicit-use-intent detection
- explicit-scope-context detection
- strong-rule evaluation
- ambiguity detection
- fallback to classifier / fallback to kb

- [x] **Step 4: Implement canonical `selected_ids` semantics**

Rules:
- `selected_ids` narrows candidate scope
- `selected_ids` alone does not direct-route
- explicit file action or explicit ref is still required for direct file routing

- [x] **Step 5: Re-run targeted tests**

Run: `pytest gateway/tests/test_file_context_resolver.py -q`

- [x] **Step 6: Commit**

```bash
git add gateway/app/services/file_context_resolver.py gateway/tests/test_file_context_resolver.py
git commit -m "refactor: align gateway file intent resolution semantics"
```

### Task 3: Remove Weak Single-Token Table Misrouting

**Files:**
- Modify: `gateway/app/services/file_context_resolver.py`
- Test: `gateway/tests/test_file_context_resolver.py`

- [x] **Step 1: Write failing regression tests for table false positives**

Cover:
- `列出 3 种原因` with only PDFs in session -> not `tabular_qa`
- `进行分析` with PDFs -> not `tabular_qa`
- `表明` in a sentence -> not `tabular_qa`
- real table query such as `按电压列筛选` with selected table -> `tabular_qa`

- [x] **Step 2: Run the regression tests**

Run: `pytest gateway/tests/test_file_context_resolver.py -q -k table`

- [x] **Step 3: Replace weak token matching with strong table-intent patterns**

Rules:
- forbid single-token `列/行/表` as strong triggers
- allow explicit table references
- allow structured patterns like `按X列筛选`, `按字段分组`, `输出前N行`
- keep column-name matching as weak evidence only

- [x] **Step 4: Re-run the regression tests**

Run: `pytest gateway/tests/test_file_context_resolver.py -q -k table`

- [x] **Step 5: Commit**

```bash
git add gateway/app/services/file_context_resolver.py gateway/tests/test_file_context_resolver.py
git commit -m "fix: remove weak token table routing false positives"
```

### Task 4: Define Reference Resolution Universe And Zero-Ready Status Responses

**Files:**
- Modify: `gateway/app/services/file_context_resolver.py`
- Modify: `gateway/app/routers/qa.py`
- Test: `gateway/tests/test_file_context_resolver.py`
- Test: `gateway/tests/test_qa_proxy.py`

- [x] **Step 1: Write failing tests for reference resolution ordering**

Cover:
- `#1`, `第1个文件`, `前3个文件`, `后2个文件`
- ordering by `display_no -> file_no -> file_id`
- resolution uses non-deleted universe
- execution uses ready subset

- [x] **Step 2: Write failing tests for zero-ready explicit file intent**

Cover:
- explicit file ref to `processing` file -> `FILE_NOT_READY`
- explicit file ref to `failed` file -> `FILE_PROCESSING_FAILED`
- explicit file ref to deleted/missing file -> `FILE_NOT_FOUND`
- selected files + explicit file action but no ready execution files -> same non-routing status response

- [x] **Step 3: Run route and resolver tests**

Run: `pytest gateway/tests/test_file_context_resolver.py gateway/tests/test_qa_proxy.py -q`

- [x] **Step 4: Implement unified zero-ready handling**

Requirements:
- one branch for explicit file intent with empty `execution_files`
- same behavior for sync and stream entrypoints
- response contains code, retriable flag, file status summary

- [ ] **Step 5: Re-run route and resolver tests**

Run: `pytest gateway/tests/test_file_context_resolver.py gateway/tests/test_qa_proxy.py -q`

- [ ] **Step 6: Commit**

```bash
git add gateway/app/services/file_context_resolver.py gateway/app/routers/qa.py gateway/tests/test_file_context_resolver.py gateway/tests/test_qa_proxy.py
git commit -m "feat: add deterministic file reference and zero-ready responses"
```

### Task 5: Implement Clarification Response Contract

**Files:**
- Modify: `gateway/app/services/file_context_resolver.py`
- Modify: `gateway/app/routers/qa.py`
- Test: `gateway/tests/test_file_context_resolver.py`
- Test: `gateway/tests/test_qa_proxy.py`

- [x] **Step 1: Write failing clarification tests**

Cover:
- explicit `这篇文献` with multiple candidate PDFs -> clarification
- explicit `这个表格` with multiple candidate tables -> clarification
- invalid ordinal / unresolved `#编号` -> clarification
- multi-candidate deictic references -> clarification
- sync and stream entrypoints both surface clarification payloads correctly

- [x] **Step 2: Run targeted tests**

Run: `pytest gateway/tests/test_file_context_resolver.py gateway/tests/test_qa_proxy.py -q -k clarify`

- [x] **Step 3: Implement clarification payload contract**

Requirements:
- `needs_clarification=true`
- candidate summary
- stable code / message contract for sync and stream
- no silent fallback to `kb_qa`

- [x] **Step 4: Re-run targeted tests**

Run: `pytest gateway/tests/test_file_context_resolver.py gateway/tests/test_qa_proxy.py -q -k clarify`

- [x] **Step 5: Commit**

```bash
git add gateway/app/services/file_context_resolver.py gateway/app/routers/qa.py gateway/tests/test_file_context_resolver.py gateway/tests/test_qa_proxy.py
git commit -m "feat: add gateway clarification response contract"
```

### Task 6: Add Structured Route Explanations And Logging

**Files:**
- Modify: `gateway/app/services/route_decision.py`
- Modify: `gateway/app/routers/qa.py`
- Test: `gateway/tests/test_route_decision.py`
- Test: `gateway/tests/test_qa_proxy.py`

- [x] **Step 1: Write failing tests for route explanation payload**

Cover:
- `strategy`
- `route_reasons`
- `route_confidence`
- `classifier_used`
- `selected_file_ids`
- `source_scope`

- [x] **Step 2: Run tests to confirm failure**

Run: `pytest gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py -q`

- [x] **Step 3: Implement route explanation fields**

Ensure:
- route result is inspectable in logs
- metadata can flow to downstream
- clarification/status responses also include enough context

- [x] **Step 4: Add gateway route decision log lines**

Log fields:
- `trace_id`
- `requested_mode`
- `actual_mode`
- `route`
- `turn_mode`
- `source_scope`
- `selected_file_ids`
- `strategy`
- `reason_codes`
- `classifier_used`
- `route_confidence`

- [x] **Step 5: Re-run tests**

Run: `pytest gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py -q`

- [x] **Step 6: Commit**

```bash
git add gateway/app/services/route_decision.py gateway/app/routers/qa.py gateway/tests/test_route_decision.py gateway/tests/test_qa_proxy.py
git commit -m "feat: add explainable gateway routing metadata"
```

### Task 7: Align Downstream Contract Consumption In fastQA

**Files:**
- Modify: `fastQA/app/services/request_adapter.py`
- Modify: `fastQA/app/routers/qa.py`
- Test: `fastQA/tests/test_request_adapter.py`

- [x] **Step 1: Write failing contract-consumption tests**

Cover:
- `fastQA` consumes `route`, `turn_mode`, `source_scope`, `selected_file_ids`, `execution_files`
- `fastQA` does not reinterpret route from raw question
- `used_files` remains downstream telemetry, not required gateway input

- [x] **Step 2: Run targeted tests**

Run: `pytest fastQA/tests/test_request_adapter.py -q`

- [x] **Step 3: Tighten adapter validation**

Rules:
- reject invalid route/source_scope combinations
- reject missing required file families for file routes
- accept explainability fields without re-deciding route

- [x] **Step 4: Re-run targeted tests**

Run: `pytest fastQA/tests/test_request_adapter.py -q`

- [ ] **Step 5: Commit**

```bash
git add fastQA/app/services/request_adapter.py fastQA/app/routers/qa.py fastQA/tests/test_request_adapter.py
git commit -m "refactor: align fastqa with gateway route contract"
```

### Task 8: Add Lightweight Classifier Integration Seam

**Files:**
- Create: `gateway/app/services/route_classifier.py`
- Modify: `gateway/app/services/file_context_resolver.py`
- Modify: `gateway/app/core/config.py`
- Test: `gateway/tests/test_file_context_resolver.py`
- Test: `gateway/tests/test_route_classifier.py`

- [x] **Step 1: Write failing tests for ambiguity-only classifier usage**

Cover:
- classifier is not called for deterministic explicit routes
- classifier is called for ambiguity cases only
- low-confidence output falls back to rules
- high-confidence output can choose `kb_qa` / `pdf_qa` / `tabular_qa` / `hybrid_qa`
- exact threshold behavior:
  - `>= 0.80` may override ambiguity default
  - `0.60 - 0.79` only applies when not conflicting with rule layer
  - `< 0.60` always falls back to rule/default path
- conflict case:
  - mid-confidence classifier proposes file route while rule layer says no explicit file intent -> must not override
- minimum schema:
  - `route`
  - `turn_mode`
  - `source_scope`
  - `confidence`
  - `reason_codes`

- [x] **Step 2: Run tests to confirm failure**

Run: `pytest gateway/tests/test_file_context_resolver.py gateway/tests/test_route_classifier.py -q`

- [x] **Step 3: Create a classifier interface, not a hard provider**

Requirements:
- provider-agnostic contract
- structured JSON response
- disabled-by-default config
- injectable transport/client for tests
- explicit threshold config or constants matching the spec

- [x] **Step 4: Integrate the classifier into the ambiguity path only**

- [x] **Step 5: Re-run tests**

Run: `pytest gateway/tests/test_file_context_resolver.py gateway/tests/test_route_classifier.py -q`

- [ ] **Step 6: Commit**

```bash
git add gateway/app/services/route_classifier.py gateway/app/services/file_context_resolver.py gateway/app/core/config.py gateway/tests/test_file_context_resolver.py gateway/tests/test_route_classifier.py
git commit -m "feat: add ambiguity-only gateway route classifier seam"
```

### Task 9: Align Frontend Handling With New Routing Responses

**Files:**
- Modify: `frontend-vue/src/services/api.js`
- Modify: `frontend-vue/src/views/Home.vue`
- Validation: `cd frontend-vue && npm run build`

- [x] **Step 1: Inspect current ask/ask_stream error handling and identify exact insert points**

- [x] **Step 2: Implement frontend handling for non-routing status responses**

Support:
- `FILE_NOT_READY`
- `FILE_PROCESSING_FAILED`
- `FILE_NOT_FOUND`
- clarification responses

- [x] **Step 3: Ensure route-change UX remains stable**

Requirements:
- plain `kb_qa` still works even when files exist
- file-status prompts are readable
- no silent fallback to wrong route

- [x] **Step 4: Run frontend build**

Run: `cd frontend-vue && npm run build`

- [ ] **Step 5: Commit**

```bash
git add frontend-vue/src/services/api.js frontend-vue/src/views/Home.vue
git commit -m "feat: align frontend with gateway routing status responses"
```

### Task 10: Update Protocol Documentation

**Files:**
- Modify: `docs/multi_mode_api_contract.md`
- Modify: `docs/file_hybrid_qa_protocol_spec.md`
- Modify: `docs/superpowers/specs/2026-03-30-gateway-qa-routing-design.md` only if implementation discovers necessary clarifications

- [x] **Step 1: Update API contract docs to reflect the frozen route fields**

Include:
- `route`
- `turn_mode`
- `source_scope`
- `selected_file_ids`
- `execution_files`
- `route_reasons`
- `route_confidence`
- `classifier_used`

- [x] **Step 2: Update file/hybrid protocol doc to match the new route authority rules**

- [x] **Step 3: Proofread for drift against implementation**

- [ ] **Step 4: Commit**

```bash
git add docs/multi_mode_api_contract.md docs/file_hybrid_qa_protocol_spec.md docs/superpowers/specs/2026-03-30-gateway-qa-routing-design.md
git commit -m "docs: align routing contracts with gateway qa route spec"
```

### Task 11: End-to-End Verification

**Files:**
- No new source files unless missing integration tests need to be added

- [x] **Step 1: Run gateway test suite**

Run: `pytest gateway/tests -q`

Result: `257 passed`

- [x] **Step 2: Run fastQA contract tests**

Run: `pytest fastQA/tests/test_request_adapter.py -q`

Result: `27 passed`

- [x] **Step 3: Build frontend**

Run: `cd frontend-vue && npm run build`

Result: build success

- [ ] **Step 4: Manual routing verification**

Validate at minimum:
- plain question with PDFs in session -> `kb_qa`
- explicit PDF question -> `pdf_qa`
- explicit table question -> `tabular_qa`
- explicit file + KB question -> `hybrid_qa`
- selected files but no file intent -> `kb_qa` or classifier path, not direct file route
- explicit file intent with non-ready file -> status response, no execution route
- ambiguous explicit file/table reference -> clarification, no silent fallback
- `thinking` file route -> dispatched to `fastQA`
- `patent` file route -> dispatched to `fastQA`
- classifier ambiguity path honors confidence thresholds

- [ ] **Step 5: Commit final verification note**

```bash
git add .
git commit -m "test: verify gateway qa routing contract end to end"
```

---

## Review Checklist

Before implementation is considered complete, confirm:

- `selected_ids` no longer directly force file route
- `turn_mode` mapping is deterministic and test-covered
- table false positives are blocked by regression tests
- ordinal resolution universe is deterministic
- zero-ready file responses are consistent for sync and stream
- clarification responses are consistent for sync and stream
- `used_files` is not treated as gateway-owned route input
- `fastQA` no longer re-decides route from raw question
- frontend can surface non-routing file status responses clearly

## Execution Order Recommendation

Recommended order:

1. Task 1
2. Task 2
3. Task 3
4. Task 4
5. Task 5
6. Task 6
7. Task 7
8. Task 8
9. Task 9
10. Task 10
11. Task 11

Reason:

- 先收 deterministic contract 和误判
- 再收 clarification 和 explainability
- 再收 downstream contract 与前端协议
- 分类器 seam 在 final verification 之前接入，避免“验证后再加新变量”
