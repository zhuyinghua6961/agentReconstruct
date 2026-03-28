# Translation Selection And Paste Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为翻译模块先落“导入剪贴板文本”这一版低风险能力，并在此基础上盘清阅读器选区事件，决定是否推进第二阶段“划选即翻译”。

**Architecture:** 第一阶段只做用户主动触发的剪贴板导入按钮，避免浏览器权限与阅读器选区耦合；同时补一份阅读器选区能力审计，为第二阶段浮动翻译按钮做输入条件确认。实现上优先在 `frontend-vue` 的翻译入口和原文阅读器之间增加最小边界，不改后端翻译主链协议。

**Tech Stack:** Vue 3, Vite, browser Clipboard API, existing translation APIs, npm build/test

---

### Task 1: 盘清翻译入口、阅读器入口与当前交互边界

**Files:**
- Read: `frontend-vue/src/views/Home.vue`
- Read: `frontend-vue/src/components/**`
- Read: `frontend-vue/src/router/**`
- Create: `docs/audit/2026-03-25-translation-ui-boundary-audit.md`

- [ ] **Step 1: 找出翻译主入口组件与状态管理位置**

审计文档必须写清：
- 翻译输入框在哪个组件维护
- 发送翻译请求的入口函数在哪里
- 当前是否已有文本长度限制与错误提示

- [ ] **Step 2: 找出原文阅读器入口**

审计文档必须写清：
- PDF / HTML / markdown 阅读器分别在哪里渲染
- 这些容器是否有选区事件可接入点

- [ ] **Step 3: 明确第一版不改后端协议**

文档必须写清：
- 剪贴板导入只是填充现有输入框
- 后端翻译接口不需要新增字段

### Task 2: 先写失败测试，锁定“导入剪贴板”最小交互

**Files:**
- Create: `frontend-vue/src/__tests__/translation-import-clipboard.spec.ts`
- Possibly Modify: `frontend-vue/package.json` if test script plumbing is needed

- [ ] **Step 1: 写失败测试**

新增测试验证：
- 点击“导入剪贴板”按钮后，读取剪贴板文本并写入翻译输入框
- 读取失败时给出可见错误提示
- 不自动触发翻译请求

- [ ] **Step 2: 若已有测试体系缺口，先补最小测试支架**

要求：
- 只补本功能所需的最小前端测试运行能力
- 不扩散到无关测试框架重构

### Task 3: 实现第一版剪贴板导入按钮

**Files:**
- Modify: `frontend-vue/src/views/Home.vue`
- Or Create/Modify: `frontend-vue/src/components/translation/**`
- Possibly Modify: `frontend-vue/src/styles/**`

- [ ] **Step 1: 增加“导入剪贴板”按钮**

要求：
- 按钮位置紧邻翻译输入框
- 文案直接、可理解
- 无文本时也允许点击，但失败要有反馈

- [ ] **Step 2: 通过用户手势读取 Clipboard API**

要求：
- 严格在点击事件中读取
- 不做页面加载时自动读取
- 长文本要做长度截断或提示

- [ ] **Step 3: 导入成功后只填充，不自动发起翻译**

要求：
- 保证用户体验可控
- 避免误翻译、误计费

### Task 4: 审计第二阶段“划选即翻译”的可行性

**Files:**
- Modify: `docs/audit/2026-03-25-translation-ui-boundary-audit.md`
- Create: `docs/audit/2026-03-25-translation-selection-feasibility.md`
- Read: `frontend-vue/src/**viewer**`

- [ ] **Step 1: 盘清 PDF/HTML 阅读器是否能提供稳定选区文本**

结论必须写清：
- 哪种阅读器可拿到选区文本
- 哪种阅读器拿不到或成本高

- [ ] **Step 2: 给出第二阶段 go / no-go 建议**

结论必须写清：
- 是否值得进入“选中后浮动翻译按钮”
- 如果值得，第一刀先在哪种阅读器上试点

### Task 5: 跑验证并提交第一阶段

**Files:**
- Test: `frontend-vue/src/__tests__/translation-import-clipboard.spec.ts`
- Test: `docs/audit/2026-03-25-translation-ui-boundary-audit.md`
- Modify: `docs/superpowers/plans/2026-03-25-translation-selection-paste.md`

- [ ] **Step 1: 跑前端测试或最小构建验证**

Run: `cd frontend-vue && npm run build`
Expected: exit 0

- [ ] **Step 2: 人工验证剪贴板导入交互**

检查项：
- 成功导入
- 读取失败提示
- 不自动翻译
- 长文本反馈正常

- [ ] **Step 3: 提交**

```bash
git add docs/audit/2026-03-25-translation-ui-boundary-audit.md docs/audit/2026-03-25-translation-selection-feasibility.md frontend-vue docs/superpowers/plans/2026-03-25-translation-selection-paste.md
git commit -m "feat: add translation clipboard import flow"
```
