# FastQA Graph KB UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不影响 `kb_qa` 向量主链、文件问答链路和现有 PDF 阅读器主链的前提下，把 `fastQA` 的 `graph_kb` 结果改造成结构化 markdown 输出，并在前端只对图谱消息做局部 UI 美化。

**Architecture:** 本实现分成两条收敛主线。第一条是后端 graph renderer 主线，只在 `fastQA/app/modules/graph_kb/service.py` 内补充 graph-only 的清洗、结构化 markdown 模板和结果截断文案，不改图谱路由边界和主链回退逻辑。第二条是前端 graph-only 渲染主线，在 `Home.vue` 的 assistant 消息节点上挂载图谱 class，并通过 scoped `:deep(...)` 样式增强图谱 markdown 的章节、列表和 DOI 呈现，不污染普通回答样式。MVP 明确只依赖现有正文 DOI 点击打开 PDF 的能力，不在本计划内补 graph `done` 的 `reference_links / pdf_links / doi_locations` 对齐。

**Tech Stack:** FastAPI, Python, pytest, Vue 3, Vite, Node `--test`, scoped CSS with `:deep(...)`, markdown rendering via `marked`

---

## Source Documents

- Spec:
  - `docs/superpowers/specs/2026-04-12-fastqa-graph-kb-ui-design.md`
- Existing graph integration baseline:
  - `docs/superpowers/specs/2026-04-11-fastqa-literature-graph-kb-design.md`
  - `docs/superpowers/plans/2026-04-11-fastqa-literature-graph-kb-implementation.md`

## Current-State Notes

- 当前图谱答案仍由 `fastQA/app/modules/graph_kb/service.py` 中的 `render_graph_kb_answer()` 直接拼自然语言长段落。
- 当前 graph 路径已经具备：
  - `query_mode="graph_kb"`
  - 三个图谱阶段步骤
  - `done.references`
- 当前 graph 路径尚未补齐普通 `kb_qa` `done` 事件中的：
  - `reference_links`
  - `pdf_links`
  - `doi_locations`
- 当前前端 assistant 消息正文由 `frontend-vue/src/views/Home.vue` 中的 `v-html` 渲染，样式在同文件 `scoped` CSS 中维护。
- 当前前端对正文 DOI 已有统一 linkify 和点击打开 PDF 的能力：
  - `frontend-vue/src/utils/index.js`
  - `frontend-vue/src/views/Home.vue`

## MVP Decision Lock

本 implementation plan 明确锁定以下 MVP 决策：

1. **只改 `graph_kb` 成功命中后的答案内容与样式。**
2. **不修改 graph 路由回退逻辑。**
3. **不补 graph `done` 的 `reference_links / pdf_links / doi_locations`。**
4. **PDF 入口只依赖正文 DOI 点击链路。**
5. **不新增图谱专属 Vue 组件或新消息协议。**

若后续需要 graph `done` 元数据对齐，应单开后续计划，不在本计划内扩 scope。

## Workspace Conventions

### Repository Root

所有命令默认以仓库根目录为起点；不要在执行命令里硬编码当前 worktree 路径。

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
```

### Verification Rule

这个仓库在当前协作要求下，**所有测试和构建命令都必须在提权环境执行**。不要在沙箱里跑验证命令再补救。

### Frontend Test Entry

前端当前使用：

- `node --test`
- `npm run build`

而不是 Vitest / Jest。

## Hard Rules

1. 只允许修改 `query_mode=graph_kb` 的成功输出和图谱消息局部样式。
2. 不允许影响 `kb_qa` 向量主链、`pdf_qa`、`tabular_qa`、`hybrid_qa`。
3. 不允许把 graph UI 需求扩展成新的结果协议或图谱专属组件。
4. 不允许在本计划内补 graph `done` payload parity；这一版只依赖正文 DOI 点击。
5. 所有前端图谱样式必须走 `Home.vue` scoped CSS 下的 `:deep(...)`。
6. 后端图谱清洗必须保持“宁可保守，也不误伤”的策略，不能因为单条脏字段把整份结果打空。
7. 每个 task 走 TDD：先补失败测试，再做最小实现，再跑目标验证，再 commit。
8. 所有验证命令都必须提权执行。

## File Structure Lock-In

### Backend Rendering Surface

- Modify: `fastQA/app/modules/graph_kb/service.py`
- Test: `fastQA/tests/test_graph_kb_service.py`
- Test: `fastQA/tests/test_fastqa_kb_graph_integration.py`

### Frontend Message Rendering Surface

- Modify: `frontend-vue/src/views/Home.vue`
- Test: `frontend-vue/src/views/Home.structure.test.js`
- Test: `frontend-vue/src/utils/answerSummary.test.js`

### Optional New Focused Frontend Test

- Create if needed: `frontend-vue/src/utils/graphKbRender.test.js`

Only create the extra test file if existing tests become too awkward or overgrown; otherwise extend current tests.

## Acceptance Targets

### A. Backend Output

1. `list_by_raw_material` 输出 `##/###` 结构化 markdown，不再是长段落
2. `expand_doi_context_by_doi` 输出分段详情
3. `_null_ / null_ / 空 token / 重复命中描述` 被清洗
4. 当前展示条数文案真实反映“返回条数”，不虚报总数
5. 图谱结果仍只影响 `graph_kb`

