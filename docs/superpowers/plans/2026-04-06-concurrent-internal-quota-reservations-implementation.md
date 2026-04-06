# Concurrent Internal Quota Reservations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `public-service` 的 internal quota grant 从“同 bucket 互斥锁”改成“同 bucket 并行预占”，修复 patentQA 进行中再问 fastQA 被 quota 错拦的问题，并保留真实的成功记额/失败释放语义。

**Architecture:** 核心改动集中在 `public-service` 的 quota service：用短生命周期 reservation decision lock 保证 precheck 原子性，用 pending grant 记录表达活跃预占，并把 finalize 记账锚定到 precheck 时锁定的 `period_key`。`gateway` 继续复用现有 `precheck -> finalize` 编排，只补回归测试验证并行放开与 finalize `NOT_FOUND` warning 契约。

**Tech Stack:** FastAPI, Python, pytest, Redis/file fallback grant persistence, gateway proxy quota orchestration

---

## Source Documents

- Spec: `docs/superpowers/specs/2026-04-06-concurrent-internal-quota-reservations-design.md`
- Current implementation:
  - `public-service/backend/app/modules/quota/service.py`
  - `public-service/backend/tests/test_quota_module.py`
  - `gateway/tests/test_qa_proxy.py`

## File Map

### Public-Service

- Modify: `public-service/backend/app/modules/quota/service.py`
- Test: `public-service/backend/tests/test_quota_module.py`

### Gateway

- Test: `gateway/tests/test_qa_proxy.py`
- Modify only if regression proves a real gap:
  - `gateway/app/routers/qa.py`

### Verification Only

- Verify: `frontend-vue/src/services/quota-normalization.test.js`
- Verify: `frontend-vue`

---

## Lock Decisions

1. 只改 `internal quota grant` 链路，不改 `require_quota()` 同步依赖。
2. 同 bucket 活跃 grant 允许并存，reservation 判定按 `completed usage + active pending reservations`。
3. reservation 统计粒度是 `user_id + canonical quota_type + period_key`。
4. precheck 必须使用短生命周期原子判定锁；不再保留旧的整请求期互斥锁语义。
5. `noop=true` grant 不占 reservation 名额，也不参与 active pending 统计。
6. `finalize(success=true)` 必须记到 grant 创建时锁定的 `period_key`。
7. reservation 占满时直接返回 `QUOTA_EXCEEDED`，不等待旧 grant 释放。
8. `GRANT_ALREADY_ACTIVE` 从正常业务路径移除，只允许保留给异常态。
9. 若业务成功但 finalize 因 reservation 过期返回 `NOT_FOUND`，gateway 保留业务成功结果并附带 quota warning，不补记 usage。
10. 不能以错误码包装、mock 回归或只改文档/前端的方式收尾，必须留下 service 层和 gateway 层的真实证据。

---

### Task 1: 先把 Public-Service 的新语义用红灯测试锁死

**Files:**
- Test: `public-service/backend/tests/test_quota_module.py`

- [ ] **Step 1: 为同 bucket 并行 grant 成功补写失败测试**

新增 service 层用例，至少覆盖：
- 同一用户、同一 `ask_query` bucket、limit 足够时，两个 grant 可同时创建成功
- 同一用户、同一 `file_qa` bucket、limit 足够时，两个 grant 可同时创建成功
- `noop=true` grant 不占 reservation 名额，不能挡住普通用户 grant

- [ ] **Step 2: 为 reservation 占满与立即失败补写失败测试**

新增 service 层用例，至少覆盖：
- limit 为 1 时，第一个 grant 成功，第二个同 bucket grant 立即返回 `QUOTA_EXCEEDED`
- 不再返回 `GRANT_ALREADY_ACTIVE`
- 不再出现“等待旧 grant 释放后再成功”的旧语义

- [ ] **Step 2.5: 显式改写旧互斥语义测试，不允许只删除**

必须把当前编码了“长期互斥 lease”前提的旧测试改写成新 reservation 语义，至少包括：
- `test_service_create_internal_quota_grant_rejects_overlapping_active_grants`
- `test_service_create_internal_quota_grant_waits_for_active_grant_release`
- `test_service_internal_quota_grant_keeps_lease_alive_past_ttl`
- `test_service_cleanup_pending_internal_quota_grants_releases_redis_lease`

改写要求：
- 不允许简单删掉这些测试
- 必须把断言改成 reservation 占满、立即失败、过期后不再占 reservation、cleanup 清理 pending reservation 的新语义
- 通过测试名、断言和注释都明确旧的“长期互斥 lease”已经退场

- [ ] **Step 3: 为 anchored period 和 finalize 语义补写失败测试**

新增 service 层用例，至少覆盖：
- grant 创建时锁定 `period/period_days/period_key`
- `finalize(success=true)` 记账落到 grant 锚定的 `period_key`
- 两个并行 grant 都成功 finalize 时，总 usage 增量正确且不超卖
- 一个成功一个失败时，只增加 1 次 usage
- 重复 finalize 保持幂等，不重复记账

