# User Personnel Binding Design

**Date:** 2026-04-20

## Summary

本设计定义当前系统中“账号绑定人员身份”的新增方案，目标是让每个账号都能对应到受控的人员主档，便于管理员按工号和姓名快速定位真实使用人。

本次能力的核心原则是：

1. 管理员维护一张独立的“人员表”，作为人员主数据真源。
2. 账号体系继续独立存在，用户名不是工号，也不替代人员主档。
3. 每个账号必须绑定一条人员记录后，才能正常使用系统。
4. 同一条人员记录允许绑定多个账号，不限制“一人只能一个账号”。
5. 用户首次绑定和后续改绑，都必须校验 `工号 + 姓名 + 校验码`。
6. 当人员记录被管理员停用时，所有已绑定该人员的账号都会被强制拦截，直到重新绑定到有效人员或由管理员处理。

当前系统已经存在三类账号前置补全/校验链路：

1. 首次登录修改密码
2. 设置安全问题
3. 补全部门

本次设计新增第四类前置能力：

4. 绑定有效人员身份

设计重点不是把工号/姓名直接塞进 `users` 表，而是新增独立的“人员表”模块，并通过账号到人员的绑定关系实现身份定位、停用拦截、管理员搜索和后续批量维护。

## Scope

本设计覆盖：

1. `public-service/backend/app/modules/auth`
2. `public-service/backend/app/modules/admin_users`
3. 新增 `public-service/backend/app/modules/personnel`
4. `gateway/app/routers/public_proxy.py`
5. `gateway/app/services/route_table.py`
6. `frontend-vue/src/router/profileSetup.js`
7. `frontend-vue/src/views/UserProfile.vue`
8. `frontend-vue/src/views/AdminDashboard.vue`
9. `frontend-vue/src/services/auth.js`
10. `frontend-vue/src/services/admin.js`
11. 新增或扩展管理员“人员表”相关前端组件
12. `users` 表与人员主档相关数据库迁移
13. 人员导入模板、校验、管理员查询与用户绑定相关测试

本设计不覆盖：

1. 与 OA / HR / LDAP 的外部自动同步
2. 手机号、邮箱、身份证等更多实名字段
3. 一个账号同时绑定多条人员记录
4. 人员删除能力
5. 校验码短信/邮件发送
6. 工号维度的数据权限隔离
7. 历史组织架构与人员异动同步

## Hard Boundaries

以下边界是强约束：

1. 当前生效链路仍然是 `frontend-vue -> gateway -> public-service`，不在 `highThinkingQA` 的 deprecated auth/admin 路由上实现新能力。
2. 人员主数据只能来自管理员维护的“人员表”，用户不能自由录入新人员。
3. 账号和人员是两个概念：用户名不等于工号，账号表不承载人员主数据真值。
4. 同一条人员记录允许绑定多个账号；同一个账号在任意时刻只能绑定一条人员记录。
5. 用户自助首次绑定和改绑，必须都校验 `工号 + 姓名 + 校验码`，只校验工号或只校验姓名都不可接受。
6. 人员记录停用后，不允许新绑定，也不允许已绑定账号继续正常使用。
7. 管理员维护人员表时只支持新增、编辑、启用、停用、导入，不支持删除。
8. 校验码必须由管理员创建/导入或重置，但不应在系统中明文回显给管理员或用户。
9. 登录返回和 `GET /api/auth/me` 必须都暴露人员绑定状态与 `require_personnel_setup`，不能只在前端本地推断。
10. 本轮不限制一个人员只能绑定一个账号。
11. 管理员界面保持当前一级 tab 结构，不新增新的管理员顶级页签；“人员表”作为“用户管理”一级下的子 tab。

## Current State

### 1. 当前真实生效的是 public-service 用户体系

当前仓库里的用户和管理员 HTTP 能力，真正运行时由 `public-service` 提供，并由 `gateway` 统一代理：

1. `public-service/backend/app/main.py` 已注册 `auth_router` 与 `admin_users_router`
2. `gateway/app/routers/public_proxy.py` 已代理 `/api/auth/*` 与 `/api/admin/*`
3. `highThinkingQA/server_fastapi/routers/auth.py` 与 `highThinkingQA/server_fastapi/routers/admin.py` 已明确标记为 deprecated

因此本次设计的后端实现应该落在 `public-service`，同时补齐 `gateway` 的新路由代理。

### 2. 当前前置校验链路只有密码、安全问题、部门

当前后端认证服务在登录和 `me` 接口里已经返回：

1. `is_first_login`
2. `require_security_questions_setup`
3. `require_department_setup`

