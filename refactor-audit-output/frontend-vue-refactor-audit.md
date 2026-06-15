# frontend-vue 重构审计文档

> 状态：已完成第一轮只读审计。本文档只记录基于代码阅读得到的证据，审计产物位于独立目录，不修改业务代码。

## 1. 审计范围

- 已阅读目录：`frontend-vue/src/services/`、`src/stores/`、`src/views/`、`src/components/`、`src/features/`、`src/router/`、`src/utils/`、`frontend-vue/tests/`。
- 已阅读关键文件：`src/services/api.js`、`src/stores/chatStore.js`、`src/stores/chatPersistence.js`、`src/stores/streamPersistPolicy.js`、`src/stores/*.test.js`、`src/views/Home.vue`、`src/api/*`、`src/features/*`、`src/router/index.js`、`vite.config.js`、`package.json`。
- 未覆盖或需要本地进一步验证的范围：未运行 `npm run build`；`features/chat` 与 `features/controls` 是否计划恢复为产品入口需要产品/路由确认。

## 2. 当前 live path

### 2.1 服务入口

- app factory / main entry：Vite Vue app，主路由 `/` 指向 `src/views/Home.vue`。
- router 注册位置：`frontend-vue/src/router/index.js`。
- runtime/persistence：`Home.vue` 使用 `useChatStore()`，`chatStore.js` 管理 chat/messages/uploads/task recovery/localStorage/KB/runtime；`services/api.js` 是主要 API façade。
- 默认网络：Vite proxy `/api` 到 gateway `http://127.0.0.1:8101`；`VITE_API_BASE_URL` 可覆盖绝对 base。

关键证据：

```js
const API_BASE = resolveBackendBase();
const V1 = '/api';
const REFRESH_SURVIVABLE_QA_TASKS_ENABLED =
  readFeatureFlag('VITE_REFRESH_SURVIVABLE_QA_TASKS_ENABLED', false);
```

```js
const proxyTarget =
  process.env.BACKEND_PROXY_TARGET || process.env.VITE_PROXY_TARGET || 'http://127.0.0.1:8101';

proxy: {
  '/api': {
    target: proxyTarget,
    changeOrigin: true,
  },
}
```

### 2.2 对外接口路径

| 接口路径 | 方法 | 所在文件 | 当前职责 | 是否 active |
|---|---|---|---|---|
| `/api/{mode}/ask_stream` | POST | `src/services/api.js` | legacy/default streaming QA | active live path |
| `/api/ask_stream` | POST | `src/services/api.js` | fallback streaming QA | active live path |
| `/api/v1/{mode}/ask_stream` | POST | 未见 frontend 调用 | required path but not used by current frontend | unknown/missing |
| `/api/v1/ask_stream` | POST | 未见 frontend 调用 | required path but not used by current frontend | unknown/missing |
| `/api/v1/tasks` | POST | `src/services/api.js` | refresh-survivable task create | active behind rollout flag |
| `/api/v1/tasks/{task_id}/events` | GET SSE | `src/services/api.js` | task replay stream | active behind rollout flag |
| `/api/v1/tasks/{task_id}/cancel` | POST | `src/services/api.js` | task cancel | active behind rollout flag |
| `/api/conversations` | GET/POST | `src/services/api.js` | conversation list/create | active live path |
| `/api/conversations/{id}` | GET/DELETE | `src/services/api.js` | detail/delete | active live path |
| `/api/conversations/{id}/messages` | POST | `src/services/api.js` | add message | active live path |
| `/api/conversations/{id}/title` | PUT/PATCH | `src/services/api.js` | update title | active live path |
| `/api/upload_pdf` | POST | `src/services/api.js` | PDF upload | active live path |
| `/api/upload_excel` | POST | `src/services/api.js` | Excel upload | active live path |
| `/api/conversations/{id}/files/{file_id}` | DELETE | `src/services/api.js` | delete uploaded file | active live path |
| `/api/conversations/{id}/files/{file_id}/download` | GET | `Home.vue`/API | file download | active live path |
| `/api/kb_info` | GET | `chatStore.js` -> `api.getKbInfo()` | KB info | active live path |
| `/api/refresh_kb` | POST | legacy workspace/API | KB refresh | deprecated but still referenced |
| `/api/quota/my`, `/api/quota/configs`, `/api/quota/users/{id}` | mixed | `services/quota.js` / `api/quota.js` | quota UI/admin | active live path |

关键路径片段：

```js
const askPath = ['fast', 'thinking', 'patent'].includes(normalizedMode)
  ? `${V1}/${normalizedMode}/ask_stream`
  : `${V1}/ask_stream`;

const response = await fetch(`${API_BASE}${askPath}`, {
```

```js
return await requestJson(`${API_BASE}${V1}/v1/tasks`, { ... })

const response = await fetch(
  `${API_BASE}${V1}/v1/tasks/${encodeURIComponent(String(taskId || ''))}/events?after_seq=${encodeURIComponent(afterSeq)}`,
```

### 2.3 核心调用链

```text
Home.vue
  -> chatStore durable state + runtime state
  -> services/api.js
  -> gateway /api/{mode}/ask_stream
  -> streamSseJson -> Home.vue event handlers
  -> chatStore messages/localStorage

rollout path:
Home.vue
  -> recoverableTaskController
  -> api.createTask / streamTaskEvents / cancelTask
  -> gateway /api/v1/tasks*
  -> task replay events -> chatStore activeTask/lastTaskSeq/messages
```

## 3. 发现的重构点

### R-001：`chatStore.js` 是巨型状态模块

- 严重程度：P1
- 类型：frontend state architecture / 巨型模块
- 代码位置：
  - `frontend-vue/src/stores/chatStore.js`
- 接口路径：
  - conversation CRUD
  - `/api/kb_info`
  - file delete/download
  - task recovery indirectly
- 关键代码片段：

```js
const chats = ref([])
const currentChatId = ref(null)
const legacyStreamingState = ref(false)
const chatBusyRuntime = ref({})
const kbInfo = ref({ loading: true, size: 0, vectorSize: 0, graphSize: 0, graphConnected: false })
const userId = ref(null)
const syncStatus = ref('synced')
let persistTimer = null

const sessionState = ref({
  initialPdfIds: [],
  newlyUploadedPdfIds: [],
  lastUsedPdfIds: []
})
```

- 当前问题：1965 行单一 Pinia store 同时持有 durable chat、messages、uploads、selected files、runtime busy、KB、session file tracking、localStorage persist、server sync、task cursor。
- 建议重构方式：拆 `conversationStore`、`messageStore`、`streamStore/runtimeStore`、`uploadStore`、`taskStore`、`kbStore`、`chatPersistenceStore`；过渡期保留 `useChatStore` façade。
- 是否可抽共享包：可抽 `chat-domain` normalize/persistence/runtime policy。
- 建议目标模块：`src/stores/conversationStore.js`、`messageStore.js`、`streamRuntimeStore.js`、`taskRecoveryStore.js`、`uploadStore.js`、`kbStore.js`。
- 设计模式建议：Facade Store、Domain Services、Thin Pinia Stores。
- 影响范围：`Home.vue`、store tests、task recovery utils、file upload UI。
- 风险：高。一次性拆分会破坏恢复/切会话/并发生成。
- 测试计划：先保持 `useChatStore` façade API 不变；跑 `src/stores/*.test.js`、`Home.structure.test.js`、`recoverableTaskController.test.js`。
- 是否可立即删除：否。
- 删除或迁移前置条件：冻结当前 store public API 清单。

### R-002：streaming/busy runtime 与持久化数据耦合

- 严重程度：P1
- 类型：runtime state separation
- 代码位置：
  - `frontend-vue/src/stores/chatStore.js`
  - `applyBusyRuntimeMutation()`
  - persistence scheduler
- 接口路径：
  - `/api/{mode}/ask_stream`
  - `/api/v1/tasks/*`
- 关键代码片段：

```js
function applyBusyRuntimeMutation(mutator, options = {}) {
  const previousIsStreaming = Boolean(legacyStreamingState.value || countBusyRuntimeEntries(chatBusyRuntime.value) > 0)
  const nextRuntimeMap = { ...(chatBusyRuntime.value || {}) }
  mutator(nextRuntimeMap)
  chatBusyRuntime.value = nextRuntimeMap
  const nextIsStreaming = Boolean(legacyStreamingState.value || countBusyRuntimeEntries(nextRuntimeMap) > 0)
  if (options?.persist !== false && shouldForcePersistForStreamingTransition({ previousIsStreaming, nextIsStreaming })) {
    saveChats({ force: true })
  }
}
```

- 当前问题：runtime mutation 直接触发 durable chat persistence；`isStreaming` 混入 active task recoverability。
- 建议重构方式：`streamRuntimeStore` 只维护 abort/busy/phase/stopRequested，不直接写 localStorage；terminal edge 由 task/message store 写 durable message/cursor。
- 是否可抽共享包：可抽 stream runtime 状态机。
- 建议目标模块：`src/stores/streamRuntimeStore.js`、`src/domain/streamLifecycle.js`。
- 设计模式建议：Finite State Machine、Event Reducer。
- 影响范围：取消、并发容量、刷新恢复。
- 风险：高。terminal edge 漏 persist 会导致刷新丢最终状态。
- 测试计划：保留 concurrent-streaming/persistenceTiming 测试，新增 runtime store 不持久化 abortController/requestId 断言。
- 是否可立即删除：否。
- 删除或迁移前置条件：用测试锁定 terminal persist 行为。

### R-003：`chatPersistence.js` 已剔除 runtime-only 字段，但规则偏窄

- 严重程度：P2
- 类型：persistence hygiene
- 代码位置：
  - `frontend-vue/src/stores/chatPersistence.js`
- 接口路径：
  - localStorage
- 关键代码片段：

```js
const RUNTIME_ONLY_MESSAGE_FIELDS = ['streamRequestId']
const RUNTIME_ONLY_CHAT_FIELDS = ['busyRuntime', 'chatBusyRuntime']
```

- 当前问题：已剔除 `streamRequestId`、`busyRuntime`、`chatBusyRuntime`；未来如果把 `abortController`、`pendingContent`、`stopRequested`、`runtimePhase` 挂到 chat/message，当前黑名单不会清理。
- 建议重构方式：改为 schema-based durable projection，而不是删除少数字段。
- 是否可抽共享包：可抽 `prepareChatSnapshot/restoreChatSnapshot`。
- 建议目标模块：`src/domain/chatPersistenceSnapshot.js`。
- 设计模式建议：DTO Projection。
- 影响范围：localStorage reload、task recovery。
- 风险：中。投影过严可能丢 durable terminal metadata。
- 测试计划：扩展 `chatPersistence.test.js` 覆盖 pendingContent/abort-like/runtime phase。
- 是否可立即删除：否。
- 删除或迁移前置条件：定义 durable chat/message schema。

### R-004：`streamPersistPolicy.js` 已独立，建议保留并上移

- 严重程度：P3
- 类型：policy extraction
- 代码位置：
  - `frontend-vue/src/stores/streamPersistPolicy.js`
- 接口路径：
  - localStorage
- 关键代码片段：

```js
export const STREAM_PERSIST_DEBOUNCE_MS = 1200

export function resolveChatPersistPolicy({ force = false, isStreaming = false } = {}) {
  if (force || !isStreaming) return { mode: 'immediate', debounceMs: 0 }
  return { mode: 'debounced', debounceMs: STREAM_PERSIST_DEBOUNCE_MS }
}

export function shouldForcePersistForStreamingTransition({ previousIsStreaming = false, nextIsStreaming = false } = {}) {
  return Boolean(previousIsStreaming) && !Boolean(nextIsStreaming)
}
```

- 当前问题：策略文件已抽出，但调用仍在巨型 store 内。
- 建议重构方式：迁入 `persistenceScheduler` 或 `domain/persistencePolicy`。
- 是否可抽共享包：是，前端 domain 层共享。
- 建议目标模块：`src/stores/persistenceScheduler.js`。
- 设计模式建议：Policy Object。
- 影响范围：低。
- 风险：低。
- 测试计划：保留 `streamPersistPolicy.test.js`。
- 是否可立即删除：否。
- 删除或迁移前置条件：无。

### R-005：`services/api.js` 混合多个后端能力

- 严重程度：P1
- 类型：API boundary / 巨型模块
- 代码位置：
  - `frontend-vue/src/services/api.js`
- 接口路径：
  - conversation、QA stream、task、upload、document、KB、file
- 关键代码片段：

```js
export const api = {
  refreshSurvivableQATasksEnabled: REFRESH_SURVIVABLE_QA_TASKS_ENABLED,

  async createConversation(_userId, title = '新对话') {
    const payload = await requestJson(`${API_BASE}${V1}/conversations`, {
```

```js
async getKbInfo() {
  return await requestJson(`${API_BASE}${V1}/kb_info`, {
    method: 'GET',
    headers: authHeaders(false),
  });
},

async *askStream(question, chatHistory = [], conversationId = null, pdfContext = null, signal = undefined, mode = 'thinking') {
```

