# PDF Reader Clipboard Translate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 PDF 原文阅读器的翻译面板中新增“粘贴并翻译”能力，同时保留现有手动粘贴翻译路径，并提供局部友好反馈。

**Architecture:** 仅改 `frontend-vue`，不改后端接口。核心做法是在 `PdfReader.vue` 内把“文本准备”和“翻译执行”拆开，新增剪贴板读取入口，并用局部状态承接失败提示。考虑到当前前端测试栈是 `node --test`，且现有组件测试主要是“源码结构断言 + 纯函数单测”模式，本次不引入新的 Vue 挂载测试栈，而是把可变逻辑下沉到可直接单测的纯 JS helper。

**Tech Stack:** Vue 3 `script setup`, node:test, Vite build

---

## File Map

- Modify: `frontend-vue/src/components/PdfReader.vue`
  - 新增 `粘贴并翻译` 按钮
  - 新增局部反馈状态
  - 抽出共享翻译执行逻辑
  - 新增剪贴板读取入口
- Modify: `frontend-vue/src/components/PdfReader.structure.test.js`
  - 断言翻译区新增按钮、反馈状态、说明文案和忙碌态接线
- Create: `frontend-vue/src/utils/pdfReaderClipboardTranslate.js`
  - 承载文本规范化、翻译 payload 构造、剪贴板错误分类、提示文案映射等纯逻辑
- Create: `frontend-vue/src/utils/pdfReaderClipboardTranslate.test.js`
  - 覆盖剪贴板成功、空文本、不支持、权限拒绝、未知失败等纯逻辑

## Task 0: 先确认约束和落点，避免计划从一开始就跑偏

**Files:**
- Read: `frontend-vue/package.json`
- Read: `frontend-vue/src/components/PdfReader.structure.test.js`
- Read: `frontend-vue/src/components/PdfReader.vue`

- [ ] **Step 1: 确认测试栈与文件定位**

确认以下事实并写进实现判断：

- `frontend-vue` 当前测试命令是 `node --test`
- 现有 `PdfReader.structure.test.js` 通过 `readFileSync()` + 正则断言源码结构
- 当前翻译请求实际调用是 `api.translate([manualText.value])`
- `package.json` 中是否存在额外的 `lint` / `typecheck` / `check` 脚本

- [ ] **Step 2: 用当前测试命令做一次基线确认**

Run:

```bash
cd frontend-vue && npm test -- src/components/PdfReader.structure.test.js
```

Expected:
- PASS
- 证明 `npm test -- <file>` 在当前 `node --test` 脚本下可用

- [ ] **Step 3: 锁定本次测试策略**

明确本次策略：

- `PdfReader.structure.test.js` 继续做源码结构断言
- 新增的逻辑测试放到纯 JS helper，而不是直接挂载 `.vue` 组件
- 不在本次需求里顺手引入 `@vue/test-utils`、`jsdom`、`happy-dom` 或新的测试 runner
- 如果 `package.json` 存在额外校验脚本，则在最终验证阶段补跑；如果不存在，则以“结构测试 + helper 单测 + build”作为收口

## Task 1: 收口 UI 结构和状态模型

**Files:**
- Modify: `frontend-vue/src/components/PdfReader.vue`
- Modify: `frontend-vue/src/components/PdfReader.structure.test.js`

- [ ] **Step 1: 先补结构测试，描述目标 UI**

在 `PdfReader.structure.test.js` 增加断言，至少覆盖：

```js
assert.match(source, /粘贴并翻译/)
assert.match(source, /clipboardFeedback/)
assert.match(source, /@click="pasteAndTranslate"/)
assert.match(source, /读取系统剪贴板内容，不是当前 PDF 划选内容/)
```

- [ ] **Step 2: 运行结构测试，确认先红**

Run:

```bash
cd frontend-vue && npm test -- src/components/PdfReader.structure.test.js
```

Expected:
- FAIL，因为按钮和反馈状态还不存在

- [ ] **Step 3: 在 `PdfReader.vue` 新增最小 UI 结构**