- [ ] **Step 4: 为 TTL / renewer 丢失语义补写失败测试**

新增 service 层用例，至少覆盖：
- reservation 已失效时，`finalize(success=true)` 返回 `NOT_FOUND`
- 不补记 usage
- cleanup / 过期后不再继续占 reservation

- [ ] **Step 5: 跑 focused public-service 测试，确认当前实现按旧互斥语义失败**

Run:
```bash
conda run --no-capture-output -n agent pytest -q public-service/backend/tests/test_quota_module.py -k "internal_quota_grant or reservation" -p no:cacheprovider
```

Expected:
- 新增用例至少部分失败
- 失败点集中在当前互斥锁模型、旧的 `GRANT_ALREADY_ACTIVE` 语义、未锚定旧 `period_key`

- [ ] **Step 6: Commit**

```bash
git add public-service/backend/tests/test_quota_module.py
git commit -m "test: lock concurrent internal quota reservation semantics"
```

### Task 2: 实现 Public-Service 的并行预占核心

**Files:**
- Modify: `public-service/backend/app/modules/quota/service.py`
- Test: `public-service/backend/tests/test_quota_module.py`

- [ ] **Step 1: 新增 reservation 决策辅助结构和短锁 helper**

实现要求：
- 在 `QuotaService` 内新增短生命周期 reservation decision lock helper
- 锁粒度为 `user_id + canonical quota_type + period_key`
- Redis 可用时走 Redis 锁；fallback 场景保留 file-based 锁
- 这把锁只覆盖 precheck 的原子判定，不覆盖整个请求执行期

- [ ] **Step 1.5: 明确退役旧的长期互斥 lease helper**

实现要求：
- 当前 `create_internal_quota_grant()` 经过的 `_acquire_internal_quota_grant_lease()` / `_release_internal_quota_grant_lease()` / renewer keepalive 路径，不能继续承担“整请求期排他锁”的职责
- 必须二选一并在代码里写死：
  - 删除这些旧 helper，并用新的短锁 helper 替代
  - 或把这些旧 helper 改造成仅服务于 reservation decision 的短锁 helper，名字和调用语义同步收敛
- 不允许保留“创建 grant 后一直持有 user+quota_type 排他锁直到 finalize”的旧行为
- pending grant payload 不得继续依赖旧的 `lease` 字段表达“长期互斥执行权”
- cleanup 路径必须同步改成清理 pending reservation 记录，而不是释放旧的长期互斥 lease

- [ ] **Step 2: 新增 active pending reservation 统计 helper**

实现要求：
- 基于现有 pending grant 持久化记录统计 active reservations
- 只统计满足 spec 条件的 grant：非 `noop`、`config_active=true`、pending、未过期、同 `user_id + quota_type + period_key`
- 不允许通过单个长寿命 grant 锁来伪装成 reservation 数

- [ ] **Step 3: 扩展 pending grant payload，真实持久化 reservation 元数据**

实现要求：
- 在 pending grant payload 中写入：
  - `user_id`
  - `quota_type`
  - `noop`
  - `config_active`
  - `period`
  - `period_days`
  - `period_key`
  - `reserved_at`
- Redis 和 file fallback 都必须持久化到 grant 记录，不允许只存局部变量

- [ ] **Step 4: 改写 `create_internal_quota_grant()` 为 reservation 语义**

实现要求：
- 先解析 quota config / limit / primary window
- 在短锁内做：
  - 读取 completed usage
  - 统计 active pending reservations
  - 计算 `effective_used`
  - 若占满则返回 `QUOTA_EXCEEDED`
  - 否则创建新的 pending grant
- 移除旧的“同 bucket 一把长期互斥锁”主路径
- 正常路径不再返回 `GRANT_ALREADY_ACTIVE`
- 若旧 helper 被保留为短锁实现，必须证明它们的生命周期只覆盖 precheck 原子判定，不会跨到 ask 执行期与 finalize 之间

- [ ] **Step 5: 扩展 `increment_quota()` 支持 anchored window**

实现要求：
- 保持 repository 合同不变
- 在 `QuotaService.increment_quota()` 增加仅供内部 finalize 使用的 anchored window 参数
- 传 anchored window 时，必须把 usage 写入指定 `period_key`
- 不传时保持现有行为，避免影响非 internal-grant 入口

- [ ] **Step 6: 改写 `finalize_internal_quota_grant()`**

实现要求：
- 成功路径用 grant 锚定的 `period_key` 记账
- 失败路径只释放 reservation，不记 usage
- finalize 仍然幂等
- grant 过期 / renewer 丢失后返回 `NOT_FOUND`，不补记 usage
- cleanup 和 pending grant 删除语义保持一致
- 不再释放或续租任何“整请求期 user+quota_type 排他 lease”

- [ ] **Step 7: 重跑 focused public-service 测试，确认新语义转绿**

Run:
```bash
conda run --no-capture-output -n agent pytest -q public-service/backend/tests/test_quota_module.py -k "internal_quota_grant or reservation" -p no:cacheprovider
```