### B. Frontend Rendering

1. 图谱消息节点挂载 graph-only class
2. 图谱 markdown 标题、列表、DOI、章节间距通过 `:deep(...)` 样式增强
3. 普通消息样式不变
4. 图谱消息步骤面板保持正常
5. 正文 DOI 点击仍可打开 PDF 阅读器

### C. Regression Safety

1. 图谱未命中或失败时仍静默回退主链
2. 已有 DOI 清洗边界测试不回归
3. 前端 `npm run build` 通过
4. 现有 `Home.structure.test.js` 和 markdown 渲染测试通过

## Task 1: Freeze Backend Markdown Contract With Failing Tests

**Files:**
- Modify: `fastQA/tests/test_graph_kb_service.py`
- Modify: `fastQA/tests/test_fastqa_kb_graph_integration.py`

- [ ] **Step 1: Add failing tests for structured list markdown output**

Cover:
- `list_by_raw_material` returns markdown headings instead of one prose sentence
- item blocks contain DOI on separate lines
- repeated raw-material match text is not duplicated inline as prose
- truncation wording uses “当前展示” / “结果已按上限截断” semantics instead of fake total-count wording

- [ ] **Step 2: Add failing tests for structured DOI-detail markdown output**

Cover:
- `expand_doi_context_by_doi` emits sections for base info, testing, process, parameters
- empty sections are omitted
- returned markdown still contains valid DOI references

- [ ] **Step 3: Add failing tests for dirty field normalization**

Cover:
- `_null_`, `null_`, orphan underscores, and repeated separators are removed
- dirty process fields like `method_ball milling_time_null_speed_null` are normalized into readable section content
- unreadable leftovers degrade to plain list items instead of dropping the entire answer

- [ ] **Step 4: Add integration regression test for graph success-path payload boundary**

Cover:
- raw graph SSE `done` frame still emits `references`
- raw `_iter_graph_kb_events()` output does **not** start emitting new `reference_links / pdf_links / doi_locations` in this MVP
- sync `/api/ask` aggregation still derives `reference_links / pdf_links / doi_locations` from `references` without dedicated graph-path changes
- graph `query_mode` and three success steps remain intact

- [ ] **Step 5: Run targeted backend tests to confirm failure**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT" && conda run --no-capture-output -n agent pytest fastQA/tests/test_graph_kb_service.py fastQA/tests/test_fastqa_kb_graph_integration.py -q
```

Expected:
- FAIL because backend still renders graph answers as long prose and lacks dirty-field normalization

- [ ] **Step 6: Commit test-only red state**

```bash
git add fastQA/tests/test_graph_kb_service.py fastQA/tests/test_fastqa_kb_graph_integration.py
git commit -m "test: define graph kb ui markdown contract"
```

## Task 2: Implement Backend Graph Markdown Rendering And Cleaning

**Files:**
- Modify: `fastQA/app/modules/graph_kb/service.py`
- Modify: `fastQA/tests/test_graph_kb_service.py`

- [ ] **Step 1: Add minimal graph-only cleaning helpers**

Implement helpers for:
- null token stripping
- duplicate separator cleanup
- empty token filtering
- safe string normalization for titles, bullets, and matched raw materials

Keep existing lightweight graph DOI normalization untouched except where new helpers compose around it.

- [ ] **Step 2: Add minimal detail-field parsing helpers**

Implement deterministic parsing for common dirty fields:
- `method`
- `time`
- `temperature`
- `speed`
- `ball_powder_ratio`
- `atmosphere`
- `thickness`

Rules:
- parse conservatively
- preserve unknown leftovers as plain bullet items
- never throw on malformed graph field input

- [ ] **Step 3: Replace prose renderers with markdown template builders**

Implement markdown builders for:
- `list_by_raw_material`
- `expand_doi_context_by_doi`

Keep other templates stable unless a minimal markdown conversion is required for consistency.

- [ ] **Step 4: Preserve existing fallback semantics**

Verify in implementation:
- sanitized-empty result still returns `render_empty`
- graph failure still returns `handled=False`
- no code path touches generation runtime

- [ ] **Step 5: Run targeted backend tests**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT" && conda run --no-capture-output -n agent pytest fastQA/tests/test_graph_kb_service.py fastQA/tests/test_fastqa_kb_graph_integration.py -q
```

