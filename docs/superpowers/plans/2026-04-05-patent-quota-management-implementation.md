# Patent Quota Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 patent 相关能力正式纳入现有 quota 管理，确保 `patent` ask/ask_stream 成功时真实计额、失败时不误扣，并保持前后端继续只使用 4 个 canonical quota bucket。

**Architecture:** 保持 `public-service` 作为唯一 quota authority，`gateway` 作为 QA ask 主链的 quota orchestrator，`patent` backend 只负责业务执行。实现时优先沿用现有 canonical alias、sync `quota` 注入、SSE `done` 终态注入等既有机制，只在发现 patent 入口没有穿过现有归一化路径时做最小修补。

**Tech Stack:** FastAPI, Python, pytest, gateway proxy layer, public-service quota service, Vue 3, Vite

---

## Source Documents

- Spec: `docs/superpowers/specs/2026-04-05-patent-quota-management-design.md`
- Related current implementation:
  - `gateway/app/routers/qa.py`
  - `public-service/backend/app/modules/quota/service.py`
  - `public-service/backend/app/modules/documents/api.py`
  - `frontend-vue/src/services/quota-normalization.js`

## File Map

### Gateway

- Modify: `gateway/app/routers/qa.py`
- Test: `gateway/tests/test_qa_proxy.py`

### Public-Service

- Test: `public-service/backend/tests/test_quota_module.py`
- Test: `public-service/backend/tests/test_documents_module.py`
- Test: `public-service/backend/tests/test_conversation_module.py`
- Modify only if regression proves a real gap:
  - `public-service/backend/app/modules/quota/service.py`
  - `public-service/backend/app/modules/documents/api.py`
  - `public-service/backend/app/modules/conversation/api.py`

### Frontend

- Test or modify only if regression proves a real gap:
  - `frontend-vue/src/services/quota-normalization.js`
- Verify build:
  - `frontend-vue`

---

## Lock Decisions

1. 不新增 patent 专属 quota type。
2. `patent kb_qa -> ask_query`。
3. `patent pdf_qa/tabular_qa/hybrid_qa -> file_qa`。
4. patent 原文继续归 `file_view`。
5. patent 文档辅助相关入口继续归 `doc_assist`。
6. sync ask 继续复用 `gateway/app/routers/qa.py` 里的 `_with_sync_quota_payload()` 注入 `quota` 字段。
7. stream ask 继续复用 `gateway/app/routers/qa.py` 里的 `_stream_with_quota()`，只有捕获到合法 `type=done` 事件才允许 `finalize(success=true)`。
8. `public-service` 优先做回归测试，不预设必须改代码；只有发现 patent 入口没有走既有 canonical 归一化时才补最小代码。

---

### Task 1: 接通 Gateway 对 Patent Ask 的 Quota 分类

**Files:**
- Modify: `gateway/app/routers/qa.py`
- Test: `gateway/tests/test_qa_proxy.py`

- [x] **Step 1: 先写失败测试，锁定 patent route 到 canonical quota 的映射**

在 `gateway/tests/test_qa_proxy.py` 增加用例，覆盖：
- patent `kb_qa` sync 请求调用 `precheck(..., quota_type="ask_query")`
- patent `pdf_qa` sync 请求调用 `precheck(..., quota_type="file_qa")`
- patent `tabular_qa` sync 请求调用 `precheck(..., quota_type="file_qa")`
- patent `hybrid_qa` sync 请求调用 `precheck(..., quota_type="file_qa")`
- patent `kb_qa` stream 请求调用 `precheck(..., quota_type="ask_query")`
- patent 文件类 stream 请求调用 `precheck(..., quota_type="file_qa")`
- patent sync 请求在 `user_id` 缺失、空值或非正整数时不会调用 `precheck`
- patent stream 请求在 `user_id` 缺失、空值或非正整数时不会调用 `precheck`

建议直接复用当前 fast/thinking quota proxy 测试夹具，断言 `QuotaProxyService.precheck()` 的入参，而不是新造 patent 专用测试基架。