当前前端通过 `frontend-vue/src/router/profileSetup.js` 统一判断是否需要强制跳转个人中心，个人中心 `UserProfile.vue` 已承载：

1. 修改密码
2. 安全问题设置
3. 部门补全
4. 用户名修改

这说明“人员绑定”最稳妥的接法不是新造一套路由，而是沿用现有 profile 强制补全模型，新增一个独立的后端标记。

### 3. 当前管理员页面已经有稳定的一层 tab 结构

当前管理员页 `AdminDashboard.vue` 顶部一级 tab 已经稳定为：

1. `配额管理`
2. `用户管理`
3. `部门管理`

因此人员维护能力不应再挤入顶层，而应作为“用户管理”内部的二级结构，避免继续横向膨胀管理员入口。

### 4. 当前用户和管理员能力里没有人员主档概念

当前 live 生效能力只识别：

1. `username`
2. `role`
3. `user_type`
4. `department`
5. `security_questions`

没有：

1. `工号`
2. `姓名`
3. `人员状态`
4. `校验码`
5. `账号绑定人员`

这意味着本次能力必须新增新的数据模型，而不是复用用户名或部门字段做“伪实名”。

### 5. 当前 users 表已经承载部门绑定，但没有人员绑定

在最近一轮部门升级后，`users` 表已经扩展承载部门引用；本次能力需要继续在 `users` 上增加一个当前人员绑定引用，而不是把工号、姓名、校验码直接平铺在 `users` 上。

## Goals

1. 管理员可以维护独立的人员表，按工号、姓名搜索和定位真实人员。
2. 用户必须绑定人员信息后，才能正常使用系统。
3. 用户可以自助首次绑定和后续改绑，但都必须重新校验工号、姓名和校验码。
4. 同一人员可以绑定多个账号。
5. 管理员可以查看某条人员当前绑定了哪些账号。
6. 管理员可以为用户直接修复或调整绑定关系。
7. 人员停用后，已绑定账号会被强制拦截。
8. 人员表支持批量导入与模板下载。

## Non-Goals

1. 不把用户名直接替换成工号。
2. 不限制一个人员只能绑定一个账号。
3. 不做 HR 系统双向同步。
4. 不做人员删除和物理清理。
5. 不把校验码作为登录凭证使用。
6. 不做短信验证码、邮箱验证码等二次验证。

## Options Considered

### Option A: 直接把工号/姓名/校验码/状态放进 users 表

优点：

1. 表面上改动少
2. 对单账号场景上手快

缺点：

1. 无法自然支持“一人多账号”
2. 人员主数据和账号资料会混在一起
3. 管理员按工号反查多个账号会很别扭
4. 人员停用和账号状态会彼此污染

结论：

不推荐。

### Option B: 独立人员表 + users.personnel_id 当前绑定

优点：

1. 数据边界清晰
2. 一个人绑定多个账号天然成立
3. 账号当前绑定关系读取简单
4. 管理员按人员反查账号容易实现
5. 与当前部门真源设计一致

缺点：

1. 需要同时改 auth、admin、gateway、frontend
2. 如果将来需要完整历史审计，还要补充操作日志

结论：

推荐方案。

### Option C: 独立人员表 + 独立绑定表，不在 users 上保存当前绑定

优点：

1. 历史建模更纯粹
2. 后续审计扩展性最好

缺点：

1. 当前所有 `me/login/list_users` 都需要额外解析“当前生效绑定”
2. 首版复杂度偏高
3. 对当前需求属于过度设计

结论：

暂不采用。

## Recommended Design

### 1. 模块边界

新增独立模块 `public-service/backend/app/modules/personnel`，职责只包括人员主档本身：

1. 人员表查询
2. 人员表新增/编辑
3. 启用/停用
4. 校验码校验
5. 人员导入
6. 查看某个人员当前绑定的账号

其余模块职责如下：

1. `auth` 负责返回当前账号的人员绑定状态，并处理用户自助绑定/改绑
2. `admin_users` 负责管理员在用户维度上查看和修复绑定关系
3. `gateway` 只负责代理新增 HTTP 接口

### 2. 数据模型

#### 2.1 人员主档表

建议新增表：`personnel_records`

字段：

1. `id BIGINT AUTO_INCREMENT PRIMARY KEY`
2. `employee_no VARCHAR(64) NOT NULL`
3. `full_name VARCHAR(64) NOT NULL`
4. `verification_code_hash VARCHAR(255) NOT NULL`
5. `status ENUM('active','disabled') NOT NULL DEFAULT 'active'`
6. `remarks VARCHAR(255) NULL`
7. `created_at`
8. `updated_at`

