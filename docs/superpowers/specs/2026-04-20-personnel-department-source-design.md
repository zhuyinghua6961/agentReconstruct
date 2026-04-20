# Personnel Department Source Design

**Date:** 2026-04-20

## Summary

本设计把“部门信息”的业务真源从 `users` 账号资料切换到 `personnel_records` 人员主档。

核心原则如下：

1. 一个 `personnel` 只对应一套唯一的三级部门信息。
2. 同一个 `personnel` 绑定的所有账号，都必须共享同一套部门信息。
3. `users.primary_department_id / secondary_department_id / tertiary_department_id` 保留，但只作为兼容性冗余缓存字段，不再作为可编辑真源。
4. 管理员新增人员、编辑人员、批量导入人员时，必须填写完整三级部门。
5. 用户注册、首次登录补全、个人中心，不再允许用户自己选择或修改部门。
6. 管理员用户管理页，不再允许直接修改某个账号的部门；部门编辑入口统一收敛到“人员表”。
7. 账号绑定人员、改绑人员、管理员代绑人员时，系统自动把该人员的部门同步到账号冗余字段。
8. 管理员修改人员部门时，系统必须立即把新部门同步到该人员名下所有已绑定账号。
9. 管理员账号不受人员/部门强制限制，可以继续无人员、无部门正常使用系统。

本次不是新增一个孤立小功能，而是一次“部门归属模型”的收口：以后非管理员账号的有效部门，来自人员主档，不来自账号自填。

## Scope

本设计覆盖：

1. `highThinkingQA/server/database/migrations`
2. `public-service/backend/app/modules/personnel`
3. `public-service/backend/app/modules/auth`
4. `public-service/backend/app/modules/admin_users`
5. `public-service/backend/app/modules/departments`
6. `gateway/app/routers/public_proxy.py`
7. `gateway/app/services/route_table.py`
8. `frontend-vue/src/views/Register.vue`
9. `frontend-vue/src/views/UserProfile.vue`
10. `frontend-vue/src/views/AdminDashboard.vue`
11. `frontend-vue/src/components/PersonnelManagementPanel.vue`
12. `frontend-vue/src/services/auth.js`
13. `frontend-vue/src/services/admin.js`
14. 与上述链路直接相关的前后端测试、导入模板和迁移校验

本设计不覆盖：

1. 部门层级模型本身的重构，仍沿用现有三级部门表
2. HR / OA / LDAP 外部同步
3. 人员部门历史版本、时间线审计表
4. “一个人员只能绑定一个账号”的限制
5. 管理员账号强制绑定人员或强制填写部门
6. 组织架构批量迁移工具之外的可视化治理后台

## Hard Boundaries

以下边界是强约束：

1. 当前生效链路仍然是 `frontend-vue -> gateway -> public-service`，不在 deprecated 的 `highThinkingQA` auth/admin 路径上落实现。
2. 非管理员账号的有效部门真源只能来自 `personnel_records`，不能再由 `users` 直接录入或修改。
3. `users` 上的三级部门字段继续保留，但只能通过“人员同步”写入，不能再作为任何前端表单的直写目标。
4. 一个 `personnel` 对应一套唯一三级部门；同一 `personnel` 下全部账号必须一致。
5. 管理员新增/编辑/导入人员时，部门必须是完整、有效、启用中的三级部门，不允许空值、不允许 legacy 两级完成态。
6. 用户自助注册、个人中心、首次登录补全，不再出现部门选择器。
7. 管理员“用户管理”不再提供账号级部门编辑；管理员要改部门，只能改“人员表”。
8. 账号绑定人员、管理员代绑人员、注册绑定人员时，若目标人员没有完整有效的三级部门，则绑定/注册必须失败。
9. 人员解绑时，目标账号的冗余部门字段必须一并清空，避免遗留脏缓存。
10. 管理员账号的 `require_department_setup`、`require_personnel_setup` 必须继续保持豁免，不因本次切换被阻断。
11. 已存在的登录返回和 `GET /api/auth/me` 仍需返回部门相关字段，前端主流程不应因“真源切换”失去兼容字段。
12. 严格按 personnel 真源阻断存量账号之前，必须先完成存量人员部门回填和冲突修复；如果发布顺序无法严格保证，则必须提供迁移期 fallback，不能让老账号因为“人员主档尚未回填部门”被提前拦截。

