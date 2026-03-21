# uploads 模块代码细读

模块路径：
- `backend/app/modules/uploads/api.py`

关联代码：
- `backend/app/core/runtime.py`
- `backend/app/modules/storage/service.py`
- `backend/app/modules/quota/service.py`
- `backend/app/modules/conversation/service.py`
- `backend/app/modules/ask_gateway/service.py`
- `backend/app/modules/file_context/service.py`
- `frontend-vue/src/api/chat.js`
- `frontend-vue/src/services/api.js`
- `frontend-vue/src/views/Home.vue`
- `frontend-vue/src/features/controls/composables/useKnowledgeWorkspace.js`
- `frontend-vue/src/stores/chatStore.js`

模块定位：
- 明确属于公共能力
- 是平台统一文件上传入口
- 但实现上同时挂着旧的 runtime PDF 路径和新的会话文件体系

## 1. 结论先说

`uploads` 不是独立文件服务，而是一个“HTTP 上传入口适配层”。

它自己做的事主要是：
- 收 multipart 文件
- 保存到本地 `uploads/`
- 尝试镜像对象存储
- 在有登录态和会话上下文时挂接到 `conversation`
- 把文件交给 `upload_processing_worker`
- 对 PDF 额外维护 `runtime.current_pdf_path`

所以它处在几个系统的交界面：
- 上传入口
- quota
- storage
- conversation
- ask_gateway 旧 PDF 路径

## 2. 深拆文档索引

本次已把 `uploads` 再细分为子文档，放在：
- `/home/cqy/worktrees/public-service/public-modules/uploads/README.md`
- `/home/cqy/worktrees/public-service/public-modules/uploads/01-api-and-contracts.md`
- `/home/cqy/worktrees/public-service/public-modules/uploads/02-save-path-runtime-and-storage.md`
- `/home/cqy/worktrees/public-service/public-modules/uploads/03-auth-and-quota.md`
- `/home/cqy/worktrees/public-service/public-modules/uploads/04-conversation-binding-and-processing.md`
- `/home/cqy/worktrees/public-service/public-modules/uploads/05-frontend-and-compat-notes.md`

这份 `05-uploads.md` 保留为总览。

## 3. 为什么这个模块需要单独细拆

虽然代码文件只有一个 `api.py`，但它的真实复杂度不低，因为它把这些逻辑全揉在了一起：
- 文件格式校验
- 本地落盘
- 对象存储镜像
- 可选登录态
- 配额预扣
- 会话文件登记
- 异步处理任务提交
- runtime 当前 PDF 指针
- 旧前端兼容

也正因为没有独立 service 层，很多策略只能从控制器代码里读出来。

## 4. 当前对外接口

- `POST /api/v1/upload_pdf`
- `POST /api/v1/upload_excel`
- `POST /api/v1/clear_pdf`

同时也保留：
- `/upload_pdf`
- `/upload_excel`
- `/clear_pdf`

这些接口目前都在 `api.py` 里直接实现，没有 `uploads/service.py`。

## 5. 核心代码事实

### 5.1 上传不是“一步到位成功”

一次上传实际可能拆成几个阶段：

1. quota 预检查并立即计数
2. 解析 multipart form
3. 保存本地文件
4. 镜像对象存储
5. 挂接 conversation 文件记录
6. 提交异步解析任务

这几个阶段没有统一事务。

### 5.2 匿名请求可以上传，但不能形成完整业务闭环

接口用的是 `get_optional_auth_context`，所以：
- 没 token 时，HTTP 层允许进入
- 但 `_persist_uploaded_file()` 要求：
  - `auth` 存在
  - `conversation_id` 存在

否则即使文件已经保存成功，接口最后也会返回：
- `缺少会话上下文，无法关联上传文件`

因此它不是一个真正意义上“匿名可用”的公共上传服务。

### 5.3 `clear_pdf` 仍然有运行时意义

`clear_pdf()` 不会删除文件，只会把：
- `runtime.current_pdf_path = None`

这条状态仍会被：
- `ask_gateway_service._default_enrich_request()`
- `file_context_service.resolve_request_file_context()`

当成“无 conversation 时的当前 PDF fallback”来使用。

所以它不是文件删除接口，而是旧 runtime PDF 上下文清理接口。

## 6. 模块内部的几条依赖链

### 6.1 runtime 链

来自 `AppRuntime`：
- `upload_folder`
- `pdf_web_bindings`
- `upload_processing_worker`
- `current_pdf_path`

### 6.2 存储链

通过 `storage_service.mirror_file()`：
- 本地文件 -> 对象存储 mirror

### 6.3 quota 链

通过 `_optional_quota_response()`：
- `auth_service.get_user_by_id()`
- `quota_service.check_quota()`
- `quota_service.increment_quota()`

### 6.4 会话链

通过 `_persist_uploaded_file()`：
- `conversation_service.add_uploaded_file()`
- `runtime.upload_processing_worker.submit()`

### 6.5 旧 PDF 运行态链

通过 `_set_current_pdf_path()`：
- `runtime.current_pdf_path`
- `ask_gateway`
- `file_context`

## 7. 当前最重要的实现判断

- 上传入口是公共能力，但后续解析状态已经绑定到 `conversation`
- `file_upload` 配额是在上传前立即扣减，而不是按最终结果结算
- `runtime.current_pdf_path` 代表还有一条旧式“单进程当前 PDF”路径仍未完全退场
- 前端已经默认要求上传后返回 `file_id`，否则会把“文件已上传但未挂接会话”当失败

## 8. 后续如果继续拆服务，uploads 的真正边界

如果以后把公共能力拆出去，`uploads` 至少要拆开看成三块：
- 通用文件接收与存储
- 会话文件登记
- 旧 PDF runtime 兼容路径

现在这三块都压在一个模块里。

## 9. 当前已确认问题与迁移修复点

- `P1` 上传流程里 quota 预检查和计数发生在前面，本地落盘和对象镜像也发生在 `_persist_uploaded_file()` 之前；如果最终因为 `auth` 或 `conversation_id` 缺失而返回“缺少会话上下文”，调用方看到的是失败，但文件可能已经真实保存，登录用户的 quota 也可能已经被扣减。
- `P2` 这个模块大量业务错误仍返回 `HTTP 200`，例如：
  - 文件类型不支持
  - 缺少上传文件
  - 缺少会话上下文
  - 会话文件元数据记录失败
- `P2` `_optional_quota_response()` 里的豁免规则与 quota 标准依赖不一致，只跳过 `user_type == 2`，没有覆盖 `user_type == 1`。
- 所以 uploads 不只是“实现比较旧”，而是已经存在明确错误行为和错误契约。后续抽独立公共后端时，上传链必须优先改成“先校验上下文，再落盘/扣额，最后按统一 HTTP 语义返回”。