Expected:
- PASS

- [ ] **Step 6: Commit backend graph renderer changes**

```bash
git add fastQA/app/modules/graph_kb/service.py fastQA/tests/test_graph_kb_service.py fastQA/tests/test_fastqa_kb_graph_integration.py
git commit -m "feat: render graph kb answers as structured markdown"
```

## Task 3: Freeze Frontend Graph-Only Rendering Contract With Failing Tests

**Files:**
- Modify: `frontend-vue/src/views/Home.structure.test.js`
- Modify: `frontend-vue/src/utils/answerSummary.test.js`
- Create if needed: `frontend-vue/src/utils/graphKbRender.test.js`

- [ ] **Step 1: Add failing structure test for graph-only message class wiring**

Cover:
- assistant message content wrapper adds a graph-only class when either:
  - `entry.message.queryMode === '知识图谱'`
  - or raw mode metadata resolves to `graph_kb` / `neo4j`
- existing badge / steps / markdown rendering branches remain present

- [ ] **Step 2: Add failing structure test for scoped `:deep(...)` graph styles**

Cover:
- `Home.vue` contains graph-only selectors using `:deep(h2)`, `:deep(h3)`, `:deep(ul)`, `:deep(.doi-link)` or equivalent
- selectors are scoped under a graph-only wrapper class rather than global markdown selectors

- [ ] **Step 3: Add failing markdown render test for graph-style-friendly output**

Cover:
- graph markdown headings render to `<h2>` / `<h3>`
- graph list bullets render to `<li>`
- DOI strings in graph markdown still become `.doi-link`

Prefer extending `answerSummary.test.js`; only create `graphKbRender.test.js` if the existing file becomes too noisy.

- [ ] **Step 4: Add failing structure test for DOI click flow preservation**

Cover:
- `Home.vue` still listens for `.doi-link`
- click handler still reads `data-doi`
- message lookup still uses `.message[data-message-index]`
- click flow still calls `buildCitationLocationsForDoi(...)`
- click flow still calls `pdfReader.value.openReader(...)`

- [ ] **Step 5: Run targeted frontend tests to confirm failure**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/frontend-vue"
GRAPH_FRONTEND_TESTS="src/views/Home.structure.test.js src/utils/answerSummary.test.js"
[ -f src/utils/graphKbRender.test.js ] && GRAPH_FRONTEND_TESTS="$GRAPH_FRONTEND_TESTS src/utils/graphKbRender.test.js"
node --test $GRAPH_FRONTEND_TESTS
```

Expected:
- FAIL because graph-only wrapper class and scoped `:deep(...)` styles do not exist yet

- [ ] **Step 6: Commit test-only red state**

```bash
git add frontend-vue/src/views/Home.structure.test.js frontend-vue/src/utils/answerSummary.test.js frontend-vue/src/utils/graphKbRender.test.js
git commit -m "test: define graph kb frontend rendering contract"
```

If no new file was created, omit it from the commit.

## Task 4: Implement Frontend Graph-Only UI Styling

**Files:**
- Modify: `frontend-vue/src/views/Home.vue`
- Modify: `frontend-vue/src/views/Home.structure.test.js`
- Modify: `frontend-vue/src/utils/answerSummary.test.js`
- Modify if created: `frontend-vue/src/utils/graphKbRender.test.js`

- [ ] **Step 1: Add graph-only class to assistant message wrapper**

Implement:
- a graph-only class on the assistant message content node, or a nested wrapper around the rendered HTML
- the class must derive from both:
  - existing normalized graph label checks
  - raw mode fallback from message metadata / raw mode fields for restored history messages

- [ ] **Step 2: Add scoped graph-only `:deep(...)` styles**

Implement styles for:
- section headings
- literature item spacing
- list indentation and density
- DOI emphasis
- light separators and spacing between blocks

Constraints:
- do not alter `.message-content` base styling globally
- do not alter non-graph markdown behavior

- [ ] **Step 3: Verify DOI click path remains intact**

Confirm the implementation does not break:
- `.doi-link` class generation from `formatAnswer()`
- `Home.vue` click handler opening `PdfReader`

No new PDF API work belongs here.

- [ ] **Step 4: Run targeted frontend tests**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/frontend-vue"
GRAPH_FRONTEND_TESTS="src/views/Home.structure.test.js src/utils/answerSummary.test.js"
[ -f src/utils/graphKbRender.test.js ] && GRAPH_FRONTEND_TESTS="$GRAPH_FRONTEND_TESTS src/utils/graphKbRender.test.js"
node --test $GRAPH_FRONTEND_TESTS
```