- [x] **Step 2: 运行 focused gateway 测试，确认当前实现确实失败**

Run: `pytest gateway/tests/test_qa_proxy.py -q -k quota`

Expected:
- 至少有 patent 相关用例失败
- 失败原因应明确指向当前 `_quota_type_for_route()` 对 patent 返回 `None`

- [x] **Step 3: 修改 `gateway/app/routers/qa.py` 的 quota 分类函数**

实现要求：
- 删除“只要是 patent 就直接不计额”的特殊排除
- 基于现有 `route_decision.route` 做 canonical quota 映射
- 保持 fast/thinking 当前逻辑不变
- 不在 `patent` backend 内部重复推导 quota type

推荐实现形态：

```python
def _quota_type_for_route(route_decision) -> str | None:
    route = str(route_decision.route or "").strip().lower()
    if route in {"pdf_qa", "tabular_qa", "hybrid_qa"}:
        return "file_qa"
    if route == "kb_qa":
        return "ask_query"
    return "file_qa" if route in _FILE_ROUTES else "ask_query"
```

如果保留现有 fallback，必须保证 patent 也能命中 `kb_qa -> ask_query`、文件 route -> `file_qa`。

- [x] **Step 4: 重跑 focused gateway 测试，确认 patent 已进入 quota precheck**

Run: `pytest gateway/tests/test_qa_proxy.py -q -k quota`

Expected:
- PASS
- patent sync / stream 都会触发 `precheck`

- [ ] **Step 5: Commit**

```bash
git add gateway/app/routers/qa.py gateway/tests/test_qa_proxy.py
git commit -m "feat: meter patent ask routes through gateway quota buckets"
```

### Task 2: 锁定 Patent Sync / Stream 的 Success-Only 记账语义

**Files:**
- Modify: `gateway/app/routers/qa.py`
- Test: `gateway/tests/test_qa_proxy.py`

- [x] **Step 1: 补写失败测试，覆盖 patent 的 finalize / abort 语义**

在 `gateway/tests/test_qa_proxy.py` 增加用例，覆盖：
- patent sync 成功响应时调用 `finalize(success=True)`
- patent sync `success=false` 时不计额，并释放 grant
- patent sync 响应包含非空 `error` 时不计额，并释放 grant
- patent stream 在存在合法 `done` 事件时调用 `finalize(success=True)`
- patent stream 上游异常、HTTP 错误、SSE error、流提前结束且没有 `done` 时不计额
- patent file gate off 时直接返回 gated 错误，不消耗 quota
- patent `kb_qa` 在 patent file gate off 时仍可继续走正常 ask 链路
- finalize 失败时：
  - sync 仍返回业务成功 payload，并在 `quota` 字段里带 warning
  - stream 仍输出业务 `done`，并把 `quota` 挂到 `done` 事件上
- 至少各有一个正例明确断言用户可见结果：
  - patent `kb_qa` sync 成功响应里 `quota.quota_type == "ask_query"`
  - patent 文件类 sync 成功响应里 `quota.quota_type == "file_qa"`
  - patent `kb_qa` stream `done` 事件里 `quota.quota_type == "ask_query"`
  - patent 文件类 stream `done` 事件里 `quota.quota_type == "file_qa"`

建议复用当前 `_with_sync_quota_payload()` 和 `_stream_with_quota()` 的既有断言模式，不为 patent 发明新的返回 shape。

- [x] **Step 2: 运行 targeted patent stream/sync 测试，确认失败点准确**

Run: `pytest gateway/tests/test_qa_proxy.py -q -k "patent and (stream or quota or finalize)"`

Expected:
- 允许部分新用例在 Task 1 完成后已经转绿
- 剩余失败点只应集中在 patent 路由尚未走进 generic quota helper 的路径上，或 patent-specific regression 尚未补齐

- [x] **Step 3: 最小修改 gateway ask 主链，复用现有 generic quota helper**