- 当前问题：941 行单对象混合 public-service、gateway QA、task、upload、conversation、document translation、file normalize、auth error handling。
- 建议重构方式：拆 `conversationApi`、`qaStreamApi`、`taskApi`、`uploadApi`、`documentApi`、`kbApi`、`quotaApi`，保留 `api` façade 兼容迁移。
- 是否可抽共享包：可抽 `httpClient`、`sseClient`、`normalizers`。
- 建议目标模块：`src/services/api/conversationApi.js`、`qaStreamApi.js`、`taskApi.js`、`uploadApi.js`、`documentApi.js`、`kbApi.js`。
- 设计模式建议：API Gateway Adapter、Compatibility Facade。
- 影响范围：`chatStore.js`、`Home.vue`、`PdfReader.vue`、tests。
- 风险：高。路径拼接变更风险高，尤其 `/api/v1/tasks` vs `/api/{mode}/ask_stream`。
- 测试计划：新增每个 client 的 URL contract tests；保留 `api.structure.test.js`。
- 是否可立即删除：否。
- 删除或迁移前置条件：固化接口路径表。

### R-006：`/api/v1/{mode}/ask_stream` 未见前端 live 调用

- 严重程度：P2
- 类型：contract mismatch
- 代码位置：
  - `frontend-vue/src/services/api.js`
  - `frontend-vue/src/api/chat.js`
- 接口路径：
  - `/api/v1/{mode}/ask_stream`
  - `/api/v1/ask_stream`
- 关键代码片段：

```js
const askPath = ['fast', 'thinking', 'patent'].includes(normalizedMode)
  ? `${API_PREFIX}/${normalizedMode}/ask_stream`
  : `${API_PREFIX}/ask_stream`;
```

- 当前问题：前端实际拼 `/api/fast/ask_stream`、`/api/thinking/ask_stream`、`/api/patent/ask_stream` 或 `/api/ask_stream`，不是 `/api/v1/.../ask_stream`。gateway 支持 v1，但前端未使用。
- 建议重构方式：由 gateway contract 明确 canonical 版本路径；如果 canonical 是 `/api/v1/{mode}/ask_stream`，集中修改 `qaStreamApi` route builder。
- 是否可抽共享包：可抽前端 route builder。
- 建议目标模块：`src/services/api/routes.js`、`qaStreamApi.js`。
- 设计模式建议：Route Builder。
- 影响范围：QA send。
- 风险：高。路径错会直接断流。
- 测试计划：URL contract test 覆盖 required paths。
- 是否可立即删除：否。
- 删除或迁移前置条件：确认 gateway canonical path。

### R-007：`src/api/*` 与 `services/api.js` 双 API 门面并存

- 严重程度：P2
- 类型：重复抽象 / scaffold
- 代码位置：
  - `frontend-vue/src/api/chat.js`
  - `src/api/conversation.js`
  - `src/features/chat/composables/useChatSession.js`
- 接口路径：
  - `/api/kb_info`
  - `/api/upload_pdf`
  - `/api/conversations/*`
- 关键代码片段：

```js
import {
  createConversation,
  deleteConversation,
  getConversationDetail,
  listConversations,
} from '../../../api/conversation';
import { streamAsk } from '../../../api/chat';
```

- 当前问题：router 只注册 `Home.vue`，但 `features/chat` 保留另一套 session/localStorage/API 调用模型。
- 建议重构方式：确认是否下线；若保留，改用拆分后的 canonical API clients。
- 是否可抽共享包：可共享 `conversationApi/qaStreamApi`。
- 建议目标模块：统一到 `src/services/api/*`。
- 设计模式建议：Single Source of Truth。
- 影响范围：未来误用风险。
- 风险：中。
- 测试计划：导入链测试或 dead-code check。
- 是否可立即删除：需要进一步验证。
- 删除或迁移前置条件：确认 `features/chat` 是否产品入口。

### R-008：`Home.vue` 直接依赖深层 store 结构

- 严重程度：P1
- 类型：component/store coupling / 巨型组件
- 代码位置：
  - `frontend-vue/src/views/Home.vue`
  - `currentRecoverableTaskSnapshot`
  - file download/upload/task recovery logic
- 接口路径：
  - conversation detail
  - file download
  - task replay
- 关键代码片段：

```js
const currentRecoverableTaskSnapshot = computed(() => {
  const chatId = normalizeChatId(store.currentChatId)
  const chat = getChatById(chatId)
  const activeTask = chat?.activeTask && typeof chat.activeTask === 'object' ? activeTask : null
  return {
    chatId,
    taskId: String(activeTask?.task_id || '').trim(),
    status: String(activeTask?.status || '').trim().toLowerCase(),
    replayAvailable: activeTask?.replay_available !== false,
  }
})
```

- 当前问题：组件直接读写 `chat.activeTask`、`uploaded_files`、`pdf_list`、`excel_list`、`lastTaskSeq`，还手写 download URL。`Home.vue` 自身约 4496 行，承担流式 runtime、task recovery、文件选择、上传、UI 状态。
- 建议重构方式：提供 selectors/actions：`currentTaskSnapshot`、`replaceConversationTruth`、`downloadFileUrl`；再拆 Home 子组件/组合函数。
- 是否可抽共享包：selectors 可抽。
- 建议目标模块：`src/domain/conversationSelectors.js`、`src/services/api/fileApi.js`、`src/features/chat/`.
- 设计模式建议：Selector + Command Action。
- 影响范围：Home 巨型组件拆分。
- 风险：中高。
- 测试计划：Home structure tests 改断言 selectors/actions；浏览器 E2E 覆盖发送/取消/恢复。
- 是否可立即删除：否。
- 删除或迁移前置条件：store façade 稳定。

### R-009：后端 UI 文案透传仍存在

- 严重程度：P2
- 类型：UX/error contract
- 代码位置：
  - `frontend-vue/src/views/Home.vue`
  - stream error handling
  - `frontend-vue/src/services/api.js`
- 接口路径：
  - stream error
  - task create error
  - HTTP error
- 关键代码片段：

```js
if (data.type === 'error') {
  const errorText = String(data.message || data.error || '处理失败')
  const presentation = buildRoutingErrorPresentation({
    code: data.code,
    message: errorText,
    metadata: mergedMeta,
    data: data.data,
  })
  const renderedError = presentation.kind === 'markdown'
    ? presentation.markdown
    : errorText
```

- 当前问题：非 quota/routing 场景仍直接展示 `data.message`/`data.error`；任务创建失败也 alert 后端 payload message。
- 建议重构方式：统一 error code -> frontend message map；后端 message 进入 debug/detail。
- 是否可抽共享包：可抽 frontend `errorPresentation`。
- 建议目标模块：`src/services/errorPresentation.js`。
- 设计模式建议：Presenter/Mapper。
- 影响范围：QA、upload、admin/profile。
- 风险：低中。
- 测试计划：routingStatus/quota-error-formatting 扩展普通错误映射测试。
- 是否可立即删除：否。
- 删除或迁移前置条件：后端 error code 稳定。

## 4. 可抽共享能力清单

| 能力 | 当前重复位置 | 建议共享模块 | 迁移优先级 |
| -- | ------ | ------ | ----- |
| HTTP base/auth/error handling | `services/api.js`、`api/http.js` | `src/services/httpClient.js` | P1 |
| SSE JSON parsing | `utils/sse.js`、`services/api.js`、`api/chat.js` | `src/services/sseClient.js` | P1 |
| conversation normalize | `services/api.js`、`chatStore.js` | `src/domain/conversationModel.js` | P1 |
| message normalize/terminal metadata | `services/api.js`、`chatStore.js` | `src/domain/messageModel.js` | P1 |
| persistence projection | `chatPersistence.js` | `src/domain/chatSnapshot.js` | P2 |
| stream persist policy | `streamPersistPolicy.js` | `src/domain/persistencePolicy.js` | P3 |
| task replay cursor | `utils/taskReplayCursor.js` | keep/shared in `src/domain/taskReplayCursor.js` | P2 |
| routing/quota error presentation | `utils/routingStatus.js`、`services/quota-error-formatting.js` | `src/services/errorPresentation.js` | P2 |
| file selection/session context | `utils/fileSelection.js`、`utils/chatRequestContext.js`、store `sessionState` | `src/domain/fileContext.js` | P2 |
| API route builders | scattered string templates | `src/services/api/routes.js` | P1 |

## 5. 可清理遗留代码清单

| 代码位置 | 当前状态 | 是否注册 | 是否被引用 | 建议处理 |
| ---- | ---- | ---- | ----- | ---- |
| `src/views/Home.vue` | active live path | 是，router `/` | 是 | 保留，后续拆分 |
| `src/stores/chatStore.js` | active live path | Pinia store | 是 | 保留 façade，内部拆分 |
| `src/services/api.js` | active live path | 不适用 | 是 | 拆 API clients，保留 façade |
| `src/api/http.js` | deprecated but still referenced | 不适用 | `src/api/*` 引用 | 若 features 下线则清理；否则并入 canonical clients |
| `src/api/chat.js` | scaffold / deprecated and unregistered | 未见 router 入口 | `features/chat` 引用 | 确认 features 使用后处理 |
| `src/api/conversation.js` | deprecated but still referenced | 未见 router 入口 | `features/chat`/`features/controls` 引用 | 统一到 canonical clients |
| `src/features/chat/components/ChatPanel.vue` | deprecated and unregistered | 未见 router/App 引用 | structure tests | 若无产品入口，归档 |
| `src/features/chat/composables/useChatSession.js` | deprecated and unregistered | 未见 router/App 引用 | features 内引用 | 下线或改 canonical API |
| `src/features/controls/*` | deprecated and unregistered | 未见 router/App 引用 | unknown | 调用图确认 |
| `frontend-vue/dist/` | archive / generated | 不适用 | 不应作为源码 | 忽略，不进入重构 |
| `/api/v1/{mode}/ask_stream` frontend path | unknown/missing | 不适用 | 未见调用 | 与 gateway contract 对齐 |
| `/api/v1/tasks` frontend path | active live path | 不适用 | 是 | 保留 rollout path |

## 6. 接口与契约风险

- gateway -> backend contract：前端默认使用 `/api/{mode}/ask_stream`，不使用 `/api/v1/{mode}/ask_stream`；需要明确 canonical。
- frontend -> gateway contract：`/api/v1/tasks*` 是 rollout path，`/api/{mode}/ask_stream` 是默认 legacy path。
- backend -> public-service contract：conversation/file/upload/quota 都通过 gateway proxy 路径 `/api/*`。
- internal token/auth headers：frontend 只处理 user bearer/localStorage，不应关心 internal token。
- SSE event schema：前端直接消费 `type/message/error/code/data/done`，并可能展示后端 message。
- task event schema：`activeTask.last_seq`、`replay_available`、`task_id` 被 store/Home 直接依赖。

## 7. 测试计划

- 单元测试：domain normalizers、route builders、error presentation、persistence projection。
- contract test：每个 API client URL/method/header/payload。
- stream/SSE test：legacy ask_stream、task replay stream、error/done/content events。
- integration smoke test：Home send message with gateway mock；upload + send；task recovery attach。
- backward compatibility test：`api` façade 保持旧方法名和返回 shape。
- failure/cancel/retry test：cancel task fail keeps busy、network interruption、task replay fallback refresh。
- persistence test：runtime-only fields 不进 localStorage，terminal edge force persist。
- quota/auth test：auth token clear/redirect、quota error formatting。
- file route test：upload/delete/download selected-file context。

## 8. 建议重构顺序

1. P1：固化 API URL contract，特别是 ask_stream 与 tasks paths。
2. P1：拆 `services/api.js` 为 clients，但保留 `api` façade。
3. P1：定义 chat/message durable schema，把 `chatPersistence` 改投影。
4. P1：拆 `chatStore`，先抽 runtime/task/upload/kb stores，保留 façade。
5. P1：给 `Home.vue` 增 selectors/actions，减少深层 store 访问。
6. P2：确认并清理 `src/api/*` 与 `features/chat/controls` 未注册骨架。
7. P2：把后端 message 透传改为 error code mapper。
8. P3：把 streamPersistPolicy 上移到 domain scheduler。

## 9. 需要进一步确认的问题

1. gateway 是否同时支持 `/api/{mode}/ask_stream` 与 `/api/v1/{mode}/ask_stream`；前端当前没有调用 v1 mode stream。
2. `VITE_REFRESH_SURVIVABLE_QA_TASKS_ENABLED` 生产默认值是什么；代码默认 false。
3. `features/chat` / `features/controls` 是否计划恢复使用。
4. KB “status” 是否有正式 endpoint；当前只发现 `/api/kb_info` 和 `/api/refresh_kb`。
5. 是否允许前端继续展示后端 `message/error`。
6. 需要补浏览器级 E2E：多 chat 同时生成、刷新后 replay、取消 task、网络中断后恢复。

## 第二轮深度补充

> 执行身份：Agent 6 frontend-vue 深度只读重构审计。执行范围严格限定 `frontend-vue/` 代码证据；未修改业务源码、配置、测试、脚本、README、依赖文件。第二轮仅追加本文档。

### A. 必跑命令结果复核