## Current State

### 1. 当前部门信息真源仍然在 users

当前系统已经有三级部门模型，并且 `users` 已持有：

1. `primary_department_id`
2. `secondary_department_id`
3. `tertiary_department_id`

`auth.service`、`admin_users.service` 当前都直接基于 `users` 上这三列构建部门展示和 `require_department_setup`。

这意味着当前“账号部门”和“人员绑定”是两套独立数据，天然可能漂移。

### 2. 当前 personnel_records 还没有部门字段

当前 `personnel_records` 只承载：

1. `employee_no`
2. `full_name`
3. `verification_code_hash`
4. `status`
5. `remarks`

还没有：

1. `primary_department_id`
2. `secondary_department_id`
3. `tertiary_department_id`

因此现在“人员”无法表达部门归属，也无法成为账号部门的真源。

### 3. 当前注册仍然要求用户自己选部门

当前注册链路已经升级为一次性完成式注册，但 `Register.vue` 和 `auth.register()` 仍要求用户提交：

1. `primary_department_id`
2. `secondary_department_id`
3. `tertiary_department_id`

这与“部门来自人员主档”的目标冲突。

### 4. 当前个人中心仍允许用户自己改部门

当前 `UserProfile.vue` 仍保留部门卡片和 `DepartmentSelector`，用户可以：

1. 查看当前部门
2. 打开部门表单
3. 调用 `PUT /api/auth/department` 保存部门

这会绕开人员主档，继续产生账号级真值。

### 5. 当前管理员用户管理仍允许直接改账号部门

当前 `admin_users` 后端和 `AdminDashboard.vue` 前端仍保留账号级部门修改能力：

1. 新增用户时可传部门
2. 批量导入用户时可传三级部门名称
3. 管理员可直接修改单个用户部门

这同样与“部门统一维护在人员表”冲突。

### 6. 当前人员表维护入口还不承载部门

当前 `PersonnelManagementPanel.vue` 已经支持：

1. 新增人员
2. 编辑人员
3. 启停用
4. 批量导入
5. 模板下载
6. 查看绑定账号

但它维护的是：

1. 工号
2. 姓名
3. 校验码
4. 状态
5. 备注

没有部门输入，也没有部门展示。

### 7. 当前人员绑定不会同步部门

当前无论是用户自助绑定人员，还是管理员为用户代绑人员，本质上都只是更新 `users.personnel_id`。

当前实现没有保证：

1. 绑定后账号部门自动跟随人员
2. 改绑后账号部门同步切换
3. 人员改部门后全部绑定账号立即更新

所以当前系统即使“同时有人员和部门”，两者也没有形成一致性约束。

## Goals

1. 把 `personnel_records` 变成非管理员账号部门归属的唯一业务真源。
2. 管理员新增、编辑、批量导入人员时，必须填写完整三级部门。
3. 一个人员对应一套唯一部门，且其全部账号自动继承该部门。
4. 用户注册时不再自己选部门，部门由通过校验的人员记录自动带出。
5. 用户个人中心不再提供部门编辑能力。
6. 管理员用户管理不再提供账号级部门编辑能力。
7. 人员部门修改后，所有绑定账号立即同步。
8. 保留 `users` 上的部门字段，维持现有登录态、列表态、兼容查询能力。

## Non-Goals

1. 不改变现有三级部门树的数据结构。
2. 不新增“人员-部门历史关系表”。
3. 不强制管理员账号绑定人员。
4. 不把账号表里的部门字段立即物理删除。
5. 不实现人员跨部门历史审计、审批流、通知流。
6. 不在本轮引入“人员导入必须同时绑定账号”。
7. 不在本轮限制一个人员只能有一个账号。

## Options Considered

### Option A: 继续以 users 为真源，同时把部门冗余复制到 personnel