约束：

1. `employee_no` 全局唯一
2. 不允许删除，只允许启用/停用
3. 校验码只存 hash，不存明文

说明：

1. 管理员导入模板里仍然提供“明文校验码”列，导入时后端负责哈希
2. 管理员编辑人员记录时，可以重置校验码，但系统不支持回显旧码

#### 2.2 用户表扩展

扩展 `users` 表：

1. `personnel_id BIGINT NULL`

约束：

1. 允许为空，兼容存量账号
2. 外键指向 `personnel_records(id)`，推荐 `ON DELETE RESTRICT` 或直接通过业务禁止删除
3. 一个账号在任意时刻只能绑定一条人员记录

说明：

1. 不在 `users` 上新增 `employee_no`、`full_name`、`verification_code`
2. 账号当前展示的工号和姓名都通过关联人员表实时读取

#### 2.3 可选审计

本轮不要求引入完整历史绑定表作为硬依赖，但推荐至少记录绑定/改绑/管理员修复日志，形式可以是：

1. 单独表 `personnel_binding_audit`
2. 或先写应用审计日志

如果首版需要压复杂度，可以先落应用日志，后续再补持久化审计表。

### 3. 状态模型

本设计明确区分两套状态，避免实现时混淆：

1. `personnel_record_status`：人员主档自身状态，只允许 `active | disabled`
2. `personnel_binding_status`：账号和人员绑定关系的运行态，只允许 `unbound | bound_active | bound_disabled | bound_missing`

其中：

1. `personnel_record_status` 只用于人员表 CRUD、列表筛选、导入、启停用等“人员主档”语义
2. `personnel_binding_status` 只用于 `login`、`GET /api/auth/me`、用户列表、路由守卫、个人中心拦截等“账号当前是否可用”语义

后端必须显式区分以下人员绑定状态：

1. `unbound`：账号未绑定任何人员
2. `bound_active`：已绑定到启用中的人员
3. `bound_disabled`：已绑定到已停用人员
4. `bound_missing`：理论上不应出现；若出现说明数据异常，也按阻断处理

用户态与账号态接口统一导出如下字段：

1. `personnel_id`
2. `employee_no`
3. `full_name`
4. `personnel_binding_status`
5. `require_personnel_setup`

如果当前账号已绑定人员，且调用的是管理员“人员表”接口，则该人员主档仍单独返回：

1. `personnel_record_status`

判断规则：

1. `unbound` -> `require_personnel_setup = true`
2. `bound_active` -> `require_personnel_setup = false`
3. `bound_disabled` -> `require_personnel_setup = true`
4. `bound_missing` -> `require_personnel_setup = true`

### 4. 登录与 me 契约

后端 `login` 与 `GET /api/auth/me` 都要扩展当前用户载荷，新增：

1. `personnel_id`
2. `employee_no`
3. `full_name`
4. `personnel_binding_status`
5. `require_personnel_setup`

契约原则：

1. 不改变两个接口现有的大体外形
2. 人员绑定字段直接并入当前用户 payload
3. 前端缓存用户信息时直接缓存这些平铺字段
4. 路由守卫不再自己猜测绑定状态，只认后端返回的 `require_personnel_setup`

### 5. 用户自助绑定与改绑

新增接口：

1. `PUT /api/auth/personnel-binding`

请求体：

1. `employee_no`
2. `full_name`
3. `verification_code`

行为：

1. 当前账号未绑定时，执行首次绑定
2. 当前账号已绑定时，执行改绑
3. 两种情况都必须重新校验 `工号 + 姓名 + 校验码`
4. 只要校验成功，就把 `users.personnel_id` 更新为目标人员
5. 不限制目标人员当前是否已经绑定了其它账号

错误处理：

1. 任一字段为空 -> `VALIDATION_ERROR`
2. 工号不存在 -> 返回统一校验失败，不暴露“工号是否存在”
3. 工号存在但姓名不匹配 -> 返回统一校验失败
4. 校验码错误 -> 返回统一校验失败
5. 人员状态是 `disabled` -> 返回 `PERSONNEL_DISABLED`

安全要求：

1. 校验码使用 hash 比对
2. 错误消息统一收口为“人员信息校验失败”或“该人员已停用”，避免枚举过多内部细节

### 6. 强制拦截与个人中心

`frontend-vue/src/router/profileSetup.js` 增加一条判断：

1. `require_personnel_setup`