- 已执行 `find frontend-vue -type f`：结果包含 `node_modules/`、`dist/`、`.runtime/`、`.env.local`，因此文件清单必须区分源码与生成/依赖噪声；源码范围以 `frontend-vue/src/`、`frontend-vue/tests/`、`package.json`、`vite.config.js` 为准。
- 已执行 `find frontend-vue -type f \( -name "*.py" -o -name "*.js" -o -name "*.vue" \) -print0 | xargs -0 wc -l | sort -nr | head -50`：Top 50 被 `node_modules` 淹没；补充聚焦 `frontend-vue/src` 后，最大文件为 `src/views/Home.vue` 4496 行、`src/stores/chatStore.js` 1965 行、`src/services/api.js` 941 行、`src/utils/recoverableTaskController.js` 666 行。
- 已执行 `rg "APIRouter|@router|app.include_router|path:|fetch|axios|EventSource" frontend-vue`：前端源码没有 FastAPI router 注册；网络调用主要为 `fetch`、`XMLHttpRequest`、自研 SSE reader，无 `axios`/`EventSource` 直接使用。
- 已执行 legacy/deprecated/fallback/scaffold/TODO 关键词扫描：源码命中集中在 `legacyStreamingState`、`src/api/chat.js` 的 legacy pdf payload、`features/*` 未注册骨架、`body.legacy-ui` 样式、quota/department legacy alias、task recovery fallback。
- 已执行 `rg "app\.state|request\.app\.state" frontend-vue`：无源码命中。
- 已执行后端配置变量扫描：源码只出现 `TOKEN` 前端 auth token 相关代码；未发现 `LLM_`、`EMBEDDING_`、`RERANK`、`REDIS`、`MINIO`、`NEO4J`、`VECTOR_DB`、`RESOURCE_ROOT`、`RUNTIME_ROOT`、`STATE_ROOT` 等后端运行配置被前端读取。
- 已执行 OpenAI/stream/SSE/Bearer 扫描：源码命中集中在 `Bearer` auth header、`stream`/`SSE` 自研 reader、markdown streaming tests；未发现 OpenAI/httpx/api_key 直连。
- 已执行 gateway metadata 扫描：源码命中 `requested_mode`、`actual_mode`、`source_scope`、`selected_file_ids`，未命中 `gateway-owned`、`X-Gateway`、`execution_files`、`primary_file_id`。

### B. 第一轮结论复核

- 复核成立：live path 为 `router '/' -> Home.vue -> useChatStore -> services/api.js -> /api/*`。证据：`router/index.js:17-29` 只注册 `Home.vue` 为根路由；`main.js:7-11` 只挂载 Pinia/router；`vite.config.js:12-20` 将 `/api` 和 `/health` 代理到 `http://127.0.0.1:8101`。
- 复核成立：`Home.vue` 和 `chatStore.js` 是第一优先拆分对象。源码统计与职责证据比第一轮更强：`Home.vue:52-56` 自持 stream/task/file selection runtime，`Home.vue:1004-1255` 实现 gateway event reducer，`chatStore.js:17-39` 同时持久化 chat、runtime、KB、user、session files，`chatStore.js:1903-1964` 暴露 60+ store API。
- 复核成立：`services/api.js` 是混合 API façade。`api.js:425-941` 同一个对象同时包含 conversation CRUD、upload、KB、ask stream、task、translation、PDF、summary。
- 复核修正：第一轮表中 `/api/v1/{mode}/ask_stream` 不是当前 frontend live path；源码实际为 `/api/{mode}/ask_stream`，证据 `api.js:626-630`、`src/api/chat.js:78-80`。`/api/v1/tasks` 是 refresh-survivable task path，证据 `api.js:703-710`。
- 复核补充：`src/api/*` 并非完全 dead code，它被 `features/chat`、`features/controls` composables 引用；但这些 features 未被 router/App 注册。证据：`useChatSession.js:1-8`、`useKnowledgeWorkspace.js:1-3`，以及 route 表 `router/index.js:17-29`。
- 复核补充：`dist/` 不是审计源码证据；必跑 grep 会命中 `dist/assets/index-*.js`，但结论均以 `src/`、`tests/`、配置文件为准。

### C. `api.js` 深挖

#### C.1 `src/services/api.js` 函数与路径清单

| 函数 | 方法/路径 | 证据 | 调用位置 | gateway/backend | SSE | 当前测试 |
| --- | --- | --- | --- | --- | --- | --- |
| `createConversation` | POST `/api/conversations` | `api.js:430-435` | `chatStore.js:1244`, `1332` | 走 gateway proxy `/api` | 否 | store mock tests |
| `getConversationList` | GET `/api/conversations?page=&page_size=` | `api.js:449-456` | `chatStore.js:895` | gateway | 否 | `chatStore.task-recovery.test.js` |
| `getConversationDetail` | GET `/api/conversations/{id}` | `api.js:471-494` | `chatStore.js:1062`, `Home.vue:323` | gateway | 否 | `api.structure.test.js`, store tests |
| `addMessage` | POST `/api/conversations/{id}/messages` | `api.js:497-503` | `chatStore.js:1446` | gateway | 否 | 结构/间接 |
| `updateConversationTitle` | PUT `/api/conversations/{id}/title` | `api.js:506-522` | `chatStore.js:854` | gateway | 否 | 间接 |
| `deleteConversation` | DELETE `/api/conversations/{id}` | `api.js:525-530` | `chatStore.js:1195` | gateway | 否 | 间接 |
| `removePdfFromConversation` | DELETE `/api/conversations/{id}/files/{file_id}` | `api.js:533-544` | `chatStore.js:1657`, `1692` | gateway | 否 | 间接 |
| `uploadExcel` | POST `/api/upload_excel` | `api.js:547-581` | `Home.vue:1766`, `chatStore.js:1716` | gateway | 否 | 结构/间接 |
| `removeExcelFromConversation` | DELETE `/api/conversations/{id}/files/{file_id}` | `api.js:584-595` | `chatStore.js:1777`, `1812` | gateway | 否 | 间接 |
| `getKbInfo` | GET `/api/kb_info` | `api.js:600-604` | `Home.vue:1653`, `chatStore.js:1474` | gateway | 否 | feature/API indirect |
| `askStream` | POST `/api/{mode}/ask_stream` or `/api/ask_stream` | `api.js:607-681` | `Home.vue:2016-2024` | gateway | 是，自手写 reader | `api.structure.test.js`, Home structure |
| `createTask` | POST `/api/v1/tasks` | `api.js:683-710` | `recoverableTaskController.js:589-596` | gateway | 否 | `recoverableTaskController.test.js`, structure |
| `getTask` | GET `/api/v1/tasks/{task_id}` | `api.js:713-718` | `recoverableTaskController.js:427` | gateway | 否 | recovery tests |
| `streamTaskEvents` | GET `/api/v1/tasks/{task_id}/events?after_seq=` | `api.js:720-752` | `recoverableTaskController.js:346-385` | gateway | 是 | recovery tests |
| `getTaskEvents` | GET `/api/v1/tasks/{task_id}/events?after_seq=` | `api.js:755-763` | 未见源码调用 | gateway | 返回 JSON | structure only/缺口 |
| `cancelTask` | POST `/api/v1/tasks/{task_id}/cancel` | `api.js:765-770` | `recoverableTaskController.js:511` | gateway | 否 | recovery tests |
| `ask` | POST `/api/{mode}/ask` or `/api/ask` | `api.js:772-788` | 未见 live 调用 | gateway | 否 | 缺口 |
| `translate` | POST `/api/translate` | `api.js:790-822` | `PdfReader.vue:702` | gateway | 否 | PdfReader structure |
| `translateDocument` | POST `/api/translate_document` | `api.js:824-832` | 未见 live 调用 | gateway | 否 | structure |
| `translateDocumentStream` | POST `/api/translate_document` | `api.js:835-870` | `PdfReader.vue:808` | gateway | 是 | `PdfReader.structure.test.js` |
| `viewPdf` | URL `/api/view_pdf/{doi}` | `api.js:873-875` | 间接/可被 UI 使用 | gateway | 否 | 缺口 |
| `summarizePdf` | POST `/api/summarize_pdf/{doi}` | `api.js:877-881` | `PdfReader.vue:662` | gateway | 否 | structure/缺口 |
| `uploadPdf` | XHR POST `/api/upload_pdf` | `api.js:884-938` | `Home.vue:1754` | gateway | 否 | structure/间接 |

#### C.2 结论

- 是否全部走 gateway：当前 live 源码没有硬编码旧后端 host；路径均为相对 `/api` 或 `VITE_API_BASE_URL + /api`，开发代理默认 gateway `127.0.0.1:8101`。因此“是否直连旧后端”的代码结论为：未发现直连旧后端 URL，但 `VITE_API_BASE_URL` 可被部署配置改成任意 base，需部署审计另行确认。
- 路径拼接混乱：`const V1 = '/api'` 命名不准确，task 路径拼成 `${V1}/v1/tasks`，ask stream 却拼 `${V1}/${mode}/ask_stream`。这造成 `/api/v1/tasks` 与 `/api/{mode}/ask_stream` 并存，且 README 所述 `/api/v1/{mode}/ask_stream` 没有 live 调用证据。
- SSE 实现重复：`askStream` 内部手写 reader `api.js:651-680`，而 task/document stream 使用 `streamSseJson` `api.js:752`, `870`。两套 parser 对多行 `data:`、错误 payload 的行为不一致。
- 目标拆分：`qaStreamApi` 负责 ask/ask_stream 和 SSE parser；`taskApi` 负责 create/get/events/cancel；`conversationApi` 负责 conversation CRUD/message/title；`uploadApi` 负责 PDF/Excel upload；`documentApi` 负责 view/summarize/translate/translateDocument；`quotaApi` 继续从 `services/quota.js`/`api/quota.js` 收敛；`authApi` 继续独立但共享 http/auth error client。

### D. `chatStore` 深挖

#### D.1 持久化 vs runtime-only

| 分类 | 字段/行为 | 证据 | 判断 |
| --- | --- | --- | --- |
| durable | `chats/currentChatId/messages/pdf_list/excel_list/uploaded_files/activeTask/lastTaskSeq/isPinned/queryMode` | `chatStore.js:964-1003`, `1851-1899` | 应进入 localStorage projection |
| durable-ish | failed/canceled terminal metadata | `chatStore.failed-terminal.test.js:23-71`, `73-121` | 应持久化，刷新后不再 loading |
| runtime-only | `legacyStreamingState/chatBusyRuntime` | `chatStore.js:20-21`, `85-94` | 不应进入 durable chat |
| runtime-only | `Home.vue streamRuntimeByChatId` with `abortController/pendingContent/flushFrame/pollTimer/eventState` | `Home.vue:52-53`, `175-194` | 必须组件/服务 runtime-only |
| session/local | `sessionState.initialPdfIds/newlyUploadedPdfIds/lastUsedPdfIds` | `chatStore.js:34-39` | 当前命名与 Excel 混用，容易污染 |

#### D.2 关键发现

- conversation list/current/messages：`loadChats()` 先恢复 localStorage，再拉服务器 conversation list，并用 `sanitizeChats()` 合并，证据 `chatStore.js:869-960`。`switchChat()` 又拉 detail 并直接覆写 `chat.messages/pdf_list/excel_list/activeTask`，证据 `chatStore.js:1033-1185`。
- streaming/busy runtime：store 内 `chatBusyRuntime` 负责并发容量与 busy 标记，Home 内 `streamRuntimeByChatId` 负责 abort/pending/SSE target。双源 runtime 通过 `requestId` 和 targetIndex 软关联，证据 `chatStore.js:141-199`、`Home.vue:175-237`。
- multi-chat concurrency：上限 5 个 busy chat，证据 `chatStore.js:14`, `50-67`, `141-151`；测试覆盖 `chatStore.concurrent-streaming.test.js:113-130`。
- upload files/selected files：store 保存 `pdf_list/excel_list`，Home 保存 `selectedFileIds/pendingDraftFiles`，并构造 `requestChatContext`，证据 `Home.vue:54-56`, `1689-1695`, `1747-1825`。
- selected/session 污染风险：Excel 上传也写入 `sessionState.value.newlyUploadedPdfIds`，证据 `chatStore.js:1549-1552`；后续又通过 `getNewlyUploadedPdfIds()` 筛 PDF、`getNewlyUploadedFileIds()` 筛全部，证据 `chatStore.js:1615-1626`。字段名与语义不一致。
- KB info：Home 和 store 都能调用 `getKbInfo()` 并写 `store.kbInfo`，证据 `Home.vue:1651-1667`、`chatStore.js:1471-1485`。
- task recovery：activeTask/lastTaskSeq 持久化在 chat 上，controller 依赖 `api.createTask/getTask/streamTaskEvents/cancelTask`，证据 `recoverableTaskController.js:518-627` 和 `196-505`。
- cancel/failed terminal：legacy cancel 在 Home 本地打 `terminalStatus: canceled`，task cancel 调 controller，再 replay gateway event；证据 `Home.vue:2102-2142`、`recoverableTaskController.js:507-516`。
- route mode：`selectedAskMode` 存 localStorage `gateway.ask.mode.v1`，请求体写 `requested_mode`，后端实际模式通过 event metadata 回填，证据 `Home.vue:42`, `56`, `403-405`、`api.js:616`, `692`。
- 组件直接修改内部字段：Home 直接改 `store.currentChatId = null`，证据 `Home.vue:1677-1680`；直接改 `liveChat.messages/pdf_list/excel_list/uploaded_files`，证据 `Home.vue:327-344`；直接改 message `stepsCollapsed`，证据 `Home.vue:700-704`。
- 长 action：`addUserMessage()` 负责创建服务器 conversation、迁移 temp id、写 message、标题、sync 状态，证据 `chatStore.js:1221-1317`；`switchChat()` 153 行左右，负责 server detail merge 与 task metadata。
- 目标结构：`useChatRuntime()` 管 Home runtime；`streamStateMachine` 统一 event reducer；`conversationStore` 管 list/current/detail merge；`messageStore` 管 message append/update/terminal；`uploadStore` 管 files/session selected context；`taskRecoveryStore` 管 activeTask/replay cursor；`kbStore` 管 KB info。

