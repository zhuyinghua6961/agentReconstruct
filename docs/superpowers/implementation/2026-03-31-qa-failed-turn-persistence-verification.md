# QA Failed-Turn Persistence Verification

日期：2026-04-01

## 范围

- `public-service` terminal authority contract / materialization / read model
- `fastQA` terminal persistence
- `highThinkingQA` terminal persistence
- `frontend-vue` failed/canceled history读取与渲染
- `gateway` stream cancel 保持 `type="error"` envelope

## 已执行命令

### Public-Service

```bash
conda run --no-capture-output -n agent pytest \
  public-service/backend/tests/test_conversation_authority_api.py \
  public-service/backend/tests/test_conversation_module.py \
  public-service/backend/tests/test_conversation_assistant_inbox.py \
  public-service/backend/tests/test_conversation_authority_integration.py \
  public-service/backend/tests/test_route_surface.py -q
```

结果：`85 passed`

### fastQA

```bash
conda run --no-capture-output -n agent pytest \
  fastQA/tests/test_conversation_authority_client.py \
  fastQA/tests/test_chat_persistence.py \
  fastQA/tests/test_qa_placeholder.py -q
```

结果：`52 passed`

备注：
- 按 rollout 原计划把 `fastQA` 与 `highThinkingQA` 同名测试文件放进同一条 pytest 命令，会触发 `import file mismatch`。
- 本轮验证改为分两条命令执行，避免 pytest 对同 basename 模块的收集冲突。

### highThinkingQA

```bash
conda run --no-capture-output -n agent pytest \
  highThinkingQA/tests/test_conversation_authority_client.py \
  highThinkingQA/tests/test_chat_persistence.py \
  highThinkingQA/tests/test_ask_router_summary_persistence.py -q
```

结果：`23 passed`

### Gateway

```bash
conda run --no-capture-output -n agent pytest gateway/tests/test_qa_proxy.py -q
```

结果：`55 passed`

新增回归：
- `test_stream_with_quota_preserves_cancel_error_envelope`

覆盖点：
- upstream cancel 仍以 SSE `type="error"` + `ASK_CANCELLED` 透传
- gateway stream 中不存在新增的 SSE `type="canceled"` transport
- quota finalize 在 cancel 时仍记为 `success=False`

### Frontend

```bash
cd frontend-vue && npm test -- \
  src/services/api.structure.test.js \
  src/stores/chatPersistence.test.js \
  src/stores/chatStore.failed-terminal.test.js \
  src/views/Home.structure.test.js
```

结果：`17 passed`

```bash
cd frontend-vue && npm run build
```

结果：`build passed`

## 自动验证结论

已自动验证：

- legacy success-only authority path 仍保留兼容
- `fastQA`/`highThinkingQA` success path 已统一走 terminal contract
- `failed`/`canceled` terminal message 可经 authority materialize 并在 read model 中保留
- `failed`/`canceled` assistant turn 不进入 LLM-facing context projection
- authority accept 失败会记录 `terminal_persistence_unconfirmed`
- gateway stream cancel 保持 `type="error"` envelope，且自动测试已断言不会新增 `type="canceled"` 帧
- frontend 会保留 `status / terminal_status / failure_message / failure_code / retriable / done_seen`
- frontend 会把 failed/canceled 历史消息视为 complete terminal message，而不是 loading

## 人工联调清单

本轮未在真实前后端联调环境逐项点击验证，以下事项仍建议人工走查：

- `fastQA` success 后刷新，仍显示正常 `done` assistant turn
- `fastQA` 失败前无输出，刷新后显示 failed shell
- `fastQA` 失败前有 partial output，刷新后显示 partial markdown + failed 状态
- `highThinkingQA` success 后刷新，仍显示正常 `done` assistant turn
- `highThinkingQA` 失败后刷新，显示 failed terminal turn
- 前端显式 stop/cancel 后刷新，显示 canceled terminal turn
- 会话列表 preview / 时间 / `message_count` 与 detail 保持一致
- 下一轮问答时，失败 assistant turn 不污染上下文

## 已知限制

- `terminal_persistence_unconfirmed` 仍是 phase 1 的观测日志，不会自动重试到用户可见确认态
- 浏览器断连场景的 cancel 覆盖仍不完整，当前主要覆盖显式 cancel / 服务侧 error path
- `fastQA` 与 `highThinkingQA` 同名测试文件不能直接放进同一条 pytest 命令执行，需要拆开或改收集策略