优点：

1. 看起来改动更少
2. 现有注册、个人中心、管理员用户管理几乎都能继续复用

缺点：

1. 仍然保留多处写入口，漂移问题不会消失
2. “一个人员对应一个部门”无法被真正约束
3. 管理员排查问题时，无法确定应该信用户表还是人员表

结论：

不采用。

### Option B: personnel 为真源，users 保留同步缓存

优点：

1. 业务真源单一，职责清晰
2. 能兼容现有登录态、用户列表、权限链路
3. 同步策略明确，改动面可控
4. 适合当前已经上线的 `users` / `auth` / `admin_users` 结构

缺点：

1. 需要同步修改注册、个人中心、管理员用户管理、人员表和导入模板
2. 需要处理存量人员数据回填与冲突报告

结论：

推荐方案。

### Option C: personnel 为真源，并彻底移除 users 上的部门字段

优点：

1. 数据模型最纯
2. 理论上不会再有缓存漂移

缺点：

1. 现有 `auth/me/login/list_users`、前端多处展示都要改成联表或动态解析
2. 风险和改动面明显大于当前需求
3. 会把这次 feature 扩大成一次大规模读模型重构

结论：

暂不采用。

## Recommended Design

### 1. 数据模型

#### 1.1 personnel_records 增加三级部门引用

`personnel_records` 新增：

1. `primary_department_id BIGINT NULL`
2. `secondary_department_id BIGINT NULL`
3. `tertiary_department_id BIGINT NULL`

并增加：

1. 对应索引
2. 对三级部门表的外键

说明：

1. 数据库层本 phase 先允许 `NULL`，是为了平滑承接存量回填与冲突处理。
2. 业务层从本 phase 起就强制“新增/编辑/导入人员必须完整填写三级部门”。
3. 等存量脏数据清理完成后，后续可以再考虑把三列收紧为 `NOT NULL`，但不把它放进本次交付的硬门槛。

#### 1.2 users 继续保留部门字段，但只作为缓存

`users.primary_department_id / secondary_department_id / tertiary_department_id` 继续保留。

新语义定义如下：

1. 这三列不再是人工维护字段。
2. 对非管理员账号，它们只保存“当前绑定人员同步下来的部门缓存”。
3. 任何账号级部门写入接口都不再代表业务真源。

#### 1.3 personnel payload 增加部门展示字段

人员列表、人员详情、人员导入结果等返回体，需要补充：

1. `primary_department_id`
2. `primary_department_name`
3. `secondary_department_id`
4. `secondary_department_name`
5. `tertiary_department_id`
6. `tertiary_department_name`
7. `department_display`
8. `department_completion_level`

其中 `department_display` 统一使用三级名称拼接结果，例如：

`计算机学院 / 软件工程系 / 智能软件实验室`

### 2. 真源与有效部门判定

#### 2.1 非管理员账号

非管理员账号的有效部门判定规则调整为：

1. 如果账号已绑定有效人员，并且该人员拥有完整有效三级部门，则该人员部门为账号有效部门。
2. `users` 上的部门字段只是该有效部门的同步缓存。
3. 如果账号未绑定人员，则账号没有有效部门，无论 `users` 上历史缓存是否还在。
4. 如果账号绑定的人员不存在、已停用或部门不完整，则账号不具备可用部门，需要被阻断。

#### 2.2 管理员账号

管理员账号保持豁免：

1. 可以没有人员
2. 可以没有部门
3. `require_department_setup = false`
4. `require_personnel_setup = false`

管理员是否显示已同步的部门缓存不影响业务限制，但不应再因为这些字段被强制跳资料补全。

### 3. 写入与同步规则

#### 3.1 新增人员

管理员新增人员时：

1. 必须通过 `DepartmentSelector` 选择完整三级部门
2. 后端用现有 `departments` 服务校验三级关系、启用状态和完整性
3. 创建成功后，人员记录自带三级部门引用

#### 3.2 编辑人员

管理员编辑人员时：