### E. persistence 深挖

- `chatPersistence.js` 当前是 blacklist sanitizer：`RUNTIME_ONLY_MESSAGE_FIELDS = ['streamRequestId']`，`RUNTIME_ONLY_CHAT_FIELDS = ['busyRuntime', 'chatBusyRuntime']`，证据 `chatPersistence.js:1-3`、`20-45`。
- runtime-only 排除已覆盖 `streamRequestId` 和 chat-level busy snapshot，测试证据 `chatPersistence.test.js:68-120`, `143-170`。
- debounce 策略独立：streaming 时 1200ms debounce，非 streaming/force 立即持久化，证据 `streamPersistPolicy.js:1-15`、`streamPersistPolicy.test.js:12-56`。
- terminal edge force persist：runtime busy 从 true 到 false 时 force persist，证据 `chatStore.js:85-94`, `1458-1464`。
- failed/canceled terminal durable：测试明确 failed/canceled 刷新后 `isComplete=true`，证据 `chatStore.failed-terminal.test.js:23-121`。
- 刷新恢复依赖 gateway task API：`recoverableTaskController` 对 recoverable task 使用 `streamTaskEvents`，无事件时用 `getTask`，失败 fallback 到 conversation truth，证据 `recoverableTaskController.js:346-385`, `427-459`, `479-495`。
- 测试缺口：没有 schema projection 白名单测试；没有覆盖 `abortController/pendingContent/flushFrame/pollTimer/eventState` 若误挂到 chat/message 时是否排除；没有 URL contract 测试保证 task replay endpoint 与 ask_stream endpoint 不被拆分时改错；没有真实 SSE parser contract 覆盖多行 `data:` 行为差异。

### F. UI 文案与后端事件 schema

- 后端阶段文案依赖：`Home.vue:745-792` 用中文 `阶段...` 正则拆 step 文案；`stageTimings.js:11-45` 内置中文阶段 label/description 与映射。若后端 message 改成英文/结构化 code，UI 展示会退化。
- event code + frontend mapper：路由/配额错误已有 `buildRoutingErrorPresentation`，但普通错误仍透传 `data.message || data.error`，证据 `Home.vue:1196-1237`、legacy catch `Home.vue:2035-2053`。
- message event schema 稳定性：Home event reducer 直接 switch `data.type` 为 `state/thinking/step/metadata/content/done/error`，证据 `Home.vue:1019-1255`。`done` 依赖 `final_answer/reference_links/reference_objects/doi_locations/used_files/timings`，`error` 依赖 `code/message/error/data`。
- 目标：后端 event 发 `code/stage_code/status/payload`，前端 mapper/i18n 将 code 映射中文；保留 `message` 作为 debug/detail，不作为主 UI 文案唯一来源。

### G. router/API 完整表

| 路由/API | 方法 | 文件 | 调用位置 | gateway/backend | SSE | 测试覆盖 |
| --- | --- | --- | --- | --- | --- | --- |
| `/` | route | `router/index.js:17-18` | `App.vue:5-7` | 前端 route | 否 | `Home.structure.test.js` |
| `/login` | route | `router/index.js:19` | login UI | 前端 route | 否 | auth tests |
| `/register` | route | `router/index.js:20` | register UI | 前端 route | 否 | `register-route.test.js` |
| `/forgot-password` | route | `router/index.js:21` | forgot UI | 前端 route | 否 | structure |
| `/admin` | route | `router/index.js:22` | admin UI | 前端 route | 否 | admin tests |
| `/profile` | route | `router/index.js:23` | profile UI | 前端 route | 否 | profile tests |
| `/quota-management` | redirect | `router/index.js:24-28` | redirects admin quota tab | 前端 route | 否 | route/admin tests |
| `/api/auth/*` | mixed | `services/auth.js:138-313` | router guard/login/profile | gateway | 否 | auth tests |
| `/api/auth/departments/tree`, `/api/auth/department` | GET/PUT | `services/departments.js:156-172` | profile department flow | gateway | 否 | departments tests |
| `/api/admin/model-status*` | GET/POST | `services/admin.js:147-160` | AdminDashboard | gateway | 否 | admin model status tests |
| `/api/admin/users*` | mixed | `services/admin.js:163-278` | AdminDashboard/personnel panels | gateway | 否 | structure |
| `/api/admin/personnel*` | mixed | `services/admin.js:286-407` | PersonnelManagement | gateway | 否 | structure |
| `/api/admin/departments*` | mixed | `services/admin.js:410-607` | DepartmentManagement | gateway | 否 | department tests |
| `/api/quota/my` | GET | `services/quota.js:18-34`, `api/quota.js:5-7` | Quota UI/admin/features | gateway | 否 | quota normalization/formatting |
| `/api/quota/configs` | GET/POST/PUT | `services/quota.js:38-84`, `api/quota.js:9-19` | QuotaManagement | gateway | 否 | quota tests |
| `/api/quota/users/{id}` | GET | `services/quota.js:100-113`, `api/quota.js:21-23` | quota admin | gateway | 否 | quota tests |
| `/api/quota/users/{id}/reset` | POST | `services/quota.js:87-97`, `api/quota.js:25-27` | quota admin | gateway | 否 | quota tests |
| `/api/conversations*` | mixed | `services/api.js:430-530`, `api/conversation.js:5-33` | Home/store/features | gateway | 否 | store/API structure |
| `/api/conversations/{id}/files/{file_id}/download` | GET | `Home.vue:2312-2322`, `api/conversation.js:32-42` | Home download/features | gateway | 否 | structure only |
| `/api/upload_pdf`, `/api/upload_excel` | POST | `services/api.js:547-581`, `884-938`; `api/chat.js:40-55` | Home/store/features | gateway | 否 | structure/indirect |
| `/api/kb_info` | GET | `services/api.js:600-604`, `api/chat.js:24-26` | Home/store/features | gateway | 否 | indirect |
| `/api/refresh_kb`, `/api/clear_cache`, `/api/clear_pdf` | POST | `api/chat.js:28-38` | `features/controls` only | gateway if registered | 否 | weak/none |
| `/api/{mode}/ask_stream` | POST | `services/api.js:607-681`, `api/chat.js:58-119` | Home legacy/features | gateway | 是 | structure |
| `/api/v1/tasks*` | mixed | `services/api.js:683-770` | recoverable task controller | gateway | events 是 | recovery tests |
| `/api/translate`, `/api/translate_document` | POST | `services/api.js:790-870` | PdfReader | gateway | document stream 是 | structure |
| `/api/view_pdf/{doi}`, `/api/patent/original/{id}` | GET | `services/api.js:873-875`, `Home.vue:1277-1284`, `api/literature.js` | PdfReader/markdown links | gateway | 否 | literature tests |
| `/api/summarize_pdf/{doi}` | POST | `services/api.js:877-881` | PdfReader | gateway | 否 | structure |

### H. legacy/deprecated/scaffold 引用验证

- `src/features/chat/*`：存在 `ChatPanel.vue`、`SidebarPanel.vue`、`useChatSession.js`，但 router/App 未注册；内部使用 `src/api/conversation.js` 与 `src/api/chat.js`，证据 `useChatSession.js:1-8`。
- `src/features/controls/*`：未注册，但 `useKnowledgeWorkspace.js` 调用 `refresh_kb/clear_cache/clear_pdf/upload*`，证据 `useKnowledgeWorkspace.js:1-3`, `28-45`, `82-92`。
- `src/api/chat.js`：另一套 API façade，包含 legacy pdf payload `legacy_use_pdf/legacy_pdf_path`，证据 `src/api/chat.js:101-105`。
- `styles/main.css`：存在 `body.legacy-ui` 大量样式，关键词扫描命中，但是否仍可通过 body class 激活未在 router/App 中确认。
- `dist/`：必扫命令命中大量压缩构建产物；审计不把它作为源码重构目标。

### I. 新增具体重构点

以下 `R-010` 至 `R-021` 均为第二轮深度补充，所属服务均为 `frontend-vue`。每个条目的 `当前状态` 以对应调用链为准：`Home.vue -> chatStore -> services/api.js` 为 `active live path`；`src/api/*` 与未注册 `features/*` 为 `scaffold / unknown，需要进一步验证`；后端中文文案依赖为 `active live path / frontend-backend coupling`。本节所有条目均按第二轮模板补充代码位置、行号范围、接口路径、当前调用链、关键代码片段、目标结构、迁移步骤、兼容/回滚、测试计划、风险和阻塞项。

### R-010：统一 QA stream 路径构建与版本契约

- 来源：第二轮深度补充
- 所属服务：frontend-vue
- 当前状态：active live path

- 严重程度：P1
- 类型：API contract / route builder
- 代码位置：
  - `frontend-vue/src/services/api.js:24-27`
  - `frontend-vue/src/services/api.js:626-630`
  - `frontend-vue/src/api/chat.js:78-80`
- 接口路径：`POST /api/{mode}/ask_stream`、`POST /api/ask_stream`、待确认 `/api/v1/{mode}/ask_stream`
- 当前调用链：`Home.vue:2016-2024 -> api.askStream() -> fetch(${API_BASE}${askPath}) -> gateway /api proxy`
- 关键片段：

```js
const V1 = '/api';

const askPath = ['fast', 'thinking', 'patent'].includes(normalizedMode)
  ? `${V1}/${normalizedMode}/ask_stream`
  : `${V1}/ask_stream`;

const response = await fetch(`${API_BASE}${askPath}`, {
```

- 当前问题：`V1` 实际是 `/api`，task 使用 `/api/v1/tasks`，QA stream 使用 `/api/{mode}/ask_stream`；README/第一轮要求提及的 `/api/v1/{mode}/ask_stream` 无 live 调用证据。
- 目标结构：`src/services/api/routes.js` 输出 `buildAskStreamPath(mode)`、`buildTaskPath(taskId?)`；`qaStreamApi` 只引用 route builder。
- 迁移步骤：先新增 route builder contract test；保留当前默认 path；加 feature flag 或 gateway capability 配置切到 v1 path；最后删除散落字符串。
- 兼容/回滚：`api.askStream` façade 方法名和参数不变；route builder 可一键回滚到 `/api/{mode}/ask_stream`。
- unit 测试计划：mode normalize、fast/thinking/patent/unknown path。
- contract 测试计划：锁定 fetch URL、method、headers、body `requested_mode`。
- router 测试计划：无前端 route 变化，仅补 Home send smoke。
- stream 测试计划：legacy SSE content/done/error frame。
- integration 测试计划：mock gateway 同时支持 old/v1，验证 fallback。
- regression 测试计划：`Home.structure.test.js` 中“snapshot per-chat request context”保持。
- 风险：高，路径错会直接断 QA。
- 阻塞项：需要 gateway owner 确认 canonical ask_stream path。

### R-011：拆分 `services/api.js` 为 typed clients 并保留 façade

- 来源：第二轮深度补充
- 所属服务：frontend-vue
- 当前状态：active live path

- 严重程度：P1
- 类型：API boundary / 巨型模块拆分
- 代码位置：
  - `frontend-vue/src/services/api.js:425-941`
- 接口路径：conversation、upload、KB、ask/ask_stream、task、translate、PDF
- 当前调用链：`Home.vue`、`chatStore.js`、`PdfReader.vue`、`recoverableTaskController.js` 均 import 同一个 `api` 对象。
- 关键片段：

```js
export const api = {
  refreshSurvivableQATasksEnabled: REFRESH_SURVIVABLE_QA_TASKS_ENABLED,
  async createConversation(_userId, title = '新对话') { ... },
  async *askStream(question, chatHistory = [], conversationId = null, pdfContext = null, signal = undefined, mode = 'thinking') { ... },
  async createTask(question, chatHistory = [], conversationId = null, pdfContext = null, mode = 'thinking', clientRequestId = '') { ... },
  async translateDocumentStream(documentType, documentId, options = {}) { ... },
  async uploadPdf(file, conversationId, onProgress) { ... },
};
```

- 当前问题：941 行文件混合 HTTP client、normalizers、conversation、upload、QA、task、document、auth error handling；任意改动容易误伤 unrelated API。
- 目标结构：`httpClient.js`、`sseClient.js`、`conversationApi.js`、`qaStreamApi.js`、`taskApi.js`、`uploadApi.js`、`documentApi.js`、`kbApi.js`、`api.js` compatibility façade。
- 迁移步骤：复制现有函数到新 clients；为 façade re-export；先让测试只断言 façade 行为；逐步迁移调用方 import。
- 兼容/回滚：保留 `api.createConversation/api.askStream/...` 原签名；异常 shape 保持 `status/code/payload`。
- unit 测试计划：每个 client URL/body/header normalizer。
- contract 测试计划：覆盖 C.1 全表所有 path。
- router 测试计划：无 route 改动。
- stream 测试计划：QA stream 与 task stream 共用 parser。
- integration 测试计划：Home send、PdfReader summarize/translate、upload。
- regression 测试计划：现有 `api.structure.test.js` 转成 façade 兼容测试。
- 风险：高，拆分期间 import 循环和 path 误拼风险大。
- 阻塞项：先冻结 API 函数清单和返回 shape。

### R-012：把 `Home.vue` event reducer 抽成 stream state machine