Expected:
- 新增 reservation 语义测试 PASS
- 旧互斥语义测试已被新语义测试替换，不靠删除覆盖空窗
- 不再有任何测试依赖“第二个 grant 必须等待第一个 finalize 才能创建成功”的旧前提

- [ ] **Step 8: Commit**

```bash
git add public-service/backend/app/modules/quota/service.py public-service/backend/tests/test_quota_module.py
git commit -m "feat: add concurrent internal quota reservations"
```

### Task 3: 补 Gateway 真实闭环回归，证明不是空壳

**Files:**
- Test: `gateway/tests/test_qa_proxy.py`
- Modify only if needed: `gateway/app/routers/qa.py`

- [ ] **Step 1: 先写 gateway 集成回归测试**

在 `gateway/tests/test_qa_proxy.py` 增加用例，至少覆盖：
- `patent kb_qa` 进行中时，再发一个 `fastQA`，第二个 precheck 不会因 active grant 冲突失败
- 两个 ask 都能得到各自正常业务结果
- 若 finalize 返回 `NOT_FOUND`，sync 仍保留业务成功响应并在 `quota` 中带 warning
- 若 stream 的 `done` finalize 返回 `NOT_FOUND`，仍保留 `done` 事件并附带 warning

- [ ] **Step 2: 跑 focused gateway 测试，确认现有 mock/transport 夹具能表达新语义**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_qa_proxy.py -k "patent and quota" -p no:cacheprovider
```

Expected:
- 新增用例若失败，失败点只指向 gateway 对新 internal-grant 结果的兼容缺口

- [ ] **Step 3: 仅在测试暴露真实缺口时做最小 gateway 代码补丁**

允许的最小补丁：
- 保持成功业务响应 + quota warning 的现有契约
- 保持 stream `done` 事件注入 warning 的现有契约

不允许：
- 在 gateway 里吞掉 `public-service` 的 reservation 语义差异来伪造通过
- 在 gateway 重试新 grant 来补记 usage

- [ ] **Step 4: 重跑 gateway 全文件测试**

Run:
```bash
conda run --no-capture-output -n agent pytest -q gateway/tests/test_qa_proxy.py -p no:cacheprovider
```

Expected:
- PASS
- patent / fast / thinking 现有 quota 注入行为不回归

- [ ] **Step 5: Commit**

```bash
git add gateway/tests/test_qa_proxy.py gateway/app/routers/qa.py
git commit -m "test: cover gateway concurrent quota reservation behavior"
```

如果没有改到 `gateway/app/routers/qa.py`，不要把它加入 commit。

### Task 4: 总体验证与审查收口

**Files:**
- Test: `public-service/backend/tests/test_quota_module.py`
- Test: `gateway/tests/test_qa_proxy.py`
- Verify: `frontend-vue/src/services/quota-normalization.test.js`
- Verify: `frontend-vue`
- Docs: `docs/superpowers/specs/2026-04-06-concurrent-internal-quota-reservations-design.md`

- [ ] **Step 1: 跑最终验证命令**

Run:
```bash
conda run --no-capture-output -n agent pytest -q public-service/backend/tests/test_quota_module.py -p no:cacheprovider
conda run --no-capture-output -n agent pytest -q gateway/tests/test_qa_proxy.py -p no:cacheprovider
node --test frontend-vue/src/services/quota-normalization.test.js
cd frontend-vue && npm run build
```

Expected:
- targeted tests PASS
- 前端验证 PASS

- [ ] **Step 2: 核对验收标准**

逐项确认：
- patentQA 进行中时，fastQA 不再被 active grant 错拦
- 同 bucket 并行请求不会超卖 quota
- limit 被 reservation 占满时返回 `QUOTA_EXCEEDED`
- `noop` grant 不占 reservation
- finalize 记账落在 precheck 锁定的 `period_key`
- finalize `NOT_FOUND` 的成功业务结果仍保留，并带 quota warning
- 没有通过错误码包装或 mock 假成功来糊弄

- [ ] **Step 2.5: 明确禁止以空壳实现收尾**

实现收尾前必须再次核对：
- 不能只改 `GRANT_ALREADY_ACTIVE -> QUOTA_EXCEEDED` 映射
- 不能只改 gateway 包装逻辑而不改 `public-service` reservation 判定
- 不能只删旧测试，不补新语义回归
- 如果缺少 service 层与 gateway 层的真实回归证据，不得宣称完成

- [ ] **Step 3: 发 code review / plan-conformity review**

要求 reviewer 重点检查：
- reservation 是否真实存在于 pending grant 持久化记录
- precheck 是否真的按 `completed + pending` 原子判定
- finalize 是否真实写入锚定 `period_key`
- 有没有用错误码包装、mock、删除旧测试等方式做空壳实现

- [ ] **Step 4: Commit**

```bash
git add public-service gateway docs/superpowers/specs/2026-04-06-concurrent-internal-quota-reservations-design.md
git commit -m "feat: allow concurrent internal quota reservations"
```

只提交本任务相关改动；如果工作区还有别的脏文件，必须显式排除。