新增内容：
- `clipboardFeedback` 状态
- 翻译区内联反馈区域
- 第二个按钮：`粘贴并翻译`
- 剪贴板说明文案
- 两个按钮共享忙碌态的禁用接线

要求：
- 不删现有 `textarea`
- 不删现有 `翻译文本`
- 反馈区域位于翻译区内部，不用全局 toast
- 结构断言尽量锚定用户可见结构与关键 handler，不要把测试写成对实现细节过度耦合的正则集合

- [ ] **Step 4: 再跑结构测试，确认转绿**

Run:

```bash
cd frontend-vue && npm test -- src/components/PdfReader.structure.test.js
```

Expected:
- PASS

- [ ] **Step 5: 提交**

```bash
git add frontend-vue/src/components/PdfReader.vue frontend-vue/src/components/PdfReader.structure.test.js
git commit -m "feat: add pdf reader clipboard translate ui"
```

## Task 2: 抽纯逻辑 helper，给测试一个真正可执行的落点

**Files:**
- Create: `frontend-vue/src/utils/pdfReaderClipboardTranslate.js`
- Create: `frontend-vue/src/utils/pdfReaderClipboardTranslate.test.js`

- [ ] **Step 1: 先写 helper 测试，覆盖最核心的纯逻辑**

新增测试文件，先写最小失败用例，目标是把“剪贴板文本规范化 + 错误分类 + 文案映射”从组件里拆出来。至少先覆盖：

```js
test('normalizeClipboardText trims valid clipboard text and rejects whitespace-only content', () => {
  // '  copied text  ' => 'copied text'
  // '   \n\t' => invalid
})

test('buildTranslatePayload wraps normalized text into a single-element array', () => {
  // 'copied text' => ['copied text']
})

test('classifyClipboardFailure returns unsupported when clipboard api is unavailable', () => {
  // runtimeContext: { hasNavigator: false, hasClipboardApi: false, hasReadText: false, isSecureContext: false }
})

test('classifyClipboardFailure returns denied when readText throws NotAllowedError', () => {
  // map permission-style exceptions to denied feedback
})
```

- [ ] **Step 2: 运行新测试，确认先红**

Run:

```bash
cd frontend-vue && npm test -- src/utils/pdfReaderClipboardTranslate.test.js
```

Expected:
- FAIL，因为 helper 还不存在

- [ ] **Step 3: 创建 helper 并收口纯逻辑**

建议结构：
- `normalizeClipboardText(rawText)`
- `buildTranslatePayload(text)`
- `classifyClipboardFailure(error, runtimeContext)`
- `getClipboardFeedbackMessage(kind)`

约束：
- helper 保持纯函数，不直接访问组件状态
- helper 不直接调用 `api.translate`
- `runtimeContext` 由组件显式构造并传入 helper，推荐字段固定为：
  - `hasNavigator`
  - `hasClipboardApi`
  - `hasReadText`
  - `isSecureContext`
- helper 明确支持：
  - `trim()` 判空
  - 单条文本统一包装为 `[text]`
  - 通过 `runtimeContext` 判断以下运行时事实，而不是在 helper 内直接读取全局对象：
    - 当前是否存在 `navigator`
    - 当前是否存在 `navigator.clipboard`
    - 当前是否存在 `navigator.clipboard.readText`
    - 当前是否处于安全上下文
  - `NotAllowedError`
  - 未知异常回退文案

- [ ] **Step 4: 跑 helper 测试，确认纯逻辑转绿**

Run:

```bash
cd frontend-vue && npm test -- src/utils/pdfReaderClipboardTranslate.test.js
```

Expected:
- PASS

- [ ] **Step 5: 提交**

```bash
git add frontend-vue/src/utils/pdfReaderClipboardTranslate.js frontend-vue/src/utils/pdfReaderClipboardTranslate.test.js
git commit -m "refactor: add pdf reader clipboard helper logic"
```

## Task 3: 在组件中接共享翻译执行路径和剪贴板成功路径

**Files:**
- Modify: `frontend-vue/src/components/PdfReader.vue`
- Modify: `frontend-vue/src/components/PdfReader.structure.test.js`

- [ ] **Step 1: 先补结构断言，确认组件接上 helper 和新入口**