- 来源：第二轮深度补充
- 所属服务：frontend-vue
- 当前状态：active live path

- 严重程度：P1
- 类型：stream/event state machine
- 代码位置：
  - `frontend-vue/src/views/Home.vue:1004-1255`
- 接口路径：`POST /api/{mode}/ask_stream`、`GET /api/v1/tasks/{task_id}/events`
- 当前调用链：`api.askStream/streamTaskEvents -> applyGatewayEvent -> updateStreamingTargetMessage -> store persistence`
- 关键片段：

```js
if (data.type === 'thinking') { ... }
if (data.type === 'step') { ... }
if (data.type === 'metadata') { ... }
if (data.type === 'content') { ... }
if (data.type === 'done') { ... }
if (data.type === 'error') { ... }
```

- 当前问题：同一函数处理 dedupe、task cursor、step parsing、patent streaming、content buffer、terminal metadata、error presentation；UI 组件承担协议解释。
- 目标结构：`src/domain/streamEventReducer.js` 纯 reducer + `src/composables/useChatStreamRuntime.js` side effects；Home 只订阅 state patch。
- 迁移步骤：先抽纯函数处理 `state/thinking/step/metadata/done/error`；再抽 content buffer；最后接入 legacy stream/task stream 两路。
- 兼容/回滚：保留 `applyGatewayEvent(chatId,data,runtime)` 作为 façade 调新 reducer。
- unit 测试计划：每类 event 输入输出 message patch。
- contract 测试计划：事件 schema snapshot。
- router 测试计划：无。
- stream 测试计划：duplicate seq、late content after done、error after done。
- integration 测试计划：legacy stream 与 task replay 共享 reducer。
- regression 测试计划：patent streaming tests、routingStatus tests。
- 风险：高，terminal edge 和 pending content 容易丢。
- 阻塞项：明确 gateway event schema 是否稳定。

### R-013：合并 Store runtime 与 Home runtime 双源状态

- 来源：第二轮深度补充
- 所属服务：frontend-vue
- 当前状态：active live path

- 严重程度：P1
- 类型：runtime state separation / concurrency
- 代码位置：
  - `frontend-vue/src/stores/chatStore.js:20-21`
  - `frontend-vue/src/stores/chatStore.js:141-199`
  - `frontend-vue/src/views/Home.vue:52-53`
  - `frontend-vue/src/views/Home.vue:175-217`
- 接口路径：`/api/{mode}/ask_stream`、`/api/v1/tasks/*`
- 当前调用链：`sendLegacyMessage -> store.startChatBusyRuntime -> Home.createStreamRuntime -> api.askStream -> store.finishChatBusyRuntime`
- 关键片段：

```js
const chatBusyRuntime = ref({})
const streamRuntimeByChatId = new Map()

runtimeMap[normalizedChatId] = {
  chatId: normalizedChatId,
  phase: nextPhase,
  stopRequested: false,
  requestId: String(options?.requestId || '').trim(),
}
```

- 当前问题：busy/capacity 在 store，abort/pendingContent/eventState 在 Home；两个 runtime map 需要手动同步，temp chat promotion 时还要 `moveChatBusyRuntime()`。
- 目标结构：`useChatRuntimeStore` 或 composable 统一 runtime entry：busy phase、abort controller、request id、target index、pending content、event state；durable store 只持 activeTask/cursor。
- 迁移步骤：先封装 Home runtime Map 操作；再让 store busy runtime 代理到 runtime composable；最后删除 `legacyStreamingState`。
- 兼容/回滚：`store.isChatBusy/startChatBusyRuntime/...` 保持 façade。
- unit 测试计划：capacity、same chat duplicate、temp id migration、stop requested。
- contract 测试计划：无 API path 改动。
- router 测试计划：无。
- stream 测试计划：abort/cancel 后 pending flush。
- integration 测试计划：5 个并发 + 第 6 个拒绝；切会话时 stream 继续。
- regression 测试计划：`chatStore.concurrent-streaming.test.js` 全量迁移。
- 风险：高，busy 状态错会导致重复生成或无法发送。
- 阻塞项：需要先抽 reducer，减少 Home 对 runtime 的直接依赖。

### R-014：将 `chatPersistence` 从 blacklist 改成 durable projection schema

- 来源：第二轮深度补充
- 所属服务：frontend-vue
- 当前状态：active live path

- 严重程度：P1
- 类型：persistence hygiene / schema
- 代码位置：
  - `frontend-vue/src/stores/chatPersistence.js:1-45`
  - `frontend-vue/src/stores/chatStore.js:964-1003`
- 接口路径：localStorage `lfp_chats`、`lfp_current_chat_id`
- 当前调用链：`saveChats -> prepareChatsForPersistence -> localStorage.setItem`
- 关键片段：

```js
const RUNTIME_ONLY_MESSAGE_FIELDS = ['streamRequestId']
const RUNTIME_ONLY_CHAT_FIELDS = ['busyRuntime', 'chatBusyRuntime']

function sanitizeChat(chat = {}) {
  const sanitized = cloneValue(chat)
  for (const field of RUNTIME_ONLY_CHAT_FIELDS) {
    delete sanitized[field]
  }
  sanitized.messages = Array.isArray(chat?.messages) ? chat.messages.map((message) => sanitizeMessage(message)) : []
  return sanitized
}
```

- 当前问题：blacklist 只知道现有 runtime 字段；未来若 `abortController/pendingContent/pollTimer/eventState` 被误挂入 chat/message，会被完整 clone 进 localStorage。
- 目标结构：`chatSnapshotSchema.js` 白名单输出 durable chat DTO；restore 时只接受 DTO 字段。
- 迁移步骤：先新增 projection 函数并让 tests 双跑；再替换 `prepareChatsForPersistence`；最后删除 runtime blacklist 或仅作为防线。
- 兼容/回滚：restore 接受旧 shape；prepare 可回滚到 blacklist。
- unit 测试计划：白名单字段、unknown field 丢弃、Date ISO。
- contract 测试计划：localStorage snapshot shape。
- router 测试计划：无。
- stream 测试计划：streaming 中 debounce snapshot 不含 runtime。
- integration 测试计划：刷新后 failed/canceled/done 保持，activeTask recoverable 保持。
- regression 测试计划：`chatPersistence.test.js`、`chatStore.failed-terminal.test.js`。
- 风险：中高，白名单漏字段会丢用户数据。
- 阻塞项：需定义 durable chat/message schema。

### R-015：拆分上传/文件选择/session context，修复 `newlyUploadedPdfIds` 语义污染

- 来源：第二轮深度补充
- 所属服务：frontend-vue
- 当前状态：active live path

- 严重程度：P2
- 类型：file context / state naming
- 代码位置：
  - `frontend-vue/src/stores/chatStore.js:34-39`
  - `frontend-vue/src/stores/chatStore.js:1488-1626`
  - `frontend-vue/src/views/Home.vue:1331-1456`
  - `frontend-vue/src/views/Home.vue:1747-1825`
- 接口路径：`POST /api/upload_pdf`、`POST /api/upload_excel`、`DELETE /api/conversations/{id}/files/{file_id}`、download path
- 当前调用链：`Home.handleFileSelect -> uploadSingleFileToChat -> api.uploadPdf/uploadExcel -> store.addUploaded*ToChat -> selectedFileIds`
- 关键片段：

```js
const sessionState = ref({
  initialPdfIds: [],
  newlyUploadedPdfIds: [],
  lastUsedPdfIds: []
})

if (options?.trackSession !== false && fileId && !sessionState.value.newlyUploadedPdfIds.includes(fileId)) {
  sessionState.value.newlyUploadedPdfIds.push(fileId)
}
```

- 当前问题：Excel 文件 id 也写进 `newlyUploadedPdfIds`；selected file state 在 Home，file list state 在 store，request context 由 `chatRequestContext` 读取两边。
- 目标结构：`uploadStore` 管 `conversationFiles`；`fileSelectionStore`/`useFileSelection` 管 selected ids；`sessionFileContext` 字段改为 `newlyUploadedFileIds`，并派生 PDF-only。
- 迁移步骤：先新增 alias `newlyUploadedFileIds`，保持旧字段读写；更新 `getNewlyUploadedPdfIds()`；迁移 Home selected ids；最后删除旧字段。
- 兼容/回滚：旧 localStorage 中 `newlyUploadedPdfIds` restore 到新字段。
- unit 测试计划：PDF/Excel 上传、选择、删除、draft upload。
- contract 测试计划：upload response normalization `file_id/file_no/display_no/status`。
- router 测试计划：无。
- stream 测试计划：selected ids 写入 ask/task request context。
- integration 测试计划：上传 PDF+Excel 后提问，确认 payload `selected_file_ids`。
- regression 测试计划：`fileSelection.test.js`、`chatRequestContext.test.js`、Home structure。
- 风险：中，误改会影响“只用选中文件回答”。
- 阻塞项：确认后端 `pdf_context` 对 Excel/CSV 的正式字段名。

### R-016：将文件下载 URL 从 Home 移入 document/file API

- 来源：第二轮深度补充
- 所属服务：frontend-vue
- 当前状态：active live path

- 严重程度：P2
- 类型：API encapsulation / auth token handling
- 代码位置：
  - `frontend-vue/src/views/Home.vue:2312-2322`
  - `frontend-vue/src/api/conversation.js:32-42`
- 接口路径：`GET /api/conversations/{conversation_id}/files/{file_id}/download`
- 当前调用链：`Home download button -> downloadUploadedFile -> window.open(url)`
- 关键片段：

```js
const token = localStorage.getItem('token') || ''
let url = `/api/conversations/${conversationId}/files/${file.file_id}/download`
if (token) {
  url += `?token=${encodeURIComponent(token)}`
}
window.open(url, '_blank')
```

- 当前问题：Home 手写 URL 且只读 `token`，而 `api/conversation.js` 已有类似 builder 但读 `agentcode.auth.token.v1`；两处 token key 不一致。
- 目标结构：`documentApi.buildConversationFileDownloadUrl(conversationId,fileId)` 统一 token key 与 base URL；Home 只调用 builder。
- 迁移步骤：迁移 builder 到 canonical services；让旧 `src/api/conversation.js` re-export；替换 Home。
- 兼容/回滚：保留 query token 方式；若 gateway 改 header download，再新增 signed-url endpoint。
- unit 测试计划：token key 优先级、base URL、encoding。
- contract 测试计划：download URL path。
- router 测试计划：无。
- stream 测试计划：无。
- integration 测试计划：上传后下载按钮 URL。
- regression 测试计划：Home structure 更新为调用 builder。
- 风险：中，下载鉴权失败会影响文件使用。
- 阻塞项：确认 gateway 是否仍支持 query token。

### R-017：把 task recovery 从 Home 依赖注入迁到 store/composable 边界

- 来源：第二轮深度补充
- 所属服务：frontend-vue
- 当前状态：active live path

- 严重程度：P1
- 类型：task recovery architecture
- 代码位置：
  - `frontend-vue/src/views/Home.vue:315-401`
  - `frontend-vue/src/utils/recoverableTaskController.js:19-666`
- 接口路径：`POST /api/v1/tasks`、`GET /api/v1/tasks/{id}`、`GET /api/v1/tasks/{id}/events?after_seq=`、`POST /api/v1/tasks/{id}/cancel`
- 当前调用链：`Home sendTaskMessage -> recoverableTaskController.sendTaskMessage -> api.createTask -> seedLocalTaskMessages -> attachRecoverableTask -> api.streamTaskEvents`
- 关键片段：

```js
const recoverableTaskController = createRecoverableTaskController({
  api,
  store,
  normalizeChatId,
  getChatById,
  getCurrentChatId: () => store.currentChatId,
  taskAttachInFlightByChatId,
  streamRuntimeByChatId,
  applyGatewayEvent,
})
```

- 当前问题：controller 已抽出，但仍依赖 Home 提供 runtime maps、event reducer、scroll/input side effects；刷新恢复与 UI 组件生命周期耦合。
- 目标结构：`useRecoverableTaskSession()` composable 管 attach/cancel/send；`taskRecoveryStore` 管 active attach locks 和 cursors；Home 只传 UI callbacks。
- 迁移步骤：先把 dependency object 收敛为 adapter；移动 `refreshConversationTruth` 到 store action；移动 attach watcher 到 composable。
- 兼容/回滚：保留 `createRecoverableTaskController` 工厂；Home 旧调用可继续。
- unit 测试计划：send/create/attach/cancel/fallback。
- contract 测试计划：task endpoints URL + event schema。
- router 测试计划：无。
- stream 测试计划：event replay after seq、empty batch getTask fallback。
- integration 测试计划：刷新页面后恢复任务。
- regression 测试计划：`recoverableTaskController.test.js` 全量。
- 风险：高，刷新恢复是核心能力。
- 阻塞项：先完成 stream reducer 抽取。

### R-018：建立 event code 到前端 i18n mapper，减少后端中文文案耦合

- 来源：第二轮深度补充
- 所属服务：frontend-vue
- 当前状态：active live path / frontend-backend coupling

- 严重程度：P2
- 类型：UX contract / i18n
- 代码位置：
  - `frontend-vue/src/views/Home.vue:745-811`
  - `frontend-vue/src/views/Home.vue:1196-1237`
  - `frontend-vue/src/utils/stageTimings.js:11-45`
  - `frontend-vue/src/utils/routingStatus.js:56-134`
