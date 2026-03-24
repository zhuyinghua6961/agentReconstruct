# highThinkingQA Authority Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 highThinkingQA 普通 thinking QA 的聊天记录持久化与上下文读取切换到 public-service authority，移除 gateway 在 thinking 路径上的代理式持久化，保持流式输出体验平滑。

**Architecture:** 复用 fastQA 已落地的 authority client + chat persistence adapter + pending overlay 模式。gateway 继续负责分发，但对 thinking 路径不再代写会话；highThinkingQA 在 ask 前置 user write、执行前 authority snapshot read、done 后 assistant async accept；public-service 继续作为权威会话落盘与最终一致性收敛点。

**Tech Stack:** FastAPI, httpx, Redis overlay, public-service internal authority API, pytest, existing ordered dispatcher.

---

## File Map

### New files

- `highThinkingQA/server/services/conversation_authority_client.py`
  - highThinkingQA -> public-service internal authority client
- `highThinkingQA/server/services/chat_persistence.py`
  - authority-aware user write / context read / assistant async accept / overlay adapter
- `highThinkingQA/tests/test_conversation_authority_client.py`
  - authority client contract tests
- `highThinkingQA/tests/test_chat_persistence.py`
  - adapter tests, overlay tests, sync/async tests

### Modified files

- `highThinkingQA/server/services/conversation_context_service.py`
  - 将上下文读取切换为 authority snapshot + request chat_history merge
- `highThinkingQA/server_fastapi/routers/ask.py`
  - 将本地 persistence helper 替换为 authority adapter hook
- `highThinkingQA/server_fastapi/app.py`
  - 注册 authority client 生命周期关闭 hook（如需要）
- `highThinkingQA/config.py`
  - 确保 rollout/public-service base url/token/overlay 设置真正进入运行时
- `highThinkingQA/tests/test_env_loader.py`
  - rollout / authority config 解析测试
- `highThinkingQA/tests/test_ask_router_summary_persistence.py`
  - ask router 持久化行为从本地 conversation_service 切为 adapter
- `highThinkingQA/tests/test_ask_service_executor.py`
  - 保证 ask 执行链在 authority snapshot 语义下不退化
- `gateway/app/routers/qa.py`
  - 对 thinking 路径移除 gateway 代理持久化；fast 路径维持现状
- `gateway/tests/test_qa_proxy.py`
  - 验证 thinking 请求不再由 gateway 写会话，fast 请求仍保留当前行为

### Potentially touched only if needed

- `highThinkingQA/server/services/__init__.py`
- `highThinkingQA/tests/test_env_loader.py`
- `public-service/backend/tests/test_conversation_authority_integration.py`
  - 仅在 highThinkingQA authority client 需要补 integration coverage 时添加，不先作为默认改动点

---

## Implementation Strategy

- 先做 `highThinkingQA` 自身 authority client / adapter / context read，保持最小侵入。
- 再切 ask router 的 user write / assistant accept。
- 最后收 gateway：thinking 路径不再代理持久化，避免重复写入。
- overlay 和 async accept 必须与主链一起切，不要留出“assistant accepted 但下一轮读不到”的窗口。
- 不在这轮里收缩 highThinkingQA 的 conversation/upload 路由，只聚焦 ask 主链路 authority 化。

---

### Task 1: Add highThinkingQA Authority Client

**Files:**
- Create: `highThinkingQA/server/services/conversation_authority_client.py`
- Test: `highThinkingQA/tests/test_conversation_authority_client.py`
- Reference: `fastQA/app/services/conversation_authority_client.py`

- [ ] **Step 1: Write the failing authority client contract tests**

```python
# highThinkingQA/tests/test_conversation_authority_client.py

def test_write_user_turn_uses_highthinking_contract():
    ...

def test_read_context_snapshot_uses_thinking_mode_contract():
    ...

def test_accept_assistant_turn_async_uses_highthinking_service_name():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agent pytest highThinkingQA/tests/test_conversation_authority_client.py -v`
Expected: FAIL because client file does not exist yet.

- [ ] **Step 3: Implement minimal authority client**

关键要求：
- service name 固定 `highThinkingQA`
- base url / token / timeout 从环境读取
- 提供：
  - `write_user_turn(...)`
  - `read_context_snapshot(...)`
  - `accept_assistant_turn_async(...)`
- 幂等键格式与 fastQA 完全一致
- requested/actual mode 保持 `thinking`

- [ ] **Step 4: Run tests to verify client passes**

Run: `conda run -n agent pytest highThinkingQA/tests/test_conversation_authority_client.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add highThinkingQA/server/services/conversation_authority_client.py highThinkingQA/tests/test_conversation_authority_client.py
git commit -m "feat: add highThinking authority client"
```