Expected:
- PASS

- [ ] **Step 5: Run frontend build**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/frontend-vue" && npm run build
```

Expected:
- PASS

- [ ] **Step 6: Commit frontend graph-only UI work**

```bash
git add frontend-vue/src/views/Home.vue frontend-vue/src/views/Home.structure.test.js frontend-vue/src/utils/answerSummary.test.js frontend-vue/src/utils/graphKbRender.test.js
git commit -m "feat: style graph kb answers in chat ui"
```

If no new file was created, omit it from the commit.

## Task 5: Run End-to-End Regression And Manual Smoke Checks

**Files:**
- Modify only if verification reveals issues:
  - `fastQA/app/modules/graph_kb/service.py`
  - `frontend-vue/src/views/Home.vue`
  - `fastQA/tests/test_graph_kb_service.py`
  - `fastQA/tests/test_fastqa_kb_graph_integration.py`
  - `frontend-vue/src/views/Home.structure.test.js`
  - `frontend-vue/src/utils/answerSummary.test.js`

- [ ] **Step 1: Run backend regression pack**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT" && conda run --no-capture-output -n agent pytest fastQA/tests/test_graph_kb_service.py fastQA/tests/test_fastqa_kb_graph_integration.py fastQA/tests/test_graph_kb_client.py -q
```

Expected:
- PASS

- [ ] **Step 2: Run frontend regression pack**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/frontend-vue"
GRAPH_FRONTEND_TESTS="src/views/Home.structure.test.js src/utils/answerSummary.test.js"
[ -f src/utils/graphKbRender.test.js ] && GRAPH_FRONTEND_TESTS="$GRAPH_FRONTEND_TESTS src/utils/graphKbRender.test.js"
node --test $GRAPH_FRONTEND_TESTS
```

Expected:
- PASS

- [ ] **Step 3: Run frontend production build**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/frontend-vue" && npm run build
```

Expected:
- PASS

- [ ] **Step 4: Manual smoke test graph list question**

Verify in running app:
- ask `有哪些使用 LiFePO4 作为原料的文献？`
- response shows graph badge
- steps panel still shows three graph steps
- literature list renders as sections/items instead of one long paragraph
- DOI remains clickable

- [ ] **Step 5: Manual smoke test DOI detail question**

Verify in running app:
- ask `10.1039/c4ra15767b 这篇文献包含哪些测试/表征和工艺信息？`
- detail answer shows sectioned markdown
- no `_null_` / `null_` leaks
- malformed process tokens are rendered readably instead of raw junk

- [ ] **Step 6: Manual smoke test non-graph fallback**

Verify in running app:
- ask `磷酸铁锂的电压是多少，压实密度是多少？`
- answer follows normal vector/generation path, not graph formatting

- [ ] **Step 7: Commit only if verification exposed fixes**

```bash
git add fastQA/app/modules/graph_kb/service.py frontend-vue/src/views/Home.vue fastQA/tests/test_graph_kb_service.py fastQA/tests/test_fastqa_kb_graph_integration.py frontend-vue/src/views/Home.structure.test.js frontend-vue/src/utils/answerSummary.test.js frontend-vue/src/utils/graphKbRender.test.js
git commit -m "fix: polish graph kb ui verification issues"
```

If verification required no further code changes, skip this commit.

## Final Verification Checklist

- [ ] Graph success answers are structured markdown, not long prose
- [ ] Graph detail answers omit `_null_` noise
- [ ] Graph-only styles are scoped and use `:deep(...)`
- [ ] Normal answers remain visually unchanged
- [ ] Graph steps still render
- [ ] Inline DOI click still opens PDF reader
- [ ] Backend graph tests pass
- [ ] Frontend node tests pass
- [ ] Frontend build passes
- [ ] No graph `done` payload parity work was accidentally added in this MVP

## Notes For Executor

- Do not touch unrelated dirty files under `patent/`; they are outside this plan.
- Do not fold this work into the earlier graph integration docs commit.
- If the frontend implementation cannot cleanly express the graph-only class without broader template churn, stop and split that change into a narrower preparatory commit.
- If product later wants graph answers to expose the same metadata richness as standard `kb_qa`, create a follow-up spec/plan for graph `done` payload parity instead of quietly extending this MVP.
