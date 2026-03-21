# 公共能力已确认问题清单

目的：
- 这份文档只记录当前代码里已经确认存在的问题、缺陷、契约偏差和迁移风险。
- 这些问题不是抽象讨论，而是后续把公共能力抽成“单独公共后端”时应顺手修复的实际项。
- 只写已经有源码依据的内容，不写没有证据的猜测。

问题分级约定：
- `P1`
  安全问题、明显错误行为、会直接影响抽公共后端边界。
- `P2`
  明确契约偏差、状态副作用不一致、实现缺陷。
- `P3`
  兼容层分裂、迁移未收口、命名或语义误导。

## 1. auth

### 1.1 `set_security_questions()` 在缺表场景下可能返回成功但没有实际写入

- 类型：`P1 / 明确实现缺陷`
- 代码事实：
  - `AuthService.set_security_questions()` 无论 repository 是否真的落库，只要不抛异常就返回“安全问题设置成功”。
  - `AuthRepository.replace_security_questions()` 在 `user_security_questions` 表不存在时会直接返回。
- 影响：
  - 用户侧会看到设置成功。
  - 但后续 `/me` 仍可能显示没有安全问题。
  - 忘记密码流程也不会真正可用。
- 后续抽公共后端时建议：
  - 把“安全问题表不存在”从静默降级改成显式失败。

### 1.2 前端存在两套登录态存储 key，容易造成会话状态分裂

- 类型：`P2 / 明确兼容缺陷`
- 代码事实：
  - 新 composable 体系使用 `agentcode.auth.token.v1 / agentcode.auth.user.v1`
  - 页面和路由守卫仍使用 `token / user`
- 影响：
  - 同一浏览器里可能出现“某些页面已登录、某些页面仍判未登录”。
  - 抽公共后端时如果不统一 session key，会把兼容问题带过去。
- 后续抽公共后端时建议：
  - 收口成单一 token/user 存储规范。

## 2. admin_users

### 2.1 批量导入绕过单用户创建主流程，首登/安全问题/密码历史副作用未统一

- 类型：`P1 / 明确实现缺陷`
- 代码事实：
  - `import_service.py` 逐行直接调用 `AuthRepository.create_user()`
  - 没有显式补：
    - `is_first_login=True`
    - `must_set_security_questions=True`
    - `add_password_history()`
    - `trim_password_history()`
- 影响：
  - 单用户创建和批量导入在同一公共能力里产生不同副作用。
  - 抽公共后端时会让“管理员发放初始口令”的行为不稳定，依赖 DB 默认值。
- 备注：
  - 这里不等于说“初始口令必须遵守用户后续改密规则”；问题点在于副作用不统一，不在密码强度规则本身。

### 2.2 管理员重置密码不会清理登录失败计数和锁定状态

- 类型：`P2 / 明确状态机缺陷`
- 代码事实：
  - `admin_users.reset_password()` 会更新密码、写历史、重新要求首次登录/安全问题。
  - 但不会调用 `reset_login_attempts()`。
- 影响：
  - 用户即使被管理员重置密码，仍可能因为旧的失败次数或锁定窗口而无法登录。
  - 这和用户自行改密、忘记密码重置后的行为不一致。

### 2.3 导入结果弹窗和后端返回字段不一致

- 类型：`P1 / 明确前后端契约偏差`
- 代码事实：
  - 后端导入明细返回 `reason`
  - 前端弹窗读取 `message`
  - 前端还展示 `user_id`，但后端成功项并不返回该字段
- 影响：
  - 管理员导入结果页“消息”列和失败记录下载内容会是空的。
  - 这是抽公共后端前必须先修掉的现网契约问题。

## 3. quota

### 3.1 `uploads` 没有走 quota 标准 finalize 语义，导致 quota 行为不一致

- 类型：`P2 / 明确接入不一致`
- 代码事实：
  - quota 标准接法是 `require_quota() + finalize_quota()`
  - `uploads` 自己直接 `check_quota() + increment_quota()`
- 影响：
  - 同样属于公共能力，上传与 documents/conversation/download 的 quota 计数语义不同。
  - 后续抽公共后端时，quota 模块本身虽然可抽，但上传链必须做接入收口。

### 3.2 quota 管理能力与部分前端管理面表达不完全一致

- 类型：`P3 / 能力表达不完整`
- 代码事实：
  - 后端支持 `daily/weekly/monthly/custom_days` 等多窗口组合。
  - 部分前端控制面仍偏向单 `default_limit` 视图。
- 影响：
  - 抽公共后端后，如果保留当前前端，管理端仍可能只覆盖一部分能力。

## 4. conversation

### 4.1 删除会话不会显式清理远端 JSON、副表历史和上传文件资产

- 类型：`P2 / 明确清理缺口`
- 代码事实：
  - `delete_conversation()` 只删 `conversations` 行、删本地 JSON、刷缓存。
  - 没有显式清理：
    - 远端 chat JSON 对象
    - `conversation_messages / conversation_files` 历史行
    - 会话下上传文件的本地/对象存储资产
- 影响：
  - 当前删除会话更像“删除主索引入口”，不是完整资产回收。
  - 抽公共后端时如果直接继承这条语义，会把存储残留一起带过去。

### 4.2 当前是补偿一致，不是事务一致，跨存储失败后状态可能短暂分裂