---

### Task 2: Add authority-aware chat persistence adapter with overlay

**Files:**
- Create: `highThinkingQA/server/services/chat_persistence.py`
- Test: `highThinkingQA/tests/test_chat_persistence.py`
- Reference: `fastQA/app/services/chat_persistence.py`

- [ ] **Step 1: Write the failing adapter tests**

```python
# highThinkingQA/tests/test_chat_persistence.py

def test_load_conversation_context_reads_authority_snapshot():
    ...

def test_persist_user_message_delegates_to_authority_client():
    ...

def test_persist_assistant_summary_stores_overlay_then_accepts_async():
    ...

def test_load_context_merges_pending_overlay_when_snapshot_lags():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `conda run -n agent pytest highThinkingQA/tests/test_chat_persistence.py -v`
Expected: FAIL because adapter file does not exist yet.

- [ ] **Step 3: Implement minimal adapter**

关键要求：
- 提供：
  - `load_conversation_context(...)`
  - `persist_user_message(...)`
  - `persist_assistant_summary(...)`
- authority snapshot -> `chat_history/summary/conversation_state` 归一化
- overlay 开关遵循 `config.py`
- `assistant_content` 非空 + `done_seen=true` 才触发 async accept
- 使用 ordered dispatcher 处理 async path
- 明确失败策略：
  - user write / context read 在 `public_service` 模式下 `fail-closed`
  - assistant async accept 在 `public_service` 模式下 `fail-open + retry/告警`

- [ ] **Step 4: Run tests to verify adapter passes**

Run: `conda run -n agent pytest highThinkingQA/tests/test_chat_persistence.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add highThinkingQA/server/services/chat_persistence.py highThinkingQA/tests/test_chat_persistence.py
git commit -m "feat: add highThinking authority chat persistence"
```

---

### Task 3: Implement shadow_public_service rollout path before cutover

**Files:**
- Modify: `highThinkingQA/server/services/chat_persistence.py`
- Modify: `highThinkingQA/config.py`
- Test: `highThinkingQA/tests/test_chat_persistence.py`
- Test: `highThinkingQA/tests/test_env_loader.py`

- [ ] **Step 1: Add failing tests for shadow rollout semantics**

```python
# highThinkingQA/tests/test_chat_persistence.py

def test_shadow_public_service_keeps_legacy_read_write_but_emits_shadow_write():
    ...

def test_shadow_public_service_failure_does_not_break_legacy_execution():
    ...
```

- [ ] **Step 2: Run targeted tests to verify failure**

Run: `conda run -n agent pytest highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_env_loader.py -v`
Expected: FAIL because adapter/config do not yet expose real shadow behavior.

- [ ] **Step 3: Implement shadow mode behavior**

关键要求：
- `legacy`: 主读写都走本地
- `shadow_public_service`: 主读写仍走本地，但并行 authority write / read compare（以不影响请求为前提）
- `public_service`: 主读写切 authority
- shadow 失败只能记日志，不得影响主请求

- [ ] **Step 4: Re-run targeted tests**

Run: `conda run -n agent pytest highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_env_loader.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add highThinkingQA/server/services/chat_persistence.py highThinkingQA/config.py highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_env_loader.py
git commit -m "feat: add highThinking shadow authority rollout"
```

---

### Task 4: Switch context loading from local conversation_service to authority snapshot

**Files:**
- Modify: `highThinkingQA/server/services/conversation_context_service.py`
- Test: `highThinkingQA/tests/test_chat_persistence.py`
- Test: `highThinkingQA/tests/test_ask_service_executor.py`

- [ ] **Step 1: Add/extend failing tests for context loading behavior**

```python
# highThinkingQA/tests/test_ask_service_executor.py

def test_prepare_execution_uses_authority_snapshot_turns_before_request_history():
    ...

def test_prepare_execution_preserves_summary_semantics_when_authority_summary_is_sparse():
    ...