1. 允许修改姓名、校验码、备注、部门
2. 如果部门有变化，必须同步刷新所有绑定账号的 `users.*department*` 缓存
3. 同步必须在同一业务操作内完成，不能要求用户重新登录才生效

#### 3.3 批量导入人员

人员导入模板新增以下必填列：

1. `employee_no`
2. `full_name`
3. `verification_code`
4. `status`
5. `primary_department_name`
6. `secondary_department_name`
7. `tertiary_department_name`

保留可选列：

1. `remarks`

导入规则：

1. 导入层按三级部门名称解析成部门 ID
2. 三列部门名称必须同时存在且可解析
3. 解析出的三级部门必须是完整、启用中的有效路径
4. 继续沿用当前“按 `employee_no` upsert”的行为
5. 如果导入覆盖了已有人员的部门，系统必须同步刷新该人员名下所有已绑定账号的部门缓存

这里选择“导入用部门名称、API 用部门 ID”的原因是：

1. 管理员手工维护 Excel/CSV 时，写名称比写 ID 更可读
2. 前端管理表单本来就使用 `DepartmentSelector`，天然更适合传 ID

#### 3.4 用户自助绑定 / 改绑人员

用户在个人中心绑定或改绑人员时：

1. 继续校验 `employee_no + full_name + verification_code`
2. 除了校验人员存在且启用，还必须校验该人员拥有完整有效的三级部门
3. 绑定成功后，立即把人员部门同步到 `users` 缓存
4. 返回给前端的用户 payload 必须已经是同步后的最新部门

#### 3.5 管理员代绑 / 解绑人员

管理员在用户管理中为用户代绑人员时：

1. 只能选择有效且部门完整的人员
2. 代绑成功后，立即同步用户部门缓存

管理员解绑人员时：

1. 仅清空目标账号的 `personnel_id`
2. 同时清空目标账号的三级部门缓存
3. 解绑后的非管理员账号重新进入 `require_personnel_setup = true`

#### 3.6 注册

注册流程改为：

1. 前端不再提交部门字段
2. 后端根据 `employee_no + full_name + verification_code` 找到人员记录
3. 校验人员状态和部门完整性
4. 创建账号时直接把该人员的三级部门写入 `users` 缓存

因此，注册后的账号不需要再补部门。

### 4. API Contract Changes

#### 4.1 Personnel API

`POST /api/admin/personnel`

新增必填字段：

1. `primary_department_id`
2. `secondary_department_id`
3. `tertiary_department_id`

`PUT /api/admin/personnel/{personnel_id}`

建议改成支持以下字段：

1. `full_name`
2. `verification_code` 可选
3. `remarks` 可选
4. `primary_department_id`
5. `secondary_department_id`
6. `tertiary_department_id`

更新规则：

1. 如果提交了任一部门字段，则必须同时提交完整三级部门
2. 如果部门有变化，服务层负责同步绑定账号

#### 4.2 Personnel Import Template

`GET /api/admin/personnel/import-template`

模板示例数据必须更新为带三级部门名称的版本。

`POST /api/admin/personnel/batch-import`

服务端校验扩展为：

1. 必填部门列存在
2. 部门名称可解析
3. 解析路径完整且启用

#### 4.3 Auth Register

`POST /api/auth/register` 与 `POST /api/v1/auth/register`

移除请求字段：

1. `primary_department_id`
2. `secondary_department_id`
3. `tertiary_department_id`

保留：

1. `username`
2. `password`
3. `employee_no`
4. `full_name`
5. `verification_code`
6. `security_questions`

返回体中的部门字段继续保留，但它们来自人员同步结果，而不是用户提交。

#### 4.4 Auth Department Update

`PUT /api/auth/department` 与 `PUT /api/v1/auth/department`

不再是有效写入口。

建议保留路由但改成明确拒绝，返回业务错误码，例如：

1. `DEPARTMENT_MANAGED_BY_PERSONNEL`

原因：

1. 可以避免旧前端或遗留调用静默写脏数据
2. 错误语义比直接删除路由更清晰

#### 4.5 Admin User Department Update