新的强制跳转优先级沿用现有模型，不拆新路由，统一跳 `/profile`。`buildRequiredProfilePath()` 新增：

1. `personnel=required`

`UserProfile.vue` 新增“人员绑定”区域：

1. 展示当前绑定的工号、姓名、状态
2. 支持首次绑定
3. 支持重新校验后改绑
4. 当后端返回 `personnel_binding_status = bound_disabled` 时，展示“当前绑定人员已停用，请重新绑定或联系管理员”

如果账号被人员状态拦截：

1. 仍允许进入个人中心
2. 不允许进入系统其它页面
3. 直到绑定到 `active` 人员后才解除拦截

### 7. 管理员界面

#### 7.1 导航结构

保持当前管理员一级 tab 不变：

1. 配额管理
2. 用户管理
3. 部门管理

在“用户管理”内新增二级子 tab：

1. `账号列表`
2. `人员表`

#### 7.2 人员表能力

管理员“人员表”子 tab 至少支持：

1. 列表查看
2. 按工号搜索
3. 按姓名搜索
4. 按状态筛选
5. 新增人员
6. 编辑姓名、备注、校验码
7. 启用/停用
8. 批量导入
9. 模板下载
10. 查看该人员绑定的账号列表

建议列表列：

1. 工号
2. 姓名
3. 状态
4. 绑定账号数
5. 更新时间
6. 操作

校验码展示策略：

1. 不在列表中展示明文校验码
2. 编辑时只允许“重置为新校验码”，不支持查看旧值

#### 7.3 账号列表能力扩展

管理员查看用户列表时，新增一列：

1. `人员信息`

显示格式建议：

1. `工号 / 姓名`
2. 未绑定显示 `未绑定`
3. 已停用显示 `工号 / 姓名（已停用）`

管理员用户详情或编辑弹窗里，支持：

1. 查看当前绑定人员
2. 通过人员搜索选择直接改绑
3. 解除绑定

管理员改绑用户时不需要校验码，因为这是后台修复能力，但必须保留操作日志。

管理员解除绑定后的结果必须明确为：

1. `users.personnel_id` 被置为 `NULL`
2. 该用户下一次 `login` 或 `GET /api/auth/me` 返回 `personnel_binding_status = unbound`
3. 同时返回 `require_personnel_setup = true`
4. 被解绑用户会重新进入强制补全链路，直到重新绑定有效人员

### 8. 管理员接口设计

新增人员表接口：

1. `GET /api/admin/personnel`
2. `POST /api/admin/personnel`
3. `PUT /api/admin/personnel/{personnel_id}`
4. `PUT /api/admin/personnel/{personnel_id}/status`
5. `GET /api/admin/personnel/{personnel_id}/bindings`
6. `POST /api/admin/personnel/batch-import`
7. `GET /api/admin/personnel/import-template`

新增或扩展用户绑定接口：

1. `PUT /api/admin/users/{user_id}/personnel-binding`
2. `DELETE /api/admin/users/{user_id}/personnel-binding`

管理员绑定请求建议直接传 `personnel_id`，而不是工号/姓名/校验码组合，避免后台二次模糊匹配。

接口契约明确如下：

1. `PUT /api/admin/users/{user_id}/personnel-binding`
2. 请求体固定为 `{"personnel_id": number}`
3. 目标 `personnel_id` 必须存在且状态为 `active`
4. 管理员改绑成功后，`users.personnel_id` 更新为目标人员
5. `DELETE /api/admin/users/{user_id}/personnel-binding`
6. 解绑成功后，`users.personnel_id = NULL`
7. 解绑后的用户立即回到 `personnel_binding_status = unbound` 且 `require_personnel_setup = true`
8. 两类后台操作都必须记录操作日志，至少包含操作人、目标用户、原 personnel_id、新 personnel_id、时间

### 9. 批量导入

人员导入模板字段建议固定为：

1. `employee_no`
2. `full_name`
3. `verification_code`
4. `status`
5. `remarks` 可选

导入规则：

1. `employee_no` 必填且唯一
2. `full_name` 必填
3. `verification_code` 必填
4. `status` 必须是 `active` 或 `disabled`
5. 同一次导入文件内如果出现重复 `employee_no`，整次导入直接判失败，并返回重复行号，避免“最后一行覆盖前一行”的歧义
6. 如果导入行的 `employee_no` 已存在于数据库，则按“更新已有人员”处理，而不是报错
7. 更新已有人员时，`full_name`、`status`、`remarks` 必须按导入值覆盖
8. 更新已有人员时，`verification_code` 必须按导入值重新计算 hash 后覆盖旧值
9. 如果导入行的 `employee_no` 在数据库中不存在，则创建新人员记录