补 `PdfReader.structure.test.js` 断言，至少覆盖：

```js
assert.match(source, /@click="pasteAndTranslate"/)
assert.match(source, /manualText\.value\.trim\(\)\.length > 0|hasManualTranslateText/)
assert.match(source, /:disabled="!hasManualTranslateText \|\| isTranslating"|:disabled="!manualText\.trim\(\) \|\| isTranslating"/)
assert.match(source, /clipboardFeedback/)
```

- [ ] **Step 2: 运行测试，确认先红**

Run:

```bash
cd frontend-vue && npm test -- src/components/PdfReader.structure.test.js
```

Expected:
- FAIL，因为组件还未接入 helper / 新 handler

- [ ] **Step 3: 在组件中抽共享翻译执行函数并接入成功路径**

要求：
- 保留现有 `translateSelected()` 作为手动按钮入口，避免模板与现有命名大面积改动
- 新增 `runTranslation(text)` 作为共享翻译执行层
- `translateSelected()` 只负责读取当前 `manualText`，按同一规范化规则处理后，再委托给 `runTranslation(text)`
- 手动路径对空白文本的 UX 明确为：
  - 仅包含空白字符时，`翻译文本` 按钮保持禁用
  - 不额外弹错误提示
  - 与剪贴板路径共享同一套规范化规则，但不复用 `clipboardFeedback`
- `pasteAndTranslate()` 只在按钮点击时调用 `navigator.clipboard.readText()`
- 组件安全构造 `runtimeContext`，不要让 helper 直接读取全局 `window` / `navigator`
- 成功读取并规范化后：
  - 写入 `manualText`
  - 清理旧反馈
  - 调用 `runTranslation(text)`
- 与现有行为保持一致：
  - 继续用 helper 返回的单条 payload，再调用 `api.translate(payload)`
  - 成功后保留输入框文本，不再像旧实现那样在 finally 中强制清空
- 共享忙碌态的结束条件明确为：
  - 剪贴板读取失败时：在设置完 `clipboardFeedback` 后立即结束
  - 进入翻译后：在 `runTranslation(text)` 的 `finally` 中结束
- `isTranslating` 的归属规则明确为：
  - `translateSelected()` 不直接设置或清理 `isTranslating`
  - `runTranslation(text)` 负责手动翻译路径的 `isTranslating` 设置与清理
  - `pasteAndTranslate()` 在“开始读取剪贴板前”设置 `isTranslating = true`
  - 如果剪贴板路径在读取或规范化阶段提前失败，由 `pasteAndTranslate()` 自己负责清理 `isTranslating`
  - 如果剪贴板路径成功进入 `runTranslation(text)`，则后续只由 `runTranslation(text)` 的 `finally` 清理 `isTranslating`
  - 不允许 `pasteAndTranslate()` 与 `runTranslation(text)` 同时各自执行一层无条件 `finally` 来重复清理同一状态

- [ ] **Step 4: 再跑测试确认成功路径转绿**

Run:

```bash
cd frontend-vue && npm test -- src/components/PdfReader.structure.test.js
```

Expected:
- PASS

- [ ] **Step 5: 提交**

```bash
git add frontend-vue/src/components/PdfReader.vue frontend-vue/src/components/PdfReader.structure.test.js
git commit -m "refactor: share pdf reader translation execution"
```

## Task 4: 实现局部友好反馈和失败回退

**Files:**
- Modify: `frontend-vue/src/components/PdfReader.vue`
- Modify: `frontend-vue/src/utils/pdfReaderClipboardTranslate.test.js`

- [ ] **Step 1: 先补失败场景测试**

测试至少覆盖：

```js
test('normalizeClipboardText rejects whitespace-only clipboard content', () => {})
test('buildTranslatePayload returns a single-element array for valid text', () => {})
test('classifyClipboardFailure returns unsupported for missing navigator or insecure context', () => {})
test('classifyClipboardFailure returns denied for permission-style errors', () => {})
test('classifyClipboardFailure falls back to unknown for other exceptions', () => {})
test('getClipboardFeedbackMessage returns the agreed inline copy', () => {})
```