```

- [ ] **Step 2: Run targeted tests to verify current failure**

Run: `conda run -n agent pytest highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_ask_service_executor.py -v`
Expected: FAIL because context loader still imports local conversation_service and lacks summary compatibility handling.

- [ ] **Step 3: Replace local snapshot loading with adapter call**

关键要求：
- 保留当前 `_merge_turns()` / overlap / budget 行为
- 只替换 snapshot 来源
- 若 authority 未启用，可保留 legacy fallback 作为 rollout 兼容分支
- 明确 `summary` 兼容策略：当 authority snapshot summary 为空壳时，rewrite/context 不得静默退化

- [ ] **Step 4: Re-run targeted tests**

Run: `conda run -n agent pytest highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_ask_service_executor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add highThinkingQA/server/services/conversation_context_service.py highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_ask_service_executor.py
git commit -m "refactor: load highThinking context from authority snapshot"
```

---

### Task 5: Switch ask router persistence hooks to authority adapter

**Files:**
- Modify: `highThinkingQA/server_fastapi/routers/ask.py`
- Test: `highThinkingQA/tests/test_ask_router_summary_persistence.py`
- Potential Test: add `highThinkingQA/tests/test_ask_router_authority_flow.py`

- [ ] **Step 1: Write failing router-level tests for authority hooks**

```python
# highThinkingQA/tests/test_ask_router_summary_persistence.py

def test_user_message_hook_calls_chat_persistence_adapter():
    ...

def test_assistant_summary_hook_calls_async_accept_and_not_local_add_message():
    ...

def test_stream_without_done_skips_assistant_accept():
    ...
```

- [ ] **Step 2: Run targeted tests to verify failure**

Run: `conda run -n agent pytest highThinkingQA/tests/test_ask_router_summary_persistence.py -v`
Expected: FAIL because router still binds local conversation_service.

- [ ] **Step 3: Replace local persistence helpers**

关键要求：
- `_persist_user_message_if_needed()` -> adapter `persist_user_message(...)`
- `_persist_assistant_message_if_needed()` -> adapter `persist_assistant_summary(...)`
- 不再直接调用本地 `conversation_service.add_message(...)`
- 不再在 ask 主链里调用本地 `refresh_conversation_summary(...)`

- [ ] **Step 4: Run targeted tests again**

Run: `conda run -n agent pytest highThinkingQA/tests/test_ask_router_summary_persistence.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add highThinkingQA/server_fastapi/routers/ask.py highThinkingQA/tests/test_ask_router_summary_persistence.py
git commit -m "refactor: route highThinking ask persistence through authority adapter"
```

---

### Task 6: Wire rollout config into real runtime behavior

**Files:**
- Modify: `highThinkingQA/config.py`
- Test: `highThinkingQA/tests/test_env_loader.py`
- Potential Modify: `highThinkingQA/server_fastapi/app.py`

- [ ] **Step 1: Add failing tests for rollout behavior**

```python
# highThinkingQA/tests/test_env_loader.py

def test_public_service_rollout_enables_authority_execution_targets():
    ...

def test_shadow_public_service_rollout_is_runtime_visible():
    ...

def test_overlay_flag_is_visible_to_chat_persistence_runtime():
    ...
```

- [ ] **Step 2: Run targeted tests to verify failure or missing runtime wiring**

Run: `conda run -n agent pytest highThinkingQA/tests/test_env_loader.py -v`
Expected: FAIL or missing assertions against runtime wiring.

- [ ] **Step 3: Connect config to implementation**

关键要求：
- 明确 `legacy / shadow_public_service / public_service` 三态如何影响 adapter
- 明确 overlay 是否开启
- 若 authority client 需要生命周期管理，在 `app.py` 增加 shutdown close

- [ ] **Step 4: Re-run env/config tests**

Run: `conda run -n agent pytest highThinkingQA/tests/test_env_loader.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add highThinkingQA/config.py highThinkingQA/server_fastapi/app.py highThinkingQA/tests/test_env_loader.py
git commit -m "feat: wire highThinking authority rollout settings"
```

---

### Task 7: Stop gateway from persisting thinking-route turns

**Files:**
- Modify: `gateway/app/routers/qa.py`
- Test: `gateway/tests/test_qa_proxy.py`
- Reference: `gateway/app/services/route_decision.py`

- [ ] **Step 1: Write failing gateway tests that encode the desired boundary**

```python
# gateway/tests/test_qa_proxy.py

def test_gateway_skips_user_persistence_for_thinking_route():
    ...

def test_gateway_skips_assistant_persistence_for_thinking_stream():
    ...

def test_gateway_keeps_fast_route_persistence_unchanged():
    ...