管理员账号级部门更新接口同样不再是有效写入口。无论是 `/api/admin/users/{user_id}/department` 还是其任何兼容包装，都建议返回：

1. `DEPARTMENT_MANAGED_BY_PERSONNEL`

同时，管理员新增用户和批量导入用户的请求契约、模板字段里都应移除三级部门输入，避免继续出现双写入口。

### 5. 前端交互调整

#### 5.1 Register.vue

注册页删除“部门信息”区块：

1. 不再拉取部门树
2. 不再渲染 `DepartmentSelector`
3. 页面说明改成“账号、人员校验、安全问题”

注册成功后，用户拿到的部门展示来自人员。

#### 5.2 UserProfile.vue

个人中心里的部门卡片改成只读展示：

1. 仍可显示当前部门
2. 不再出现“填写部门/修改部门”按钮
3. 不再出现部门表单和保存逻辑

如果后端返回 `require_department_setup = true`，说明当前绑定人员的部门主档不完整或无效。

此时页面应显示阻断提示：

1. 告知用户“当前人员所属部门未配置完成，请联系管理员在人员表中维护”
2. 不提供用户自助修复入口

#### 5.3 AdminDashboard.vue

管理员用户管理页需要收口：

1. 新增用户弹窗移除部门选择
2. 修改用户部门入口移除
3. 用户列表里的部门仍可展示，但只读
4. 人员绑定成功后，列表中的部门展示自动更新

#### 5.4 PersonnelManagementPanel.vue

人员管理面板需要新增部门维护能力：

1. 新增人员时必须选择三级部门
2. 编辑人员时可以修改三级部门
3. 列表中展示人员当前部门
4. 批量导入说明中明确三级部门为必填

当前 `window.prompt` 式新增/编辑已不足以承载三级搜索选择，前端需要升级为表单化交互。

建议复用已有 `DepartmentSelector`，因为它已经具备搜索能力，符合之前确认过的“选择时可搜索部门”的要求。

### 6. 阻断语义

为了兼容现有资料补全路由，建议沿用两个标记，但调整含义：

#### 6.1 require_personnel_setup

以下情况为 `true`：

1. 非管理员账号未绑定人员
2. 绑定的人员不存在
3. 绑定的人员已停用

此时用户应被拦到个人中心的人員绑定区块，重新绑定有效人员。

#### 6.2 require_department_setup

以下情况为 `true`：

1. 非管理员账号绑定了人员
2. 该人员存在且启用
3. 但该人员缺少完整有效三级部门

此时用户同样会被拦到个人中心，但看到的是“联系管理员补人员部门”的只读阻断卡片，而不是部门编辑表单。

说明：

1. 正常情况下，这个状态只应出现在存量脏数据或迁移未完成场景。
2. 新创建、导入、绑定、注册链路都必须杜绝生成这种新坏数据。

### 7. 兼容与迁移

#### 7.1 数据库迁移

迁移脚本需要：

1. 给 `personnel_records` 加三级部门列
2. 加索引和外键
3. 不删除 `users` 上原有三级部门列

#### 7.2 存量数据回填

需要提供一次性回填逻辑，把现有人员记录尽量补齐部门：

1. 遍历每个 `personnel_records`
2. 找出其当前绑定的所有账号
3. 提取这些账号上的“完整三级部门组合”

处理规则：

1. 若只有一组唯一且完整的三级部门，则自动回填到该人员
2. 若没有任何完整三级部门，则标记为 `missing_department`
3. 若存在多组冲突的三级部门，则标记为 `conflicting_departments`

对第 2、3 类记录：

1. 不自动猜测
2. 输出冲突报告供管理员人工修复
3. 回填前后都不应静默覆盖管理员无法感知的数据

#### 7.3 真源切换门槛

“按 personnel 严格判定有效部门并对缺失主档做阻断”不能和“新增字段上线”同时默认打开。

必须满足以下二选一之一：