- [ ] **Step 2: 运行测试，确认先红**

Run:

```bash
cd frontend-vue && npm test -- src/utils/pdfReaderClipboardTranslate.test.js
```

Expected:
- FAIL，因为失败分支文案和状态还没补齐

- [ ] **Step 3: 实现局部友好反馈**

在 `PdfReader.vue` 实现：
- 剪贴板为空：`剪贴板里没有可翻译的文本`
- 不支持 API：`当前环境不支持一键读取剪贴板，请手动粘贴`
- 权限被拒绝：`浏览器不允许直接读取剪贴板，请手动粘贴`
- 未知异常：`读取剪贴板失败，请手动粘贴后再试`

约束：
- 不清空已有 `manualText`
- 不走全局 toast
- 翻译失败时继续沿用现有翻译失败展示
- 成功路径不新增 success feedback，只清理旧 `clipboardFeedback`
- `clipboardFeedback` 至少在以下时机清理：
  - 成功读取剪贴板后
  - 再次点击任一翻译入口前
  - 用户修改输入框内容时
- 共享忙碌态的恢复时机必须与 Task 3 中定义的结束条件保持一致，避免按钮卡死

- [ ] **Step 4: 跑测试确认失败分支转绿**

Run:

```bash
cd frontend-vue && npm test -- src/utils/pdfReaderClipboardTranslate.test.js
```

Expected:
- PASS

- [ ] **Step 5: 提交**

```bash
git add frontend-vue/src/components/PdfReader.vue frontend-vue/src/utils/pdfReaderClipboardTranslate.test.js frontend-vue/src/utils/pdfReaderClipboardTranslate.js
git commit -m "feat: add pdf reader clipboard translate feedback"
```

## Task 5: 做整体验证并收口

**Files:**
- Modify if needed: `frontend-vue/src/components/PdfReader.vue`
- Modify if needed: `frontend-vue/src/components/PdfReader.structure.test.js`
- Modify if needed: `frontend-vue/src/utils/pdfReaderClipboardTranslate.test.js`
- Modify if needed: `frontend-vue/src/utils/pdfReaderClipboardTranslate.js`

- [ ] **Step 1: 跑前端相关测试**

Run:

```bash
cd frontend-vue && npm test -- \
  src/components/PdfReader.structure.test.js \
  src/utils/pdfReaderClipboardTranslate.test.js
```

Expected:
- PASS

- [ ] **Step 2: 跑完整前端 build**

Run:

```bash
cd frontend-vue && npm run build
```

Expected:
- `vite build` 成功

- [ ] **Step 2.5: 如果存在额外校验脚本，则补跑**

Run if available:

```bash
cd frontend-vue && npm run lint
cd frontend-vue && npm run typecheck
```

Expected:
- 如果脚本存在，则应通过
- 如果脚本不存在，则在收口说明中明确“当前前端无额外 lint/typecheck 脚本”

- [ ] **Step 3: 手工联调检查**

至少手工验证：
- 复制英文文本后点击 `粘贴并翻译`，能直接出结果
- 手动粘贴后点击 `翻译文本`，旧路径不受影响
- 权限失败时，翻译区内部能看到友好提示
- 翻译失败后，输入框文本仍保留
- `粘贴并翻译` 按钮附近能看到“读取系统剪贴板内容，不是当前 PDF 划选内容”的说明
- 点击 `粘贴并翻译` 后，两个按钮会立即进入共享禁用态，直到本轮流程结束

- [ ] **Step 4: 处理最后的小修**

仅允许修复：
- 文案
- 按钮禁用态
- 局部反馈显示位置
- helper 中的错误分类边界
- 结构测试脆弱性

不允许顺手做无关视觉重构。

- [ ] **Step 5: 最终提交**

```bash
git add frontend-vue/src/components/PdfReader.vue frontend-vue/src/components/PdfReader.structure.test.js frontend-vue/src/utils/pdfReaderClipboardTranslate.js frontend-vue/src/utils/pdfReaderClipboardTranslate.test.js
git commit -m "feat: add pdf reader paste and translate flow"
```