- 类型：`P3 / 已知架构风险`
- 代码事实：
  - DB、JSON、本地文件、对象存储、Redis cache 之间主要依赖锁和补偿重试。
- 影响：
  - 这不一定是 bug，但在抽公共后端时必须保留“最终一致 + 补偿”的设计前提，不能误判成单库事务模块。

## 5. uploads

### 5.1 缺少 `auth + conversation_id` 时，文件已保存但接口仍返回错误

- 类型：`P1 / 明确错误行为`
- 代码事实：
  - 上传先做 quota，再本地落盘，再对象镜像。
  - `_persist_uploaded_file()` 如果没有 auth 或 conversation_id，会返回“缺少会话上下文，无法关联上传文件”。
- 影响：
  - 调用方看到失败，但文件可能已真实保存到本地/对象存储。
  - 如果登录用户已通过 quota 预扣，还会发生“失败响应但 quota 已扣”的行为。

### 5.2 上传接口大量业务错误仍返回 HTTP 200

- 类型：`P2 / 明确契约缺陷`
- 代码事实：
  - 不支持文件类型、缺文件、无会话上下文、元数据记录失败等场景，普遍返回 `200 + error payload`
- 影响：
  - 前后端必须自行解析 payload，不能依赖状态码。
  - 抽公共后端时如果要提供更稳定的上传接口，最好先统一错误语义。

### 5.3 上传 quota 的豁免规则与 quota 标准豁免规则不完全一致

- 类型：`P2 / 明确语义不一致`
- 代码事实：
  - quota 标准豁免是 `user_type in {1,2}`
  - uploads 这里只跳过 `user_type == 2`
- 影响：
  - 管理员和超级用户在不同模块里的配额待遇不一致。

## 6. documents

### 6.1 `view_pdf` 表面上 optional auth，实际上仍被 quota 依赖强制要求登录

- 类型：`P2 / 明确接口语义误导`
- 代码事实：
  - `view_pdf()` 同时依赖 `get_optional_auth_context` 和 `require_quota("file_view")`
  - `require_quota()` 内部又依赖 `require_auth_context`
- 影响：
  - 接口定义看起来像“匿名可访问但登录可增强”。
  - 实际行为却是需要登录。
  - 抽公共后端时如果不先统一契约，前端会继续误判访问方式。

### 6.2 `reference_preview` POST body 与前端字段名不一致

- 类型：`P1 / 明确前后端契约偏差`
- 代码事实：
  - 后端 schema 接收 `dois_text / doi_list / max_items`
  - `frontend-vue/src/api/literature.js` 当前 POST 的是 `{ doi: values, max_items }`
- 影响：
  - 前端调用这条 POST 接口时，后端拿不到 `doi_list`。
  - 这会直接影响引用预览功能可用性。

### 6.3 `literature_content` / `kb_info` 一类工具接口大量业务失败仍返回 200

- 类型：`P2 / 契约风格不统一`
- 影响：
  - 这不是单点 bug，但对抽公共后端不利，因为文档/前端必须额外保留 payload 判错逻辑。

## 7. storage

### 7.1 论文 PDF 读取仍存在新旧双入口，配置来源和行为没有完全收口

- 类型：`P2 / 明确迁移未收口`
- 代码事实：
  - 新链路走 `storage_service`
  - 老链路仍走 `modules/storage/paper_storage.py`
  - 还保留 `services/storage/paper_storage.py` shim
- 影响：
  - 抽公共后端时如果只迁新入口，老 agent/generation 链仍会保留旧行为。
  - 同一论文 PDF 能力继续双轨运行。

### 7.2 local backend 只是引用包装，不是真正对象存储

- 类型：`P3 / 易误解设计事实`
- 影响：
  - 这不是 bug，但如果抽公共后端时把 local backend误当成“本地对象仓库”，会造成实现假设错误。

## 8. system

### 8.1 `kb_info / refresh_kb / clear_cache` 当前默认未鉴权

- 类型：`P1 / 明确安全问题`
- 代码事实：
  - 这些接口只依赖 `get_runtime`
  - 没有 `require_auth_context` 或 `require_admin_context`
- 影响：
  - 未登录请求也可触发知识库刷新和进程内缓存清空。
  - 这是抽公共后端前必须优先修复的问题。

### 8.2 `schemas.py` 与真实返回体明显脱节

- 类型：`P2 / 明确契约文档缺陷`
- 代码事实：
  - API 未声明 `response_model`
  - schema 明显少于真实返回字段
- 影响：
  - 如果后面用 schema 生成接口说明或做独立服务契约，会直接出错。

### 8.3 `system` 混合了平台观测接口和 QA 运维接口

- 类型：`P3 / 边界混合风险`
- 影响：
  - 抽公共后端时可以先整体带走，但后续内部仍建议继续拆层。

## 9. 处理优先级建议

### 9.1 抽公共后端前应优先修

- system 未鉴权运维动作
- admin_users 导入链副作用缺失
- admin_users 导入结果前后端字段不一致
- uploads “失败响应但文件已保存 / quota 已扣”
- documents `reference_preview` 字段不一致
- auth 安全问题静默成功

### 9.2 抽公共后端时同步收口

- quota 接入语义统一
- conversation 删除清理边界
- auth 前端单一 session key
- storage 论文 helper 双入口收口
- system schema 与真实契约统一