- 接口路径：stream/task event `step/error/metadata`
- 当前调用链：`gateway event -> Home.splitStepMessage/buildStepPayload/buildRoutingErrorPresentation -> UI`
- 关键片段：

```js
const stageMatch = compact.match(/^(阶段[0-9一二三四五六七八九十百千万点\.]+)(?:\s*[：:]\s*|\s+)(.+)$/u)
const errorText = String(data.message || data.error || '处理失败')
const renderedError = presentation.kind === 'markdown'
  ? presentation.markdown
  : errorText
```

- 当前问题：阶段标题和错误主文案依赖后端中文 message；只有 routing/quota 部分有 mapper。
- 目标结构：`eventPresentation.js`：`stage_code -> title/detail`、`error.code -> userMessage`，支持 zh-CN 默认文案。
- 迁移步骤：先对现有中文 message 做兼容解析；新增 code 优先；逐步要求 gateway 发 `stage_code/error_code`。
- 兼容/回滚：无 code 时继续使用 message fallback。
- unit 测试计划：stage code、旧中文 message、普通 error、quota error。
- contract 测试计划：event schema code/message/payload。
- router 测试计划：无。
- stream 测试计划：step/thinking/error 展示。
- integration 测试计划：模拟英文 backend message 不破坏中文 UI。
- regression 测试计划：`routingStatus.test.js`、`stageTimings.test.js`。
- 风险：中，文案变化影响用户理解。
- 阻塞项：需要后端同意稳定 event code。

### R-019：清理或隔离未注册 `features/*` 与 `src/api/*` 双门面

- 来源：第二轮深度补充
- 所属服务：frontend-vue
- 当前状态：scaffold / unknown，需要进一步验证

- 严重程度：P2
- 类型：legacy/scaffold isolation
- 代码位置：
  - `frontend-vue/src/features/chat/composables/useChatSession.js:1-8`
  - `frontend-vue/src/features/controls/composables/useKnowledgeWorkspace.js:1-3`
  - `frontend-vue/src/api/chat.js:24-119`
  - `frontend-vue/src/router/index.js:17-29`
- 接口路径：`/api/kb_info`、`/api/refresh_kb`、`/api/clear_cache`、`/api/clear_pdf`、`/api/{mode}/ask_stream`、conversation CRUD
- 当前调用链：未注册 features 内部调用 `src/api/*`；live Home/store 使用 `services/api.js`。
- 关键片段：

```js
import { streamAsk } from '../../../api/chat';
import { clearCache, clearPdf, getKbInfo, refreshKb, uploadExcel, uploadPdf } from '../../../api/chat';

const routes = [
  { path: '/', component: Home, meta: { requiresAuth: true } },
  { path: '/admin', component: AdminDashboard, meta: { requiresAuth: true, requiresAdmin: true } },
]
```

- 当前问题：两套 API 和 session model 共存；未来开发者可能误用 unregistered feature composables，绕开 canonical `services/api.js`。
- 目标结构：若保留 features，则全部改 import canonical clients；若下线，则移动到明确 archive 或删除测试引用。
- 迁移步骤：先加 import graph 文档/测试，确认未注册；替换 `src/api/*` 为 façade re-export；产品确认后删除或注册。
- 兼容/回滚：`src/api/*` 保持 re-export 一段时间。
- unit 测试计划：features composables 使用 canonical clients mock。
- contract 测试计划：`refresh_kb/clear_pdf` 是否仍有 gateway endpoint。
- router 测试计划：确认未注册不影响 live routes。
- stream 测试计划：若保留 ChatPanel，streamAsk 与 qaStreamApi 共享 parser。
- integration 测试计划：无注册则不跑 E2E；若注册则加 smoke。
- regression 测试计划：`ChatPanel.structure.test.js`。
- 风险：中，误删可能影响计划恢复入口。
- 阻塞项：产品确认 features/chat/controls/references 是否未来入口。

### R-020：统一 SSE parser，修复 askStream 与 task/document stream 行为差异

- 来源：第二轮深度补充
- 所属服务：frontend-vue
- 当前状态：active live path

- 严重程度：P2
- 类型：SSE parsing / stream reliability
- 代码位置：
  - `frontend-vue/src/services/api.js:651-680`
  - `frontend-vue/src/utils/sse.js:1-36`
  - `frontend-vue/src/services/api.js:752`
  - `frontend-vue/src/services/api.js:870`
- 接口路径：`/api/{mode}/ask_stream`、`/api/v1/tasks/{id}/events`、`/api/translate_document`
- 当前调用链：legacy ask stream uses inline parser；task/document stream use `streamSseJson`
- 关键片段：

```js
const dataLines = lines
  .filter((line) => line.startsWith('data:'))
  .map((line) => line.slice(5).trim());
yield JSON.parse(dataLines.join('\n'));
```

- 当前问题：inline parser 支持多 `data:` join；`streamSseJson` 只取第一条 `data:`，并把 parse error 作为 `{type:'error'}` 事件。不同 stream endpoint 行为不一致。
- 目标结构：`sseClient.readJsonEvents(response)` async generator；`streamSseJson` 作为 adapter 调它。
- 迁移步骤：新增统一 parser 测试；改 `askStream` 使用 generator；改 `streamTaskEvents/translateDocumentStream` 使用同一 parser。
- 兼容/回滚：保留 `streamSseJson({response,onEvent})` API。
- unit 测试计划：single data、多 data、partial frame、invalid JSON、empty data。
- contract 测试计划：SSE content-type 与 JSON fallback。
- router 测试计划：无。
- stream 测试计划：ask/task/document 三类。
- integration 测试计划：mock ReadableStream。
- regression 测试计划：recoverableTaskController stream tests。
- 风险：中，parser 细节改变可能影响 task replay。
- 阻塞项：确认 gateway SSE 是否可能发送 multi-line data。

### R-021：把 KB/status/quota/auth API 边界统一到共享 http client

- 来源：第二轮深度补充
- 所属服务：frontend-vue
- 当前状态：active live path

- 严重程度：P2
- 类型：http/auth client consolidation
- 代码位置：
  - `frontend-vue/src/services/api.js:28-127`
  - `frontend-vue/src/services/auth.js:6-132`
  - `frontend-vue/src/services/admin.js:3-144`
  - `frontend-vue/src/api/http.js:1-104`
  - `frontend-vue/src/services/quota.js:1-113`
- 接口路径：`/api/auth/*`、`/api/admin/*`、`/api/quota/*`、`/api/kb_info`
- 当前调用链：router/auth/admin/quota/Home 分别使用各自 fetch wrapper。
- 关键片段：

```js
function readStoredToken() {
  return localStorage.getItem('token')
    || localStorage.getItem('agentcode.auth.token.v1')
    || '';
}

const TOKEN_KEYS = ['agentcode.auth.token.v1', 'token'];
```

- 当前问题：多个 wrapper 的 token key 优先级、错误处理、redirect/alert 行为不一致；download builder 又只读某一个 token key。
- 目标结构：`httpClient` 统一 base URL、token read、JSON/form/download、auth error normalization；各 API client 只描述 path/body。
- 迁移步骤：先抽共享 token/base/error helper；auth/admin/quota/API clients 逐个迁移；保留原 exported API 对象。
- 兼容/回滚：原 `authApi/adminApi/quotaApi/api` 方法名不变。
- unit 测试计划：token precedence、401 clear/redirect、disabled account formatting。
- contract 测试计划：auth/admin/quota URL/method。
- router 测试计划：router guard `authApi.getMe`。
- stream 测试计划：stream endpoints auth headers。
- integration 测试计划：login -> Home -> quota/admin auth flow。
- regression 测试计划：auth/register/username/admin tests。
- 风险：中，auth 行为变化影响全站。
- 阻塞项：确认 token key 迁移策略。

### J. 未能确认项

1. gateway canonical QA stream path：源码只能证明当前前端用 `/api/{mode}/ask_stream`，无法确认 gateway 是否要求切到 `/api/v1/{mode}/ask_stream`。
2. `VITE_REFRESH_SURVIVABLE_QA_TASKS_ENABLED` 生产值：源码默认 false，部署配置未审计。
3. `features/chat`、`features/controls`、`features/references` 是否计划恢复为产品入口：路由未注册，但不能据此直接删除。
4. `/api/refresh_kb`、`/api/clear_cache`、`/api/clear_pdf` 是否仍由 gateway 支持：只在未注册 controls feature 中发现调用。
5. 文件 download query token 是否为正式契约：源码存在，但未验证 gateway。
6. 后端 stream event schema 是否已有 code-first 版本：源码只显示前端能消费部分 metadata/code 字段。

### K. 覆盖核对补充

- `package.json` 已覆盖：`frontend-vue/package.json:6-10` 定义 `dev/build/test/preview`；`package.json:12-22` 仅声明 `vue/vue-router/pinia/marked/katex` 与 Vite 相关依赖，未见 `axios`、`EventSource` polyfill、OpenAI SDK 或后端 SDK 依赖。
- `vite.config.js` 已覆盖：`vite.config.js:4-21` 证明开发代理 `/api`、`/health` 到 `BACKEND_PROXY_TARGET || VITE_PROXY_TARGET || http://127.0.0.1:8101`，因此源码默认通过 gateway proxy，不直连 mode-specific 旧后端。
- `src/composables/` 顶层目录不存在：`find frontend-vue/src -maxdepth 2 -type d` 只发现 `src/features/*/composables`，未发现 `frontend-vue/src/composables`。已覆盖的 composables 位于 `features/auth`、`features/chat`、`features/controls`、`features/references`。
- tests / `*.test.*` 已覆盖：`find frontend-vue/src frontend-vue/tests -type f \( -name "*.test.*" -o -path "frontend-vue/tests/*" \)` 命中 50+ 个测试文件，重点覆盖 `services/api.*.test.js`、`stores/chatPersistence.test.js`、`stores/chatStore.*.test.js`、`utils/recoverableTaskController.test.js`、`utils/*stream*.test.js`、`views/Home.structure.test.js`、`components/PdfReader.structure.test.js`。缺口仍是 API URL contract、统一 SSE parser contract、真实路由级 send/upload/recover integration。
- API import 图复核：live 路径主要 import `../services/api`，证据 `Home.vue:4`、`chatStore.js:5`、`PdfReader.vue:3`；未注册 feature composables 主要 import `../../../api/*`，证据 `features/chat/composables/useChatSession.js`、`features/controls/composables/useKnowledgeWorkspace.js`、`features/references/composables/useReferencePanelState.js`。这进一步支持 R-019 的“双门面隔离/收敛”建议。

## 第三轮证据闭环补充

> 执行身份：Agent 6 frontend-vue 第三轮“重构实施前证据闭环审计”。本轮只读 `frontend-vue/` 与 gateway route/docs 证据，仅追加本文档；未修改 `frontend-vue/` 下任何源码、配置、测试、脚本、README、依赖文件。

### 1. 第二轮未确认项复核

### V-301

- 验证目标：`features/chat` 与 `features/controls` 是否已经恢复为产品入口。
- 只读命令：`rg "features/chat|features/controls|useChatSession|useKnowledgeWorkspace" frontend-vue/src frontend-vue/tests`；`rg "ChatPanel|SidebarPanel|ControlsPanel|useChatSession|useKnowledgeWorkspace|useConversationFileActions" frontend-vue/src frontend-vue/tests`；`nl -ba frontend-vue/src/router/index.js | sed -n '1,80p'`。
- 证据：`router/index.js:17-28` 只注册 `/`、auth、admin/profile/quota redirect，没有 `features/chat` 或 `features/controls` route；`rg` 对 `useChatSession/useKnowledgeWorkspace/useConversationFileActions` 只命中各自 export 和 `ChatPanel.structure.test.js`，未见 `Home.vue/App.vue/router` import；`useChatSession.js:1-8` 与 `useKnowledgeWorkspace.js:1-3` 仍引用 `src/api/*`。
- 结论：代码层面闭环为“未恢复为 live 产品入口”；产品计划层面仍不能从代码判断，删除前需要产品确认。
- 置信度：高。

### V-302

- 验证目标：`src/api/*` 与 `src/services/api.js` 双门面是否仍被 live components 使用。
- 只读命令：`rg "from .*src/api|../api|services/api|askStream|streamAsk|createTask|streamTaskEvents" frontend-vue/src frontend-vue/tests`；`rg -n "from ['\"].*api/(literature|quota|auth|conversation|chat)|from ['\"].*services/(api|quota|auth|admin|departments)|import \\{ api \\}" frontend-vue/src frontend-vue/tests`。
- 证据：live Home/store/PdfReader 分别 import `../services/api`，证据 `Home.vue:4`、`chatStore.js:5`、`PdfReader.vue:265`；`src/api/chat.js` 与 `src/api/conversation.js` 的 caller 只在未注册 `features/chat`、`features/controls`，证据 `useChatSession.js:1-8`、`useKnowledgeWorkspace.js:1-3`、`useConversationFileActions.js:1-3`；但 `src/api/literature.js` 被 live `PdfReader.vue:264` 使用，`src/api/quota.js` 被 live `services/quota.js:8` 间接使用。
- 结论：不能把 `src/api/*` 整体判 dead。`src/api/chat.js`、`src/api/conversation.js` 是 legacy/scaffold caller；`src/api/literature.js`、`src/api/quota.js` 仍处于 live 调用链；`src/api/http.js` 作为这些 `src/api/*` 的 transport helper 仍间接 live。
- 置信度：高。

### V-303

