# 2026-03-23 highThinkingQA 普通 QA / 聊天持久化现状审阅

## 1. 结论先行

当前 monorepo 中，highThinkingQA 的普通 QA 与聊天持久化存在明确的 authority 分裂：

- `gateway + public-service` 已经形成当前前端主生效的会话 authority 链路；
- `highThinkingQA` 仍保留并调用一套本地 conversation 持久化与上下文读取链路；
- 两者之间没有明确的 conversation create/sync 桥接；
- 因此 highThinkingQA 当前本地会话能力大概率不是规范主链，而是残留的 legacy 本地实现。

用户要求的最终边界是合理且必要的：

- 文件 QA / 混合 QA 全部归 fastQA；
- highThinkingQA 只做自己的普通 thinking QA；
- highThinkingQA ask 主链路的聊天记录持久化应迁移到 public-service，而不是继续自持一份本地 authority。

## 2. 当前规范入口

### 2.1 gateway 是规范入口

已确认：
- 会话 CRUD `/api/conversations*` 通过 gateway 指向 public-service
- 普通 thinking QA `/api/{mode}/ask`、`/ask_stream` 由 gateway 分发到 highThinkingQA

这意味着：
- 从产品面看，当前用户真正使用的是 public-service 的会话体系
- highThinkingQA 本地 conversation 子系统并不是前端主入口

## 3. 当前 gateway 对 highThinkingQA 的关系

### 3.1 gateway 在转发前后就已经写 public-service

代码锚点：
- `gateway/app/routers/qa.py`
- `gateway/app/services/conversation_persistence.py`

已确认行为：
- 转发到 highThinkingQA 前：
  - `persist_user_message(...)`
- 同步 ask 成功后 / 流式 ask 结束后：
  - `persist_assistant_summary(...)`

注意：
- 这套写入走的是 public-service 公开接口：
  - `POST /api/v1/conversations/{conversation_id}/messages`
- 不是 internal authority API

含义：
- 当前 thinking QA 的“主生效会话写入”很可能已经在 gateway 完成
- 但这条链路职责不纯，不是理想终局

## 4. 当前 highThinkingQA 自身的 ask / 持久化链路

### 4.1 ask 主链

代码锚点：
- `highThinkingQA/server_fastapi/routers/ask.py`
- `highThinkingQA/server/services/ask_service.py`
- `highThinkingQA/server/services/conversation_context_service.py`

当前链路：
1. gateway 归一化 payload，保留 `actual_mode=thinking`
2. 转发到 `highThinkingQA /api/thinking/ask` 或 `/api/thinking/ask_stream`
3. `routers/ask.py` 解析请求并从 token 绑定 `user_id`
4. `conversation_context_service.py` 从本地 `conversation_service.get_conversation_context_snapshot(...)` 读取上下文
5. `ask_service.py` 完成 rewrite 和 `agent_core.graph.run_agent(...)`
6. 产出 `metadata/content/done`
7. ask router 前后再调用本地 conversation 持久化 helper

### 4.2 本地持久化仍然存在

代码锚点：
- `highThinkingQA/server_fastapi/routers/ask.py`
- `highThinkingQA/server/services/conversation/conversation_service.py`

已确认行为：
- ask/ask_stream 前：写 user message
- ask/ask_stream 收尾：写 assistant message
- assistant 写完后还会 refresh summary

### 4.3 本地持久化后端资源

当前本地 conversation 子系统会落到：
- MySQL：`conversations`、`conversation_messages`
- 本地 JSON：chat json store
- 对象存储镜像 + outbox 重试

这说明：
- highThinkingQA 并不是只残留一个假接口，而是真的还保留完整本地会话实现
- 只是它未必还是规范主链

## 5. 为什么说 highThinkingQA 本地会话大概率不是主生效链路

关键原因：
- 会话创建主入口已经在 public-service
- highThinkingQA 本地 `add_message` / `get_conversation_context_snapshot` 都要求本地也存在相同 `conversation_id`
- 当前代码中没有看到 gateway 或 public-service 把 conversation create/mutate 同步到 highThinkingQA 本地库

因此大概率结果是：
- public-service 侧 conversation 存在并正常增长
- highThinkingQA 本地可能查不到该 conversation
- 本地持久化/上下文读取会跳过、失败或返回空

## 6. 旧版对照

legacy source of truth：
- `/home/cqy/worktrees/fastapi-version/backend`

旧版高层结构：
- `ask_gateway` 接收 ask/ask_stream
- 普通 QA 走知识库问答主链
- `ask_gateway` 默认直接绑定 `conversation_service.persist_user_request`
- 收尾直接绑定 `conversation_service.persist_assistant_summary`

关键点：
- 旧版 ask 与会话持久化在同一服务 authority 下，没有跨服务一致性问题
- 当前新版 highThinkingQA 的核心问题，正是“普通 QA 执行服务”和“主会话 authority”已经拆开，但 highThinkingQA 还按旧模式在读写本地会话

## 7. 当前高价值缺口

### 7.1 上下文读取仍只读本地

- `conversation_context_service.py` 当前只读本地 `conversation_service`
- 不读 public-service authority snapshot

影响：
- 即使 public-service 已有完整对话历史，highThinkingQA 执行时也可能看不到

### 7.2 ask 前后写入仍只写本地

- ask router 当前仍直接 `add_message(...)`
- 不走 public-service internal authority API

影响：
- 与 public-service 的规范会话权威面继续分裂

### 7.3 rollout 配置存在但未接入主链

代码锚点：
- `highThinkingQA/config.py`

已有配置痕迹：
- `CONVERSATION_EXECUTION_AUTHORITY_TARGET`
- `CONVERSATION_ASSISTANT_WRITE_TARGET`
- `CONVERSATION_CONTEXT_READ_TARGET`
- `CONVERSATION_OVERLAY_ENABLED`

但当前事实：
- 这些 rollout flag 基本未进入 ask/上下文/持久化主链

### 7.4 gateway 当前写 public-service 用的是公开消息接口，而不是 authority/internal API

影响：
- 即便前端体验上能看到消息入会话，也不意味着 highThinkingQA 已完成 authority 迁移
- 这只是 gateway 层的代理式持久化，不是 highThinkingQA 的执行侧 authority 集成

## 8. 当前最实质的四个新版缺口

1. 没有 public-service -> highThinkingQA 的 conversation 镜像创建/同步
2. highThinkingQA 上下文读取仍只读本地，不读 public-service authority
3. gateway 写 public-service 用的是公开 messages API，不是 authority/internal API
4. authority rollout 配置存在，但未接入主链

## 9. 对迁移方案的直接启示

结论很明确：
- 不应该再补一条“public-service 同步镜像到 highThinkingQA 本地 conversation”的桥
- 正确方向应该是反过来：
  - highThinkingQA ask 主链路直接切到 public-service authority read/write
  - 本地 conversation 子系统逐步退场

也就是：
- user write -> public-service internal user write
- context read -> public-service context snapshot
- assistant done -> public-service assistant async accept
- overlay -> 复用 fastQA 方案