1. 先完成存量回填和人工冲突修复，再发布严格真源判定版本
2. 如果无法保证发布顺序，则实现迁移期 fallback：对于“已绑定人员但该人员尚未回填部门”的存量账号，暂时继续容忍 legacy `users` 部门缓存，不立即置 `require_department_setup=true`

推荐做法：

1. 先上线“人员表可维护部门 + 新写入链路同步部门”
2. 再执行回填并修复冲突
3. 最后切换到严格 personnel 真源判定

只有在第 3 步完成后，`require_department_setup=true` 才应该真正只代表“人员主档部门本身异常”，而不是“迁移暂未完成”。

#### 7.4 读模型兼容

回填未完成前，`users` 上可能仍残留历史部门缓存。

兼容原则：

1. 对新创建账号、新注册账号、新绑定/改绑账号，后端必须立刻按 personnel 真源工作，不能回退到旧账号缓存
2. 对存量已绑定账号，在严格真源切换前，可以短暂容忍 legacy `users` 缓存作为迁移期 fallback
3. 对未绑定人员的账号，历史部门缓存不再构成“有效部门”
4. 一旦发生绑定、改绑、解绑、人员改部门，就立即用新规则覆盖/清空缓存

这样可以避免历史残留缓存掩盖真实脏数据。

### 8. Error Handling

建议新增或统一以下业务错误码：

1. `DEPARTMENT_MANAGED_BY_PERSONNEL`
2. `PERSONNEL_DEPARTMENT_REQUIRED`
3. `PERSONNEL_DEPARTMENT_INVALID`
4. `PERSONNEL_DEPARTMENT_DISABLED`
5. `PERSONNEL_DEPARTMENT_INCOMPLETE`

典型语义：

1. 新增/编辑/导入人员缺部门时，返回 `PERSONNEL_DEPARTMENT_REQUIRED`
2. 绑定或注册到缺部门人员时，返回 `PERSONNEL_DEPARTMENT_INCOMPLETE`
3. 旧入口再试图直接写账号部门时，返回 `DEPARTMENT_MANAGED_BY_PERSONNEL`

### 9. Testing Requirements

后端至少覆盖：

1. `personnel_records` 新增三级部门字段后的 repository 读写
2. 人员新增/编辑/导入必须带完整三级部门
3. 人员改部门后，绑定账号缓存被同步更新
4. 用户自助绑定人员时同步部门
5. 管理员代绑/解绑人员时同步或清空部门
6. 注册不再接收部门字段，而是从人员带出部门
7. 旧的账号级部门更新接口被明确拒绝
8. `/api/*` 与 `/api/v1/*` 的 register / department update 契约保持完全一致
9. `login` / `me` 在存量脏数据场景下正确返回 `require_department_setup` / `require_personnel_setup`
10. 迁移期 fallback 与严格真源切换后的行为分别被覆盖

前端至少覆盖：

1. 注册页不再渲染 `DepartmentSelector`
2. 个人中心不再提供部门编辑按钮和保存逻辑
3. 个人中心在 `require_department_setup=true` 时展示只读阻断提示
4. 管理员用户管理移除账号级部门编辑
5. 人员管理新增/编辑表单必须包含可搜索的三级部门选择
6. 人员导入模板文案和结果展示包含部门字段

gateway 至少覆盖：

1. 现有人员、注册、账号管理路由表仍然完整
2. `/api/*` 与 `/api/v1/*` 相关路由语义保持同步
3. 不需要新增全新路径，但现有路径的契约测试要跟随更新

## Recommended Rollout

推荐按以下顺序交付：

1. 先做数据库迁移和人员表数据模型扩展
2. 再做人员表写入口、绑定链路、注册链路的部门同步逻辑
3. 再补存量数据回填与冲突报告，并完成人工修复
4. 再同时发布“前端去掉旧部门入口”与“后端开启严格 personnel 真源判定 + 旧入口封禁”

原因：

1. 真源切换必须先有数据承载位
2. 新写入链路先写对，回填结果才不会被新脏数据继续污染
3. 严格阻断必须建立在回填和冲突修复完成之后
4. 前端去入口必须和后端封禁一起收口，避免用户撞到半切换状态