```

- [ ] **Step 2: Run targeted gateway tests to verify failure**

Run: `conda run -n agent pytest gateway/tests/test_qa_proxy.py -k persistence -v`
Expected: FAIL because gateway currently persists all routes.

- [ ] **Step 3: Implement route-conditional persistence in gateway**

关键要求：
- `actual_mode == "thinking"` 时：跳过 `conversation_persistence_service.persist_user_message()`
- `actual_mode == "thinking"` 时：跳过 sync/stream assistant summary persistence
- `fast` 路径行为保持不变
- 不要改变路由判断本身

- [ ] **Step 4: Re-run targeted gateway tests**

Run: `conda run -n agent pytest gateway/tests/test_qa_proxy.py -k persistence -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add gateway/app/routers/qa.py gateway/tests/test_qa_proxy.py
git commit -m "refactor: stop gateway persistence for thinking routes"
```

---

### Task 7A: Encode authority failure policy in tests and adapter behavior

**Files:**
- Modify: `highThinkingQA/server/services/chat_persistence.py`
- Test: `highThinkingQA/tests/test_chat_persistence.py`
- Test: `highThinkingQA/tests/test_ask_router_summary_persistence.py`

- [ ] **Step 1: Add failing tests for fail-open/fail-closed policy**

```python
# highThinkingQA/tests/test_chat_persistence.py

def test_public_service_mode_user_write_failure_blocks_execution():
    ...

def test_public_service_mode_context_read_failure_blocks_execution():
    ...

def test_public_service_mode_assistant_accept_failure_keeps_overlay_and_schedules_retry():
    ...
```

- [ ] **Step 2: Run targeted tests to verify failure**

Run: `conda run -n agent pytest highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_ask_router_summary_persistence.py -v`
Expected: FAIL because failure policy is not fully encoded yet.

- [ ] **Step 3: Implement explicit failure policy**

关键要求：
- `write_user_turn` 失败 -> 阻断请求
- `read_context_snapshot` 失败 -> 阻断请求
- `accept_assistant_turn_async` 失败 -> 不回滚已生成答案，但必须保留 overlay 并调度重试/告警

- [ ] **Step 4: Re-run targeted tests**

Run: `conda run -n agent pytest highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_ask_router_summary_persistence.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add highThinkingQA/server/services/chat_persistence.py highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_ask_router_summary_persistence.py
git commit -m "feat: encode highThinking authority failure policy"
```

---

### Task 8: Add cross-service regression coverage for the new authority flow

**Files:**
- Test: `highThinkingQA/tests/test_chat_persistence.py`
- Test: `highThinkingQA/tests/test_ask_router_summary_persistence.py`
- Test: `public-service/backend/tests/test_conversation_authority_integration.py`
- Test: `gateway/tests/test_qa_proxy.py`
- Test: `gateway/tests/test_route_decision.py`

- [ ] **Step 1: Add integration-style regression cases**

建议覆盖：
- thinking user write -> authority user message
- thinking context read -> authority snapshot
- thinking assistant done -> async accept
- snapshot lag + overlay merge
- authority summary sparse 时 rewrite/context 不静默退化
- gateway no longer duplicates thinking writes
- gateway file-provider / legacy alias 分流前置逻辑不把文件请求误送到 highThinkingQA

- [ ] **Step 2: Run highThinkingQA targeted suite**

Run: `conda run -n agent pytest highThinkingQA/tests/test_conversation_authority_client.py highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_ask_router_summary_persistence.py highThinkingQA/tests/test_ask_service_executor.py highThinkingQA/tests/test_env_loader.py -v`
Expected: PASS

- [ ] **Step 3: Run gateway targeted suite**

Run: `conda run -n agent pytest gateway/tests/test_qa_proxy.py gateway/tests/test_route_decision.py -k "thinking or persistence or file or mixed" -v`
Expected: PASS

- [ ] **Step 4: Run public-service targeted suite**

Run: `conda run -n agent pytest public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_authority_integration.py public-service/backend/tests/test_conversation_assistant_inbox.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add highThinkingQA/tests gateway/tests/test_qa_proxy.py gateway/tests/test_route_decision.py public-service/backend/tests
git commit -m "test: cover highThinking authority persistence flow"
```

---

### Task 9: End-to-end verification and rollout notes

**Files:**
- Modify: `docs/superpowers/specs/2026-03-23-highthinkingqa-persistence-migration-spec.md`
- Optional Modify: service env examples / local run docs if touched during implementation

- [ ] **Step 1: Run full relevant service suites**

Run:
- `conda run -n agent pytest highThinkingQA/tests -v`
- `conda run -n agent pytest gateway/tests/test_qa_proxy.py gateway/tests/test_route_decision.py -v`
- `conda run -n agent pytest public-service/backend/tests/test_conversation_authority_api.py public-service/backend/tests/test_conversation_authority_integration.py public-service/backend/tests/test_conversation_assistant_inbox.py public-service/backend/tests/test_conversation_module.py -v`

Expected: PASS

- [ ] **Step 2: Restart relevant services and smoke test**

Run:
- `bash highThinkingQA/scripts/stop_fastapi_gunicorn.sh && bash highThinkingQA/scripts/start_fastapi_gunicorn.sh`
- `bash gateway/scripts/stop_gunicorn.sh && bash gateway/scripts/start_gunicorn.sh`
- `bash scripts/status_all.sh`

Expected: gateway/highThinkingQA/public-service health all ok

- [ ] **Step 3: Manual smoke checklist**

验证：
- thinking 普通 QA 首问可成功
- 第二问能读到上一轮 assistant 内容
- gateway 不再重复写 thinking 会话
- fastQA 文件/混合 QA 不受影响
- 流式输出结束后持久化不阻塞前端

- [ ] **Step 4: Update spec with actual rollout outcome and residual risks**

补充：
- 是否启用 overlay
- 是否保留 legacy fallback
- public-service assistant inbox retry 风险是否仍待后续处理

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-03-23-highthinkingqa-persistence-migration-spec.md
git commit -m "docs: finalize highThinking authority rollout notes"
```

