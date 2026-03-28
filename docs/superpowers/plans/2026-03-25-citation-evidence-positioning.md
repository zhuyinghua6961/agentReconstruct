# Citation Evidence Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `fastQA` 与 `highThinkingQA` 落地统一的引用证据预览 contract，让前端点击 DOI 时至少能看到 evidence preview，并在有页码时支持跳页。

**Architecture:** 第一版不做句子级高亮，也不重建整套索引。后端统一输出 richer `reference_objects` 与可选 `doi_locations`；前端复用现有 DOI 点击入口、`PdfReader` 和引用面板消费这些字段。`fastQA` 先复用已有 `sample_text`，`highThinkingQA` 先从 `RetrievedChunk` 构造 evidence payload。

**Tech Stack:** Python, FastAPI, Vue 3, Pinia, pytest, node test, Vite

---

### Task 1: 固化第一版 richer reference contract

**Files:**
- Modify: `docs/audit/2026-03-25-p4-citation-evidence-positioning-spec.md`
- Create: `docs/audit/2026-03-26-reference-object-contract-audit.md`
- Test: `fastQA/tests/test_reference_link_boundary.py`
- Create: `highThinkingQA/tests/test_reference_payload.py`

- [ ] **Step 1: 写 `fastQA` 失败测试，锁定 richer reference object 字段**

要求至少断言：
- `references` 兼容保留
- `reference_objects` 允许字典字段透传
- `evidence_text/sample_text/section_name/page/chunk_index` 不会被裁掉

- [ ] **Step 2: 运行 `fastQA` 失败测试确认当前行为缺口**

Run: `conda run -n agent pytest fastQA/tests/test_reference_link_boundary.py -q`
Expected: FAIL

- [ ] **Step 3: 写 `highThinkingQA` 失败测试，锁定 done payload 最小 shape**

要求至少断言：
- `reference_objects` 为稳定列表
- 每项至少有 `doi/evidence_text/section_name/chunk_index/page/page_range/locator_confidence`
- 无页码时字段为空值而不是缺失

- [ ] **Step 4: 运行 `highThinkingQA` 失败测试确认当前行为缺口**

Run: `conda run -n agent pytest highThinkingQA/tests/test_reference_payload.py -q`
Expected: FAIL

- [ ] **Step 5: 记录 contract audit 文档**

写清：
- 第一版统一字段
- 兼容字段
- 允许为空的字段

### Task 2: 让 fastQA 输出 evidence preview payload

**Files:**
- Modify: `fastQA/app/modules/generation_pipeline/synthesis_postprocess.py`
- Modify: `fastQA/app/modules/qa_kb/streaming.py`
- Modify: `fastQA/app/routers/qa.py`
- Test: `fastQA/tests/test_reference_link_boundary.py`

- [ ] **Step 1: 实现 `fastQA` reference object 正常输出 `evidence_text`**

要求：
- 兼容已有 `sample_text`
- 优先统一到 `evidence_text`
- 保留 `doi/title/section_name/chunk_index`

- [ ] **Step 2: 如 `pdf_chunks` 上游已有页码，补 `doi_locations` 组装**

要求：
- 没页码时返回空数组/空字段
- 不要伪造页码

- [ ] **Step 3: 跑定向测试直到通过**

Run: `conda run -n agent pytest fastQA/tests/test_reference_link_boundary.py -q`
Expected: PASS

- [ ] **Step 4: 做一次 fastQA 兼容自检**

确认：
- `references/reference_links/pdf_links` 不变
- 旧前端不因 richer fields 崩溃

### Task 3: 让 highThinkingQA 输出 evidence preview payload

**Files:**
- Modify: `highThinkingQA/retriever/vector_retriever.py`
- Modify: `highThinkingQA/server/services/ask_service.py`
- Modify: `highThinkingQA/server_fastapi/routers/ask.py`
- Test: `highThinkingQA/tests/test_reference_payload.py`

- [ ] **Step 1: 扩 `RetrievedChunk` 暴露第一版需要的 locator 字段**

最小目标：
- `section_name`
- `chunk_index`
- `text`
- 可选 `page/page_range`

- [ ] **Step 2: 在 `ask_service` done payload 组装 richer `reference_objects`**

要求：
- 从最终引用 DOI 对回检索 chunks
- 选一段稳定 `evidence_text`
- 无页码时明确传空值

- [ ] **Step 3: 保持 `reference_links/pdf_links` 完全兼容**

- [ ] **Step 4: 跑定向测试直到通过**

Run: `conda run -n agent pytest highThinkingQA/tests/test_reference_payload.py -q`
Expected: PASS

### Task 4: 前端复用现有入口展示 evidence preview

**Files:**
- Modify: `frontend-vue/src/stores/chatStore.js`
- Modify: `frontend-vue/src/services/api.js`
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/components/PdfReader.vue`
- Modify: `frontend-vue/src/features/references/components/ReferencePanel.vue`
- Modify: `frontend-vue/src/features/references/composables/useReferenceInspector.js`
- Create: `frontend-vue/src/features/references/referenceEvidence.test.js`

- [ ] **Step 1: 写前端失败测试，锁定 richer reference object 的 normalize 行为**

要求：
- 有 evidence payload 时能保留
- 无页码时不崩
- DOI 点击仍能打开原文入口

- [ ] **Step 2: 让 store/api 保留 richer `reference_objects` 和 `doi_locations`**

- [ ] **Step 3: 在 `PdfReader` 里消费 evidence preview**

要求：
- 点击引用先能看到 evidence preview
- 有页码时继续跳页
- 无页码时正常显示证据卡片

- [ ] **Step 4: 在引用面板里显示 evidence preview 摘要**

要求：
- 优先显示问答返回的证据
- `reference_preview` API 继续只补 title/journal 等轻元数据

- [ ] **Step 5: 跑前端测试与 build**

Run: `cd frontend-vue && node --test src/features/references/referenceEvidence.test.js`
Expected: PASS

Run: `cd frontend-vue && npm run build`
Expected: exit 0

### Task 5: 验证、文档、提交

**Files:**
- Modify: `docs/superpowers/plans/2026-03-25-citation-evidence-positioning.md`
- Modify: `docs/audit/2026-03-26-reference-object-contract-audit.md`

- [ ] **Step 1: 跑后端定向测试**

Run: `conda run -n agent pytest fastQA/tests/test_reference_link_boundary.py highThinkingQA/tests/test_reference_payload.py -q`
Expected: PASS

- [ ] **Step 2: 跑前端定向测试与 build**

Run: `cd frontend-vue && node --test src/features/references/referenceEvidence.test.js`
Expected: PASS

Run: `cd frontend-vue && npm run build`
Expected: exit 0

- [ ] **Step 3: 补文档中的已知缺口**

必须写清：
- `highThinkingQA` 页码字段暂未完全闭环
- 第二阶段才考虑 metadata 重建与句子级高亮

- [ ] **Step 4: 单独提交这一条线**

```bash
git add fastQA highThinkingQA frontend-vue docs/audit/2026-03-25-p4-citation-evidence-positioning-spec.md docs/audit/2026-03-26-reference-object-contract-audit.md docs/superpowers/plans/2026-03-25-citation-evidence-positioning.md
git commit -m "feat: add citation evidence preview contract"
```