- 验证目标：前端默认 `/api/{mode}/ask_stream` 与 gateway `/api/v1/{mode}/ask_stream` canonical path 是否冲突。
- 只读命令：`rg "/api/v1|/api/|V1|ask_stream|tasks|conversations|upload_pdf|upload_excel" frontend-vue/src frontend-vue/tests`；`rg "ask_stream|/api/v1|tasks|APIRouter|include_router" gateway -S`；`nl -ba gateway/docs/gateway_canonical_protocol_revision.md | sed -n '1,100p'`；`nl -ba gateway/app/routers/qa.py | sed -n '850,940p'`。
- 证据：frontend `api.askStream` 构造 `/api/{mode}/ask_stream` 或 `/api/ask_stream`，证据 `services/api.js:626-630`；gateway canonical revision 明确 canonical QA routes 是 `POST /api/{mode}/ask` 与 `POST /api/{mode}/ask_stream`，`/api/v1/...` 为 temporary compatibility，证据 `gateway_canonical_protocol_revision.md:16-32`；gateway route 同时接受 `/api/{mode}/ask_stream` 与 `/api/v1/{mode}/ask_stream`，证据 `qa.py:852-866`；无 mode 的 `/api/v1/ask_stream` 测试期望 404，证据 `test_qa_proxy.py:2424-2434`。
- 结论：第二轮“canonical 冲突”需修正为：前端默认 `/api/{mode}/ask_stream` 符合 gateway canonical；风险在 `V1 = '/api'` 命名误导、无 mode `/api/v1/ask_stream` 已不是 gateway path、以及 task API 仍固定 `/api/v1/tasks`。
- 置信度：高。

### V-304

- 验证目标：`askPath` 的 `V1 = '/api'` 命名是否误导。
- 只读命令：`nl -ba frontend-vue/src/services/api.js | sed -n '1,220p'`；`nl -ba frontend-vue/src/services/api.js | sed -n '420,790p'`。
- 证据：`services/api.js:24-26` 定义 `const V1 = '/api'`；QA stream 用 `${V1}/${normalizedMode}/ask_stream`，证据 `services/api.js:626-630`；task create 用 `${V1}/v1/tasks`，证据 `services/api.js:703-710`。
- 结论：命名误导成立。`V1` 实际是 gateway API prefix，不是 `/api/v1` version prefix；拆 API client 前应先改名为 `API_PREFIX` 或集中 route builder。
- 置信度：高。

### V-305

- 验证目标：task API `/api/v1/tasks*` 是否受 feature flag 控制。
- 只读命令：`rg "VITE_REFRESH_SURVIVABLE_QA_TASKS_ENABLED|refreshSurvivableQATasksEnabled|createTask\\(|streamTaskEvents\\(|sendTaskMessage|sendLegacyMessage" frontend-vue/src frontend-vue/tests`；`nl -ba gateway/app/routers/tasks.py | sed -n '1,180p'`。
- 证据：frontend `readFeatureFlag('VITE_REFRESH_SURVIVABLE_QA_TASKS_ENABLED', false)` 默认 false，证据 `services/api.js:18-26`；store 暴露 computed `refreshSurvivableQATasksEnabled`，证据 `chatStore.js:67-69`；Home send 分支只在 flag true 时走 `sendTaskMessage`，否则走 legacy `askStream`，证据 `Home.vue:2072-2094`；gateway `POST /api/v1/tasks` 在 flag disabled 时返回 404，证据 `tasks.py:15-24`；`GET /api/v1/tasks/{id}`、events、cancel route 本身未在 router 层再次检查该 flag，证据 `tasks.py:27-60`。
- 结论：新建 task/send path 受前后端双重 flag 控制；task detail/events/cancel 是 existing task 操作，未在 router 层逐个 flag gate。重构时不能把整个 task client 简化为“全 family gated”。
- 置信度：高。

### V-306

- 验证目标：`chatStore.js` public API 清单是否足够支持 façade 重构。
- 只读命令：`nl -ba frontend-vue/src/stores/chatStore.js | sed -n '1485,1975p'`；`rg "useChatStore|chatStore\\.|store\\." frontend-vue/src frontend-vue/tests`。
- 证据：`chatStore.js:1903-1964` 已返回状态、computed 和 40+ actions；Home 仍直接写 `store.currentChatId = null`，证据 `Home.vue:1677-1680`，并直接改 `liveChat.messages/pdf_list/excel_list/uploaded_files`，证据 `Home.vue:327-344`。
- 结论：当前 public API 足以冻结兼容 façade，但不足以直接安全拆 store；拆分前需补 `enterDraftChatState`、`replaceConversationTruth`、`downloadFileUrl`、`currentTaskSnapshot` 等 selectors/actions，减少 Home 对内部 durable shape 的写入。
- 置信度：高。

### V-307

- 验证目标：streaming/busy runtime 与 durable state 的分离边界。
- 只读命令：`nl -ba frontend-vue/src/stores/chatStore.js | sed -n '1,230p'`；`nl -ba frontend-vue/src/views/Home.vue | sed -n '160,245p'`；`nl -ba frontend-vue/src/views/Home.vue | sed -n '1000,1265p'`。
- 证据：store runtime 只保存 busy phase/requestId/targetMessageIndex/stopRequested，证据 `chatStore.js:20-21`、`141-199`；Home runtime Map 保存 `AbortController`、pendingContent、flushFrame、pollTimer、eventState，证据 `Home.vue:169-217`；durable task cursor 在 chat 上保存 `activeTask/lastTaskSeq`，证据 `chatStore.js:1851-1900`；runtime busy 转 terminal 时触发 force persist，证据 `chatStore.js:85-94`。
- 结论：边界可执行：abort/pending/buffer/eventState 只能 runtime；message terminal fields、activeTask、lastTaskSeq 是 durable。风险是当前 runtime 分散在 store 与 Home 两套 map，迁移要保留 terminal edge persist。
- 置信度：高。

### V-308

- 验证目标：`chatPersistence.js` 黑名单投影是否会遗漏未来 runtime 字段。
- 只读命令：`nl -ba frontend-vue/src/stores/chatPersistence.js | sed -n '1,120p'`；`nl -ba frontend-vue/src/stores/streamPersistPolicy.js | sed -n '1,80p'`。
- 证据：当前只删除 message `streamRequestId` 和 chat `busyRuntime/chatBusyRuntime`，证据 `chatPersistence.js:1-45`；clone 是 generic object clone，未知字段默认保留，证据 `chatPersistence.js:4-18`；streaming debounce/terminal force policy 独立，证据 `streamPersistPolicy.js:1-15`。
- 结论：风险成立。未来若 `abortController/pendingContent/flushFrame/pollTimer/eventState/stopRequested/runtimePhase` 被挂到 chat/message，将默认进 localStorage。需要 white-list durable DTO projection。
- 置信度：高。

### V-309

- 验证目标：后端中文阶段文案是否已被前端依赖。
- 只读命令：`nl -ba frontend-vue/src/views/Home.vue | sed -n '735,825p'`；`nl -ba frontend-vue/src/utils/stageTimings.js | sed -n '1,100p'`；`nl -ba frontend-vue/src/views/Home.vue | sed -n '1000,1265p'`。
- 证据：`splitStepMessage` 用 `阶段[0-9一二三四...]` 正则拆标题和 detail，证据 `Home.vue:745-792`；`stageTimings.js:10-46` 内置 `阶段一/阶段二/阶段二点五/阶段四` 映射；普通 stream error 仍以 `data.message || data.error` 为主文案，证据 `Home.vue:1196-1237`。
- 结论：依赖成立。已有 quota/routing mapper 不能覆盖所有普通阶段/错误文案；重构前应建立 code-first presentation mapper，旧中文 message 作为兼容 fallback。
- 置信度：高。

### V-310

- 验证目标：build/test 未运行带来的结构性风险。
- 只读命令：`nl -ba frontend-vue/package.json | sed -n '1,80p'`；`find frontend-vue/src frontend-vue/tests -type f \\( -name "*.test.js" -o -name "*.structure.test.js" \\) | sort`。
- 证据：`package.json:6-10` 的 `build` 是 `vite build`，会写 `dist`；`test` 是 `node --test`，不是 Jest，用户给出的 `--runInBand --listTests` 不适用；测试清单存在 50+ `node:test` 文件，覆盖 API structure、store、recovery、stream utils、Home structure。
- 结论：本轮按只读约束未运行 build/test。结构性风险是未通过 bundler 校验 import/语法，也未执行 existing node tests；进入重构实施前至少要在允许写入产物/临时文件的环境跑 `npm run build` 和 `npm test`。
- 置信度：高。

### 2. dead-code / legacy 引用闭环

#### frontend API façade 判定表

| API façade | import/caller 证据 | live/legacy 判定 | 处理建议 |
| --- | --- | --- | --- |
| `src/services/api.js` | `Home.vue:4`、`chatStore.js:5`、`PdfReader.vue:265`；`Home.vue:2016-2024` 调 `api.askStream`；`recoverableTaskController.js:589-596` 调 `api.createTask` | live canonical façade | 拆 clients 时必须保留 `api` compatibility façade |
| `src/api/chat.js` | `useChatSession.js:8` 调 `streamAsk`；`useKnowledgeWorkspace.js:2` 调 KB/upload/clear；无 router/App/Home caller | legacy/scaffold | 若 features 恢复，改为 re-export canonical clients；否则隔离或删除前先产品确认 |
| `src/api/conversation.js` | `useChatSession.js:1-8` 调 conversation CRUD；`useConversationFileActions.js:1-3` 调 download builder；无 live route caller | legacy/scaffold，但含 token-key 冲突证据 | download builder 迁到 canonical file/document API；旧模块 re-export |
| `src/api/literature.js` | `PdfReader.vue:264` import，`PdfReader.vue:437`、`502` 调 PDF load；`features/references/*` 也 import | live | 不应随 chat/controls legacy 一起删除；后续并入 document/literature client |
| `src/api/quota.js` | `services/quota.js:8` import，`QuotaManagementPanel.vue:3`、`UserProfile.vue:4` 通过 `services/quota` 使用 | live indirect | 收敛到 shared http client，但保留 `quotaApi` |
| `src/api/auth.js` | `features/auth/composables/useAuthSession.js:2`、`auth.register.test.js:7`；live router/views 使用 `services/auth` | secondary/legacy tested | 与 `services/auth` 合并前保留测试或迁移测试目标 |
| `src/api/http.js` | `src/api/chat.js:1`、`conversation.js:1`、`literature.js`、`quota.js` import | mixed helper | 因 `literature/quota` live，不能直接删除 |

#### dead-code / legacy 引用结论

- `features/chat`、`features/controls`：未注册为产品入口，但内部仍是完整 session/workspace 模型，不能简单按 dead code 删除；当前判断为 `legacy scaffold, product decision required`。
- `features/references`：未见 route 注册，但其 `src/api/literature` 依赖与 `PdfReader` live 能力重叠；应作为“待确认入口/共享能力”处理，不跟 chat/controls 一刀切。
- `src/api/chat.js` 与 `src/api/conversation.js`：有 import/caller 证据但 caller 不在 live route；重构时优先改为 compatibility re-export，避免未来恢复入口时绕过 canonical client。
- `frontend-vue/dist/` 与 `node_modules/`：只读 `find frontend-vue -type f` 命中大量文件，但不作为重构源码证据。

### 3. live path 调用链闭环

#### path canonicalization 决策表

| 路径族 | frontend 当前调用 | gateway 证据 | 决策 | 重构影响 |
| --- | --- | --- | --- | --- |
| QA stream mode path | `services/api.js:626-630` -> `/api/{fast,thinking,patent}/ask_stream` | `gateway_canonical_protocol_revision.md:16-32` 定义 canonical；`qa.py:852-866` 同时接受 `/api` 与 `/api/v1` mode path | `/api/{mode}/ask_stream` 是 canonical；`/api/v1/{mode}/ask_stream` 是 compatibility | route builder 默认保持 `/api/{mode}/ask_stream` |
| QA stream no-mode alias | `services/api.js:628` fallback `/api/ask_stream`；`src/api/chat.js:80` fallback `/api/ask_stream` | `gateway_forwarding_protocol.md:242-249` 将 `/api/ask_stream` 视为 compatibility alias；`test_qa_proxy.py:2424-2434` 证明 `/api/v1/ask_stream` removed | 保留 `/api/ask_stream` fallback，不迁到 `/api/v1/ask_stream` | unknown mode path test 必须锁定 404/alias 预期 |
| Task API | `services/api.js:703-770` -> `/api/v1/tasks*` | `tasks.py:15-60` 只暴露 `/api/v1/tasks*`，create 受 gateway flag gate | task API canonical 目前就是 `/api/v1/tasks*` | 不要把 task 路径自动迁到 `/api/tasks`，除非 gateway 新增 route |
| Public conversation/upload/KB | `services/api.js:430-604` -> `/api/conversations`、`/api/upload_*`、`/api/kb_info`；legacy docs 也提 `/api/v1/...` compatibility | `gateway_canonical_protocol_revision.md:18-32` canonical public routes `/api/...` | frontend live 已偏向 canonical `/api/...` | client split 保持 `/api/...` |
| File download | Home 手写 `/api/conversations/{id}/files/{file_id}/download?token=`，证据 `Home.vue:2312-2322` | gateway docs 允许 query-token compatibility，证据 `gateway_canonical_protocol_revision.md:30-32` | path canonical 是 `/api/...`，query token 是 compatibility | 迁到 builder，并统一 token key |