这样管理员可以通过导入完成批量新增、批量更名、批量重置校验码和批量启停用，且行为是确定性的。

### 10. 安全设计

#### 10.1 校验码存储

校验码不允许明文落库，推荐复用当前密码 hash 思路或使用单独安全 hash。

要求：

1. 导入和新增时接收明文
2. 存储时写入 hash
3. 查询接口不返回原码
4. 修改时只能覆盖重置

#### 10.2 错误信息最小暴露

用户自助绑定接口不要分别返回“工号不存在 / 姓名不匹配 / 校验码错误”，而应收口为：

1. `人员信息校验失败`
2. `该人员已停用`

这样可以减少利用接口枚举人员主档的风险。

#### 10.3 强停用风险

“强停用”意味着管理员停用人员记录后，绑定该人员的账号立即不可正常使用。

这符合需求，但会引出一个运维风险：

1. 如果误停用了当前唯一活跃管理员对应的人员，可能导致后台账号也被拦截

推荐防护：

1. 停用前提示“该人员当前绑定 X 个账号”
2. 如果其绑定了管理员账号，给出更强确认提示
3. 是否阻止“停用最后一个活跃管理员对应人员”可在实现阶段决定；本设计先不强制，但必须在 spec 中明确这是已知风险

### 11. 数据读取与展示原则

1. 账号当前显示的工号和姓名，以 `users.personnel_id -> personnel_records` 的实时关联结果为准
2. 不在账号表保存工号和姓名快照
3. 人员姓名被管理员更正后，所有绑定账号展示同步更新

### 12. 迁移与兼容

数据库迁移建议：

1. 新增 `personnel_records`
2. 扩展 `users.personnel_id`
3. 补索引与外键

存量兼容策略：

1. 现有账号初始 `personnel_id = NULL`
2. 上线后这些账号会被视为 `require_personnel_setup = true`
3. 用户必须在个人中心完成绑定后才能继续使用

如果担心一次性强切影响太大，可以预留灰度开关：

1. `PERSONNEL_BINDING_ENFORCED=true/false`

但默认设计目标是最终强制开启，不长期保留软兼容分支。

### 13. 测试与验证

后端测试至少覆盖：

1. 人员表 CRUD 与状态变更
2. 人员导入模板和导入校验
3. `login` / `me` 返回人员绑定状态
4. 未绑定账号被标记为 `require_personnel_setup`
5. 绑定成功后解除拦截
6. 改绑必须重新校验
7. 同一人员可绑定多个账号
8. 停用人员会让已绑定账号被拦截
9. 管理员改绑用户无需校验码
10. 管理员解绑用户后，用户重新变为 `unbound`
11. 同一导入文件内重复 `employee_no` 会整体失败
12. 导入命中已有 `employee_no` 时会按规则覆盖更新

前端测试至少覆盖：

1. 路由守卫新增 `require_personnel_setup`
2. 个人中心强制绑定流程
3. 改绑表单提交流程
4. 已停用人员绑定的阻断展示
5. 管理员“用户管理 -> 人员表”二级 tab
6. 人员表搜索与状态展示
7. 管理员查看某人员已绑定账号列表

## Open Questions

当前设计默认以下决策已经确定：

1. 同一人员允许绑定多个账号
2. 绑定和改绑都要重新校验
3. 校验码由管理员导入/维护
4. 人员停用后强拦截已绑定账号
5. 人员表作为“用户管理”下的子 tab

如果后续实现前还要再确认，主要只剩一个可选细节：

1. 是否要阻止“停用最后一个活跃管理员绑定的人员记录”

本 spec 先把它作为实现时的安全增强点，而不是阻塞设计主线的前置条件。

## Acceptance Criteria

当以下条件都成立时，本设计视为完成：

1. 管理员可以在“用户管理 -> 人员表”中维护人员主档
2. 人员表支持批量导入与模板下载
3. 用户未绑定人员时会被强制跳到个人中心
4. 用户可以通过 `工号 + 姓名 + 校验码` 首次绑定
5. 用户可以通过重新校验完成改绑
6. 同一人员可以同时绑定多个账号
7. 管理员可以按用户维度修复或修改绑定
8. 管理员可以查看某个人员绑定了哪些账号
9. 人员停用后，已绑定账号被强制阻断
10. 管理员解绑用户后，被解绑账号重新进入强制绑定流程
11. 登录、`me`、路由守卫、个人中心、管理员界面在人员绑定状态上保持一致