要求：
- sync ask 继续走 `_should_count_sync_response()` + `_with_sync_quota_payload()`
- stream ask 继续走 `_stream_with_quota()`
- 不要为 patent 复制一套 `_proxy_ask_patent()` 或 `_stream_with_quota_patent()`
- patent file gate 和 upstream error 必须仍然在 finalize 前返回或 abort

- [x] **Step 4: 重跑 gateway 测试，确认 patent 成功计额、失败不计额**

Run: `pytest gateway/tests/test_qa_proxy.py -q`

Expected:
- PASS
- patent/fast/thinking 的 quota helper 仍共用同一套逻辑

- [ ] **Step 5: Commit**

```bash
git add gateway/app/routers/qa.py gateway/tests/test_qa_proxy.py
git commit -m "test: lock patent quota finalize behavior"
```

### Task 3: 做 Public-Service 回归覆盖，并仅在发现缺口时补最小代码

**Files:**
- Test: `public-service/backend/tests/test_quota_module.py`
- Test: `public-service/backend/tests/test_documents_module.py`
- Test: `public-service/backend/tests/test_conversation_module.py`
- Modify only if needed:
  - `public-service/backend/app/modules/quota/service.py`
  - `public-service/backend/app/modules/documents/api.py`
  - `public-service/backend/app/modules/conversation/api.py`

- [x] **Step 1: 先写回归测试，证明现有 canonical alias 和 patent 入口仍然成立**

至少补齐以下断言：
- `normalize_quota_type("kb_qa") == "ask_query"`
- `normalize_quota_type("pdf_qa") == "file_qa"`
- `normalize_quota_type("tabular_qa") == "file_qa"`
- `normalize_quota_type("hybrid_qa") == "file_qa"`
- patent original 路由仍使用 `require_quota("file_view")`
- conversation 文件下载仍使用 `require_quota("file_view")`
- patent 文献辅助 / 文档辅助入口仍使用 `doc_assist`
- `get_user_quotas` / config list 不会额外暴露 patent bucket

- [x] **Step 2: 运行 public-service focused 测试，判断是否只需测试补强**

Run: `pytest public-service/backend/tests/test_quota_module.py public-service/backend/tests/test_documents_module.py public-service/backend/tests/test_conversation_module.py -q`

Expected:
- 最理想情况是只需新增测试即可通过
- 如果失败，失败点必须明确证明某个 patent 入口没有走既有 canonical 路径

- [x] **Step 3: 若 regression 暴露真实缺口，再做最小代码补丁**

允许的最小补丁范围：
- 在 `quota/service.py` 补 patent 相关 alias 归一化，前提是测试证明现有 alias 未覆盖
- 在 `documents/api.py` 或 `conversation/api.py` 补 patent 入口的 quota dependency，前提是测试证明该入口未接到 `file_view` / `doc_assist`

不允许：
- 新增 `patent_qa`、`patent_file_qa` 等 canonical 类型
- 为 patent 单独复制一套 quota service
- 改动与本轮无关的 admin/user quota API 结构

- [x] **Step 4: 重跑 public-service focused 测试**

Run: `pytest public-service/backend/tests/test_quota_module.py public-service/backend/tests/test_documents_module.py public-service/backend/tests/test_conversation_module.py -q`

Expected:
- PASS
- patent 相关 public-service 入口的归属边界不变

- [ ] **Step 5: Commit**

```bash
git add public-service/backend/tests/test_quota_module.py public-service/backend/tests/test_documents_module.py public-service/backend/tests/test_conversation_module.py public-service/backend/app/modules/quota/service.py public-service/backend/app/modules/documents/api.py public-service/backend/app/modules/conversation/api.py
git commit -m "test: cover patent quota mappings and document entrypoints"
```

### Task 4: 验证 Frontend Canonical Quota 模型不扩桶

**Files:**
- Test or modify only if needed: `frontend-vue/src/services/quota-normalization.js`
- Verify build: `frontend-vue`