#### live 调用链闭环

```text
router/index.js:17
  -> Home.vue:3-4 useChatStore + services/api
  -> sendMessage Home.vue:2072-2094
  -> legacy branch: sendLegacyMessage -> api.askStream Home.vue:2016-2024
  -> services/api.js:607-681 -> POST /api/{mode}/ask_stream
  -> gateway qa.py:852-866 -> _proxy_ask_stream -> upstream /api/{actual_mode}/ask_stream

refresh-survivable branch:
Home.vue:2083-2090
  -> recoverableTaskController.sendTaskMessage
  -> api.createTask recoverableTaskController.js:589-596
  -> services/api.js:703-710 -> POST /api/v1/tasks
  -> api.streamTaskEvents recoverableTaskController.js:346-385
  -> services/api.js:720-752 -> GET /api/v1/tasks/{id}/events
  -> Home.applyGatewayEvent Home.vue:1004-1255
```

#### chatStore public API 清单

| 分类 | public API |
| --- | --- |
| state/computed | `chats`, `currentChatId`, `currentChat`, `currentMessages`, `refreshSurvivableQATasksEnabled`, `activeBusyCount`, `hasBusyCapacity`, `isStreaming`, `kbInfo`, `userId`, `syncStatus`, `sessionState` |
| user/session | `setUserId`, `getUserId`, `loadChats`, `createChat`, `switchChat`, `deleteChat`, `clearAllChats`, `togglePinned`, `persistLocalState` |
| title/conversation | `buildAutoTitleFromText`, `buildAutoTitleFromFileName`, `updateCurrentChatTitle`, `ensureChatConversation` |
| busy/runtime façade | `getChatBusyRuntime`, `getChatActiveTask`, `getChatLastTaskSeq`, `isChatBusy`, `isChatStreaming`, `isChatStopRequested`, `startChatBusyRuntime`, `markChatBusyStreaming`, `finishChatBusyRuntime`, `requestChatBusyStop`, `stopChatBusyRuntime`, `clearChatBusyRuntime`, `setStreaming` |
| task recovery durable | `scheduleTaskRecoveryPersist`, `flushTaskRecoveryPersist`, `setChatActiveTask`, `updateChatTaskReplayCursor`, `clearChatActiveTask` |
| messages | `addUserMessage`, `addBotMessage`, `updateLastBotMessage`, `addSystemMessage` |
| KB | `setKbInfo`, `loadKbInfo` |
| files/session context | `addUploadedPdf`, `addUploadedPdfToChat`, `addUploadedExcelToChat`, `refreshCurrentChatFiles`, `getAllPdfIds`, `getAllUploadedFileIds`, `getNewlyUploadedPdfIds`, `getNewlyUploadedFileIds`, `removePdf`, `uploadExcel`, `removeExcel` |

结论：清单足够作为 migration compatibility surface；但 `Home.vue` 仍直接写内部字段，因此 façade 重构第一步不是拆 store，而是补缺失 command/selectors 并让 Home 停止直写。

### 4. 测试护栏闭环

#### 测试护栏清单

| 护栏 | 已有证据 | 覆盖能力 | 缺口/第三轮要求 |
| --- | --- | --- | --- |
| API structure | `services/api.structure.test.js:16-19` 锁定 legacy `ask_stream` path；`109-119` 锁定 task endpoints | 防止路径粗暴改错 | 需要 route builder URL contract：`/api/{mode}/ask_stream`、`/api/ask_stream`、`/api/v1/tasks*` |
| task recovery | `recoverableTaskController.test.js` 多处 mock `createTask/streamTaskEvents`；`chatStore.task-recovery.test.js` 覆盖 activeTask/lastSeq restore | 刷新恢复与 replay cursor | 需要 gateway flag disabled contract：frontend fallback + backend 404 处理 |
| store runtime | `chatStore.concurrent-streaming.test.js` 覆盖 capacity/busy/stop；`chatStore.persistenceTiming.test.js` 覆盖 terminal persist | 并发和 busy runtime | 抽 runtime store 前必须保持这些测试或迁移成 façade 测试 |
| persistence | `chatPersistence.test.js` 覆盖 `streamRequestId` 排除、failed/canceled terminal | runtime blacklist 与 terminal durable | 需要 white-list projection 测试，覆盖未来 runtime 字段误挂载 |
| SSE/parser | `utils/sse.js` 有调用方测试间接覆盖；`services/api.js` inline parser 未被统一 contract 覆盖 | stream basic | 需要统一 parser 测试：multi-line `data:`、invalid JSON、partial frame |
| Home structure | `Home.structure.test.js` 覆盖 task branch、quota card、askStream snapshot | 组件关键调用链 | 拆 Home 前需要补 selectors/actions contract，降低 fragile regex |
| Pdf/literature | `api/literature.test.js`、`PdfReader.structure.test.js` | `src/api/literature` live 能力 | API façade 收敛时不可误删 literature helper |
| build/test execution | 未运行 | 无本轮 runtime 保障 | `npm run build` 会写 `dist`，本轮禁止；`npm test` 为 `node --test`，非 `--listTests` 模式，本轮未执行 |

本轮未运行 `npm run build`，原因：Vite build 会写 `frontend-vue/dist/`，违反只读/禁止会写文件命令约束。未运行 `npm test -- --runInBand --listTests`，原因：项目 `package.json:9` 使用 `node --test`，不是 Jest；该参数不能提供可靠只读 list-tests 行为，且执行测试会运行代码而非单纯列举。

### 5. 可实施重构任务拆分

### TASK-301

- 目标：建立 frontend route builder 并修正 `V1 = '/api'` 命名误导。
- 前置条件：以 `gateway_canonical_protocol_revision.md:16-32` 为准，默认 QA stream 保持 `/api/{mode}/ask_stream`；task API 保持 `/api/v1/tasks*`。
- 实施范围：新增/迁移到 route builder；替换 `services/api.js` 内散落的 `V1` 字符串；不改变 public `api.*` 签名。
- 验收标准：`fast/thinking/patent` stream URL 为 `/api/{mode}/ask_stream`；unknown fallback 为 `/api/ask_stream`；task URL 仍为 `/api/v1/tasks*`。
- 测试护栏：新增 route builder unit/contract test；保留 `api.structure.test.js` 对 legacy path 的断言。
- 回滚策略：route builder 单点回滚到当前字符串模板。
- 阻塞/依赖：无产品阻塞；需 gateway owner 认可无 mode `/api/v1/ask_stream` 不再作为前端目标。

### TASK-302

- 目标：拆 `src/services/api.js` 为 canonical clients，并保留 `api` compatibility façade。
- 前置条件：冻结 C.1 API 函数清单和本轮 façade 判定表；先处理 `src/api/literature/quota` live 依赖，避免误删。
- 实施范围：`httpClient/sseClient/conversationApi/qaStreamApi/taskApi/uploadApi/documentApi/kbApi`；`api.js` 仅 re-export/compose。
- 验收标准：`Home.vue`、`chatStore.js`、`PdfReader.vue` 无行为变化；旧 `api.askStream/createTask/uploadPdf/...` 签名保持。
- 测试护栏：URL contract、SSE parser、PdfReader structure、quota/auth token precedence。
- 回滚策略：保留原 `api.js` façade 文件，clients 可逐个撤回。
- 阻塞/依赖：需要先完成 TASK-301，避免拆分时复制错误路径。

### TASK-303

- 目标：为 `chatStore` 拆分建立 façade 安全层，减少 Home 直接写内部 durable shape。
- 前置条件：以本轮 `chatStore public API 清单` 作为兼容面；补缺 selector/action。
- 实施范围：新增 `enterDraftChatState`、`replaceConversationTruth`、`getCurrentTaskSnapshot`、`buildConversationFileDownloadUrl` 或等价 action；Home 先迁移到 action。
- 验收标准：Home 不再直接 `store.currentChatId = null`，不直接替换 `liveChat.messages/pdf_list/excel_list/uploaded_files`；现有 store API 仍可用。
- 测试护栏：Home structure 改断言 selector/action；store task-recovery/persistence/concurrent tests 全部保留。
- 回滚策略：action 内部仍调用现有 store 逻辑，失败可回退 Home 旧路径。
- 阻塞/依赖：无产品阻塞；需要谨慎处理 temp chat promotion。

### TASK-304

- 目标：分离 streaming/busy runtime 与 durable state。
- 前置条件：先抽 `applyGatewayEvent` reducer 或至少冻结 event contract；保留 terminal edge force persist。
- 实施范围：runtime-only：abortController、pendingContent、flushFrame、pollTimer、eventState、stopRequested；durable：messages terminal fields、activeTask、lastTaskSeq。
- 验收标准：localStorage 不含 runtime-only 字段；cancel/error/done 后 terminal message 和 cursor 可刷新恢复。
- 测试护栏：`chatStore.concurrent-streaming.test.js`、`chatStore.persistenceTiming.test.js`、`recoverableTaskController.test.js`、新增 runtime-not-persisted tests。
- 回滚策略：保留 `store.startChatBusyRuntime/isChatBusy/...` façade。
- 阻塞/依赖：建议先完成 TASK-303，降低 Home 耦合。

### TASK-305

- 目标：把 `chatPersistence.js` 从 blacklist sanitizer 改为 durable DTO projection。
- 前置条件：定义 durable chat/message schema，明确 terminal metadata、task cursor、file lists 应保留。
- 实施范围：`prepareChatsForPersistence`、`restorePersistedChats`；兼容旧 localStorage shape。
- 验收标准：未知 runtime 字段默认丢弃；failed/canceled/done、timings、references、activeTask/lastTaskSeq 保留。
- 测试护栏：扩展 `chatPersistence.test.js` 覆盖 `abortController/pendingContent/flushFrame/pollTimer/eventState`。
- 回滚策略：保留当前 blacklist 作为临时二级防线。
- 阻塞/依赖：需要 schema review，防止白名单漏用户数据。

### TASK-306

- 目标：对 `features/chat`、`features/controls`、`src/api/chat.js`、`src/api/conversation.js` 做产品入口决策与 API 收敛。
- 前置条件：产品确认是否恢复入口；若恢复，确认 route/导航设计；若不恢复，确认删除/归档策略。
- 实施范围：未注册 feature composables 改用 canonical clients 或隔离；旧 `src/api/chat/conversation` re-export canonical API。
- 验收标准：无 live component 继续绕过 canonical `services/api` clients；import graph 可解释。
- 测试护栏：`ChatPanel.structure.test.js`、feature composable mock tests 或 dead-code import test。
- 回滚策略：旧模块保留一轮 release 为 compatibility adapter。
- 阻塞/依赖：产品入口决策未闭环。

### TASK-307

- 目标：建立 stream event code 到前端 presentation mapper，减少后端中文文案耦合。
- 前置条件：gateway/backend 同意 event code/stage_code/error code 稳定字段；旧中文 message 保持 fallback。
- 实施范围：`splitStepMessage`、`stageTimings`、普通 stream error presentation。
- 验收标准：英文/结构化 backend message 不破坏中文 UI；中文旧 message 仍可展示。
- 测试护栏：`stageTimings.test.js`、`routingStatus.test.js`、新增 event presentation tests。
- 回滚策略：mapper fallback 到当前 message parsing。
- 阻塞/依赖：后端 event schema code-first 版本未确认。

### 6. 不可立即处理项与阻塞原因

- `features/chat` / `features/controls` 删除或注册：代码证明未 live，但产品计划未确认；只能先收敛 API 或隔离，不能直接删。
- `/api/v1/tasks*` 路径迁移：gateway 目前 task router 只暴露 `/api/v1/tasks*`，不能按 `/api/...` canonical public route 规则自行改成 `/api/tasks`。
- `getTask/events/cancel` flag gate：gateway 仅 `POST /api/v1/tasks` 显式 gate；是否要 gate whole family 属后端 contract 决策。
- persistence white-list：需要 durable schema review，否则白名单漏字段会丢聊天、文件、terminal metadata。
- 后端中文文案替换：前端已有中文 regex 和 label 依赖，需后端先提供稳定 code/stage_code。
- build/test：本轮禁止运行会写文件命令；未执行 build/test 是审计限制，不代表通过。

### 7. 最终进入重构前检查清单

- [ ] 已确认 `TASK-301` route builder 目标：QA stream default `/api/{mode}/ask_stream`，task API 保持 `/api/v1/tasks*`。
- [ ] 已冻结 `api` compatibility façade 方法和返回 shape，拆分期间调用方不直接改签名。
- [ ] 已确认 `src/api/literature.js` 与 `src/api/quota.js` live，不随 chat/controls legacy 清理。
- [ ] 已补齐 `chatStore` façade selectors/actions，让 Home 不再直接写 `currentChatId` 和 conversation internals。
- [ ] 已定义 durable chat/message projection schema，并列出保留字段与 runtime-only 丢弃字段。
- [ ] 已把 `features/chat`、`features/controls` 的产品入口决策写入任务或 issue。
- [ ] 已建立 URL contract tests、SSE parser tests、persistence projection tests、task flag tests。
- [ ] 已在允许写入产物的环境运行 `cd frontend-vue && npm run build`。
- [ ] 已在允许执行测试的环境运行 `cd frontend-vue && npm test`。
- [ ] 已记录 gateway/backend event code 与中文 fallback 的迁移约定。