---

## Acceptance Checklist

- [ ] highThinkingQA 不再在 ask 主链路中直接调用本地 `conversation_service.add_message()`
- [ ] highThinkingQA 不再从本地 `conversation_service.get_conversation_context_snapshot()` 读取主上下文
- [ ] highThinkingQA 可以通过 public-service authority snapshot 正常得到 recent turns
- [ ] highThinkingQA 在 authority summary 为空壳时仍能保持 rewrite/context 质量，不出现明显语义退化
- [ ] highThinkingQA 在 done 后调用 assistant async accept，而不是本地直接写 assistant message
- [ ] overlay 能覆盖 assistant async materialize 的空窗
- [ ] `shadow_public_service` 阶段可以并行 shadow 写入/对比，且 shadow 故障不影响主请求
- [ ] gateway 对 thinking 路径不再写 public-service 公网 messages API
- [ ] gateway 对 fast 路径行为不回归
- [ ] 文件 QA / 混合 QA 仍全部走 fastQA
- [ ] gateway file-provider / legacy alias 场景下不会把文件请求误送到 highThinkingQA
- [ ] highThinkingQA 普通 QA 第二问能稳定读取上一轮回答
- [ ] 所有相关 pytest 套件通过

## Deferred / Explicitly Out of Scope

- highThinkingQA 本地 conversation/upload/documents 路由的彻底下线
- public-service snapshot summary 语义增强
- gateway 文件上下文 provider 对普通 QA 的依赖收缩



## Progress Update 2026-03-23 21:15 CST

已完成并验证：
- `highThinkingQA` 已接入 authority client 与 chat persistence adapter。
- `conversation_context_service` 主上下文读取已切到 `chat_persistence.load_conversation_context()`；ask 路由的 user/assistant 持久化钩子已切到 adapter。
- `public-service` assistant inbox 已补 `retry/dead/reclaim stuck processing`。
- `public-service` authority 闭环测试已覆盖 `fastQA` 与 `highThinkingQA` 两个 client。
- `public-service` 与 `highThinkingQA` 已重启，健康检查通过。

本轮实测通过：
- `conda run -n agent pytest highThinkingQA/tests/test_conversation_authority_client.py highThinkingQA/tests/test_chat_persistence.py highThinkingQA/tests/test_ask_router_summary_persistence.py highThinkingQA/tests/test_env_loader.py -q`
- `conda run -n agent pytest highThinkingQA/tests/test_ask_service_executor.py -k build_conversation_context_uses_chat_persistence_snapshot -q`
- `conda run -n agent pytest public-service/backend/tests/test_conversation_assistant_inbox.py public-service/backend/tests/test_conversation_authority_integration.py public-service/backend/tests/test_system_module.py -k 'authority_assistant_inbox or conversation_authority or background_status_contract or highthinking_authority_client_closed_loop_materializes_assistant_turn' -q`
- `conda run -n agent pytest gateway/tests/test_qa_proxy.py -k 'thinking_skips_public_message_persistence or routes_file_question_to_fast_backend or routes_mixed_question_to_fast_backend or persists_user_and_assistant_messages' -q`

当前剩余：
- 还没有跑完整 `highThinkingQA/tests` 全套；`test_ask_service_executor.py` 里存在一批旧的 monkeypatch 路径问题，和这次 authority 迁移逻辑不是同一类问题，需要单独收敛。
- gateway 测试补强结论已经确认，但子 agent 的测试文件改动还没合并到当前工作区；当前生产语义仍是按 `actual_mode` 判定是否由 gateway 持久化。
- 还缺前端/真实对话手工联调：验证 thinking 第二问是否稳定读到上一轮 assistant 内容。