- [x] **Step 1: 检查并补充前端 quota 归一化断言**

确认 `frontend-vue/src/services/quota-normalization.js` 仍满足：
- `kb_qa -> ask_query`
- `pdf_qa/tabular_qa/hybrid_qa -> file_qa`
- quota 列表 canonical order 仍然只有 4 个 bucket

如果仓库里已有对应测试位置，就补测试；如果没有现成测试 harness，可以在本任务只做代码核对 + build 验证，不凭空引入一整套前端测试框架。

- [x] **Step 2: 仅在发现缺口时做最小修改**

只允许的修改：
- 补遗漏 alias
- 修正 canonical label / sort 行为

不允许：
- 新增 patent 专属 quota 卡片
- 新增 patent 专属 quota type

- [x] **Step 3: 运行前端构建**

Run: `cd frontend-vue && npm run build`

Expected:
- PASS
- quota 页面仍正常编译

- [ ] **Step 4: Commit**

```bash
git add frontend-vue/src/services/quota-normalization.js frontend-vue/package.json frontend-vue/package-lock.json
git commit -m "test: verify frontend patent quota normalization"
```

如果没有改到某些文件，就不要把它们加进 commit。

### Task 5: 总体验证与复审收口

**Files:**
- Test: `gateway/tests/test_qa_proxy.py`
- Test: `public-service/backend/tests/test_quota_module.py`
- Test: `public-service/backend/tests/test_documents_module.py`
- Test: `public-service/backend/tests/test_conversation_module.py`
- Verify: `frontend-vue`
- Docs: `docs/superpowers/specs/2026-04-05-patent-quota-management-design.md`

- [x] **Step 1: 运行最终验证命令**

Run:

```bash
pytest gateway/tests/test_qa_proxy.py -q
pytest public-service/backend/tests/test_quota_module.py public-service/backend/tests/test_documents_module.py public-service/backend/tests/test_conversation_module.py -q
cd frontend-vue && npm run build
```

Expected:
- 所有 targeted tests PASS
- 前端 build PASS

- [x] **Step 2: 核对业务验收标准**

逐项确认：
- patent `kb_qa` 成功请求真实消耗 `ask_query`
- patent 文件类请求真实消耗 `file_qa`
- patent ask 失败请求不会误扣 quota
- patent 原文仍归 `file_view`
- patent 文档辅助仍归 `doc_assist`
- 前后端没有出现新的 patent quota bucket
- 至少保留一组能证明“不是空壳”的证据：
  - 一个 sync 成功用例同时证明响应里出现正确 canonical `quota.quota_type`
  - 一个 stream 成功用例同时证明 `done` 事件里出现正确 canonical `quota.quota_type`
  - 一个失败用例证明没有 finalize / 没有 usage 增量或 grant 被释放

- [x] **Step 2.5: 明确禁止以空壳实现收尾**

实现收尾前必须再次核对：
- 不能只加 alias、枚举、前端展示或文档
- 不能只让测试 mock 出 `quota` 字段而真实 ask 链路仍不走 `precheck/finalize/abort`
- 不能只验证内部 helper 被调用，而没有任何用户可见结果或记账结果的回归证据
- 如果最终证据不足以证明“成功真扣、失败不扣”，不得宣称功能完成

- [x] **Step 3: 发 code review / implementation review**

把本次改动与 spec 一起发 reviewer，要求重点检查：
- patent ask 是否真的接进 quota 闭环
- 有没有只补 UI / alias 但没接真实计额
- 有没有偷偷引入第二套 quota 类型
- 有没有用 mock/占位通过测试，但缺少真实闭环证据

- [ ] **Step 4: Commit**

```bash
git add gateway public-service frontend-vue docs/superpowers/specs/2026-04-05-patent-quota-management-design.md
git commit -m "feat: bring patent capabilities under canonical quota management"
```

只提交本任务相关改动；如果工作区还有别的脏文件，必须显式排除。
