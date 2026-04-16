# User Department Management Design

**Date:** 2026-04-16

## Summary

本设计定义当前系统中“用户部门信息”能力的新增方案，覆盖管理员维护两级部门字典、管理员为用户设置部门、用户首次登录强制补全部门、以及批量导入按部门名称匹配字典的完整链路。

当前系统已经存在两类首次登录强制动作：

1. 修改密码
2. 设置至少一个安全问题

本次设计在不改变现有认证主链路的前提下，新增第三类强制动作：

3. 补全一级部门与二级部门

部门必须来自管理员维护的受控字典，不允许自由输入。一级和二级必须同时存在才算完整。只要用户部门为空，就必须被强制拦到个人中心补全后才能正常使用系统。

## Scope

本设计覆盖：

1. `public-service/backend/app/modules/auth`
2. `public-service/backend/app/modules/admin_users`
3. 新增 `public-service/backend/app/modules/departments`
4. `gateway/app/routers/public_proxy.py`
5. `gateway/app/services/route_table.py`
6. `frontend-vue/src/views/Login.vue`
7. `frontend-vue/src/router/index.js`
8. `frontend-vue/src/views/UserProfile.vue`
9. `frontend-vue/src/views/AdminDashboard.vue`
10. `frontend-vue/src/services/auth.js`
11. `frontend-vue/src/services/admin.js`
12. 新增前端部门管理与搜索选择相关组件或服务
13. 用户表与部门字典相关数据库迁移
14. 与上述能力相关的后端测试、前端构建校验、接口契约测试

本设计不覆盖：

1. 部门维度的数据权限
2. 按部门统计报表
3. 部门导出能力
4. 管理员/用户自由录入自定义部门
5. 删除部门字典项
6. 组织架构历史版本追踪
7. 除个人中心之外的用户资料页重构

## Hard Boundaries

以下边界是强约束：

1. 当前生效链路仍然是 `frontend-vue -> gateway -> public-service`，不在 `highThinkingQA` 的 deprecated auth/admin 路由上实现新功能。
2. 部门必须来自管理员维护的字典，只允许选择，不允许自由输入。
3. 用户部门完整性的定义是“一级部门和二级部门都存在，且二级部门属于当前一级部门”；只填一级或只填二级都视为未完成。
4. 用户部门为空时，必须像当前首次改密码/安全问题一样，被强制拦到 `/profile` 补全后才能继续使用系统。
5. 管理员新增用户、管理员修改用户、用户自助修改部门，前端交互都使用字典选择；批量导入按名称匹配字典。
6. 部门字典只支持新增、修改、启用、停用，不支持删除。
7. 停用部门后不允许新绑定，但已绑定该部门的老用户关系保留，不自动清空。
8. 批量导入时，两个部门列可以同时留空；但只填一级或只填二级必须判失败。
9. 登录返回和 `GET /api/auth/me` 必须都暴露部门状态与 `require_department_setup`，不能只在前端本地推断。
10. 现有 live 库中 `users.role` 仍是 `enum('user','admin')`，`super` 身份继续通过 `user_type=2` 表示；本次部门需求不重构这套角色编码。

## Current State

### 1. 当前真实生效的是 public-service 用户体系

当前仓库里的用户和管理员 HTTP 能力，真正运行时由 `public-service` 提供，并由 `gateway` 统一代理：

1. `public-service/backend/app/main.py` 已注册 `auth_router` 与 `admin_users_router`
2. `gateway/app/routers/public_proxy.py` 已代理 `/api/auth/*` 与 `/api/admin/*`
3. `highThinkingQA/server_fastapi/routers/auth.py` 与 `highThinkingQA/server_fastapi/routers/admin.py` 已明确标记为 deprecated，不再是当前架构的实现落点

因此本次设计的后端实现应该落在 `public-service`，同时补齐 `gateway` 的路由代理与路由表。

### 2. 首次登录强制流程当前只覆盖密码与安全问题

当前后端认证服务在登录和 `me` 接口里返回：

1. `is_first_login`
2. `has_security_questions`
3. `require_security_questions_setup`

当前前端使用这两个条件做强制跳转：

1. 登录页根据登录响应跳转 `/profile`
2. 路由守卫在每次鉴权成功后再次检查并强制拦截
3. 个人中心页面负责完成改密码与安全问题设置

这意味着“强制补全部门”最稳妥的接法不是新开一条特殊流程，而是沿用现有模式，再新增一个后端返回的 `require_department_setup` 标记。

### 3. 用户资料页没有现成的部门编辑接口

当前 `UserProfile.vue` 只包含：

1. 基本信息展示
2. 修改密码
3. 安全问题设置
4. 配额显示

后端 `auth` 模块当前也没有“更新个人资料”或“更新部门”的接口。要支持用户补全部门，必须新增专用的部门更新接口。

### 4. 管理员用户管理当前没有部门概念

当前管理员能力只覆盖：

1. 用户列表
2. 单个创建
3. 重置密码
4. 启停用
5. 用户类型切换
6. 删除
7. 批量删除
8. 批量切换类型
9. Excel/CSV 批量导入

管理员新增用户的请求体当前只有：

1. `username`
2. `password`
3. `user_type`

批量导入模板当前也只有：

1. `username`
2. `password`
3. `user_type`

因此部门能力需要同时扩展：

1. 管理员单建用户
2. 管理员修改用户部门
3. 批量导入模板与导入校验
4. 管理员界面的列表展示与维护入口

### 5. 数据库当前没有部门字典表与用户部门字段

当前 `users` 表只包含账号、角色、状态、首次登录、安全问题等字段，没有：

1. 一级部门字段
2. 二级部门字段
3. 部门字典表

因此本次设计必须新增字典表，并扩展 `users` 表保存部门引用。

### 6. 实际 live MySQL 表结构

本次设计已核对当前本地用户态 MySQL 的实际 schema，数据库名为 `agentcode`。当前核心表包括：

1. `users`
2. `user_security_questions`
3. `password_history`
4. `conversations`
5. `conversation_messages`
6. `conversation_files`
7. `conversation_json_outbox`
8. `quota_configs`
9. `user_quota_usage`
10. `user_quota_overrides`

其中与本需求直接相关的真实表结构如下。

#### 6.1 live `users` 表

当前 `users` 表真实字段为：

1. `id bigint auto_increment`
2. `username varchar(64) not null`
3. `password_hash varchar(255) not null`
4. `role enum('user','admin') not null default 'user'`
5. `user_type tinyint unsigned not null default 3 comment '1=admin,2=super,3=common'`
6. `status enum('active','disabled') not null default 'active'`
7. `is_first_login tinyint(1) not null default 0`
8. `must_set_security_questions tinyint(1) not null default 0`
9. `failed_login_attempts int unsigned not null default 0`
10. `locked_until datetime null`
11. `created_at timestamp not null default current_timestamp`
12. `updated_at timestamp not null default current_timestamp on update current_timestamp`
13. `password_updated_at datetime null`

当前约束和索引：

1. 主键：`PRIMARY KEY (id)`
2. 唯一键：`UNIQUE KEY username (username)`
3. 普通索引：`idx_users_status (status)`
4. 普通索引：`idx_users_role (role)`

这说明：

1. live 库已经存在 `user_type`
2. `super` 不是 `role` 枚举值，而是通过 `user_type=2` 表达
3. `is_first_login` 和 `must_set_security_questions` 的 live 默认值都是 `0`，当前业务依赖 service 显式写值，而不是依赖表默认值
4. 目前确实没有任何部门字段

#### 6.2 live `user_security_questions` 表

当前 `user_security_questions` 表真实字段为：

1. `id bigint auto_increment`
2. `user_id bigint not null`
3. `question varchar(255) not null`
4. `answer_hash varchar(255) not null`
5. `sort_order tinyint unsigned not null`
6. `created_at timestamp not null default current_timestamp`
7. `updated_at timestamp not null default current_timestamp on update current_timestamp`

当前约束和索引：

1. 主键：`PRIMARY KEY (id)`
2. 唯一键：`uk_user_security_questions_user_sort (user_id, sort_order)`
3. 普通索引：`idx_user_security_questions_user (user_id)`
4. 外键：`fk_user_security_questions_user -> users(id) on delete cascade`

#### 6.3 live `password_history` 表

当前 `password_history` 表真实字段为：

1. `id bigint auto_increment`
2. `user_id bigint not null`
3. `password_hash varchar(255) not null`
4. `created_at timestamp not null default current_timestamp`

当前约束和索引：

1. 主键：`PRIMARY KEY (id)`
2. 普通索引：`idx_password_history_user_created (user_id, created_at desc)`
3. 外键：`fk_password_history_user -> users(id) on delete cascade`

#### 6.4 对本次设计的直接影响

真实 schema 和仓库中的理想迁移脚本并不完全一致，因此部门需求必须以 live 表结构为准：

1. 不能假设 `users.role` 可扩成 `super`
2. 不能假设 `is_first_login` / `must_set_security_questions` 的默认值已经适合首次流程
3. 本次需要做的是在现有 `users` 上 `ALTER TABLE` 增列，而不是按文档化理想结构重建 `users`
4. 新设计不应顺手调整 `users` 现有字段类型或默认值，避免把部门需求和认证表重构混成一件事

## Goals

1. 新增管理员可维护的两级部门字典。
2. 让管理员创建用户时可以指定部门，也可以留空。
3. 让管理员批量导入用户时可以按部门名称匹配字典，也允许整行部门留空。
4. 让管理员可以后续修改任意用户的部门。
5. 让普通用户在个人中心补全部门，且只能从字典中搜索选择。
6. 当用户部门为空时，必须被强制拦到个人中心，补全后才能进入系统其它页面。
7. 让前端在选择部门时具备搜索能力，降低部门较多时的选择成本。
8. 保持现有密码修改和安全问题强制流程不被破坏。

## Non-Goals

1. 不引入按部门的权限隔离。
2. 不引入部门编码体系；本轮导入按名称匹配。
3. 不支持管理员删除部门。
4. 不要求部门维护界面支持拖拽树结构编辑。
5. 不支持导入时自动创建不存在的部门。

## Options Considered

### Option A: 直接在 auth/admin_users 中硬塞部门字段与接口

优点：

1. 改动面最小
2. 上手最快

缺点：

1. 部门字典和用户领域继续耦合
2. 后续管理员维护入口会把 `admin_users` 挤得更重
3. 搜索、字典查询、状态过滤等能力不易独立演进

结论：

可做，但不是推荐方案。

### Option B: 新增独立 departments 模块，auth/admin_users 只引用字典

优点：

1. 职责清晰
2. 字典维护、用户绑定、首次强制流程三层边界明确
3. 后续扩展搜索、筛选、停用规则更稳
4. 与当前 `public-service` 模块化结构一致

缺点：

1. 改动点比 Option A 多
2. 需要同步更新 gateway 代理面与前端服务层

结论：

推荐方案。

### Option C: 前端写死部门树，后端只存字符串

优点：

1. 实现最省事

缺点：

1. 管理员无法维护
2. 与“管理员界面要提供维护入口”的需求直接冲突
3. 无法保证批量导入、单建用户、个人中心选择使用的是同一份真值

结论：

不可接受。

## Recommended Design

### 1. 模块边界

新增独立模块 `public-service/backend/app/modules/departments`，职责只包括部门字典本身：

1. 一级部门管理
2. 二级部门管理
3. 部门树查询
4. 部门搜索
5. 启用/停用规则

其余模块的职责：

1. `auth` 负责返回当前用户的部门信息、部门完整性状态、以及用户自助补全部门
2. `admin_users` 负责管理员在用户维度上设置或清空部门，以及导入时按名称匹配部门
3. `gateway` 只负责把新接口代理到 `public-service`

### 2. 数据模型

#### 2.1 一级部门表

建议新增表：`primary_departments`

字段：

1. `id`
2. `name`
3. `status`
4. `created_at`
5. `updated_at`

约束：

1. `name` 全局唯一
2. `status` 只允许 `active` / `disabled`

#### 2.2 二级部门表

建议新增表：`secondary_departments`

字段：

1. `id`
2. `primary_department_id`
3. `name`
4. `status`
5. `created_at`
6. `updated_at`

约束：

1. `primary_department_id + name` 组合唯一
2. `status` 只允许 `active` / `disabled`
3. 外键指向 `primary_departments(id)`

#### 2.3 用户表扩展

扩展 `users` 表：

1. `primary_department_id BIGINT NULL`
2. `secondary_department_id BIGINT NULL`

约束：

1. 两个字段都允许为空，支持管理员创建或导入时留空
2. 外键分别关联到一级、二级部门表
3. 数据完整性的业务校验由应用层保证：二级必须属于一级
4. 不改变现有 `role enum('user','admin')` 与 `user_type` 并存的 live 编码方式

#### 2.4 状态计算

不新增 `must_set_department` 这类布尔字段，统一用动态计算：

1. 当 `primary_department_id` 为空时，`require_department_setup = true`
2. 当 `secondary_department_id` 为空时，`require_department_setup = true`
3. 当二级部门不属于一级部门时，`require_department_setup = true`
4. 当两者都存在且关系合法时，`require_department_setup = false`

这样可以避免和真实字段状态漂移。

### 3. 登录与鉴权返回

后端 `login` 与 `GET /api/auth/me` 都要扩展当前用户信息载荷，至少返回：

1. `primary_department_id`
2. `primary_department_name`
3. `secondary_department_id`
4. `secondary_department_name`
5. `require_department_setup`

部门字段位置在本设计中固定如下，不允许再留两种可选形态：

1. `POST /api/auth/login` 继续沿用当前登录契约：
   - `data.user` 是用户载荷
   - 部门字段并入 `data.user`
   - `data.require_department_setup` 同步返回
   - 顶层 `require_department_setup` 也同步返回，和当前 `require_password_change`、`require_security_questions_setup` 的模式保持一致
2. `GET /api/auth/me` 继续沿用当前 `data` 即用户载荷的契约：
   - 部门字段直接并入 `data`
   - `require_department_setup` 直接并入 `data`
3. 不新增 `data.user_department` 这样的嵌套对象，避免 frontend/backend 在两种结构之间分叉实现

也就是说，原则是：

1. 不改变两个接口现有的大体外形
2. 部门字段总是并入“该接口当前已经存在的用户 payload”
3. 前端本地缓存用户信息时，也直接缓存这些平铺后的部门字段与 `require_department_setup`

### 4. 用户自助补全部门

新增接口：

1. `GET /api/auth/departments/tree`
2. `PUT /api/auth/department`

行为定义：

1. `tree` 返回所有可选的启用部门树，用于页面初始化和无搜索时展示
2. 前端在拿到 `tree` 后本地完成搜索过滤；如果当前一级部门已选，则二级搜索只在当前一级下过滤
3. `PUT /api/auth/department` 只允许当前登录用户修改自己的部门

保存规则：

1. 一级和二级必须同时提交
2. 二级必须属于一级
3. 两个部门都必须是 `active`
4. 保存成功后，当前用户的 `require_department_setup` 立即变为 `false`

### 5. 管理员维护部门字典

管理员界面新增“部门管理”入口，建议与当前“用户管理”放在同一页面内，用页签或分区切换，避免再加一套路由复杂度。

新增管理员接口：

1. `GET /api/admin/departments/tree`
2. `POST /api/admin/departments/primary`
3. `PUT /api/admin/departments/primary/{primary_id}`
4. `PUT /api/admin/departments/primary/{primary_id}/status`
5. `POST /api/admin/departments/secondary`
6. `PUT /api/admin/departments/secondary/{secondary_id}`
7. `PUT /api/admin/departments/secondary/{secondary_id}/status`

管理员界面能力包括：

1. 查看一级部门列表
2. 查看某个一级下的二级部门
3. 本地搜索一级或二级部门
4. 新增一级部门
5. 新增二级部门
6. 修改一级/二级名称
7. 启用/停用一级/二级部门

停用规则：

1. 一级部门和二级部门都保留各自独立的持久化 `status` 字段
2. 停用一级部门时，不对其下二级部门做批量持久化改写，不把子级 `status` 自动改成 `disabled`
3. 二级部门的“可被选择”状态按有效状态计算：`primary.status == active` 且 `secondary.status == active` 时才可选
4. 当一级部门为 `disabled` 时，其下所有二级部门即使自身存储状态仍为 `active`，有效状态也视为不可选
5. 当一级部门重新启用时，子级是否恢复可选，取决于子级自身存储状态；也就是说，之前保持 `active` 的子级会自动恢复可选，存储为 `disabled` 的子级仍保持不可选
6. 已经绑定到停用部门的用户关系保留，只是不允许再被新选择
7. 管理端和用户端选择器默认只展示当前有效可选项；若某用户已绑定停用部门，则详情页要能显示“已停用”状态

### 6. 管理员按用户维护部门

扩展管理员用户能力：

1. `POST /api/admin/users` 支持传部门
2. 新增 `PUT /api/admin/users/{user_id}/department`
3. `GET /api/admin/users` 列表返回当前用户部门摘要

单建用户建议使用部门 ID，而不是名称：

1. `primary_department_id`
2. `secondary_department_id`

规则：

1. 两个字段都不传，允许
2. 只传一个，拒绝
3. 两个都传时，必须是启用项且关系合法
4. 管理员可以把用户部门清空；一旦清空，该用户后续必须被强制补全

用户列表建议新增一列：

1. `部门`

显示格式建议为：

1. `计算机学院 / 软件工程系`
2. 若为空则显示 `未填写`
3. 若已绑定但部门已停用，则显示 `计算机学院 / 软件工程系（已停用）`

### 7. 批量导入

导入模板扩展为五列：

1. `username`
2. `password`
3. `user_type`
4. `primary_department_name`
5. `secondary_department_name`

示例：

1. `zhangsan,Pass123!,common,计算机学院,软件工程系`
2. `lisi,Pass456!,super,化学学院,材料系`
3. `wangwu,Pass789!,common,,`

导入规则：

1. 两个部门列同时为空，允许导入成功
2. 只填一级或只填二级，当前行失败
3. 一级名称不存在，失败
4. 二级名称不存在，失败
5. 二级不属于一级，失败
6. 任一部门为停用状态，失败
7. 部门名称匹配建议默认按去除首尾空格后的精确匹配，不做模糊匹配

导入结果明细继续沿用现有格式，但错误原因要明确区分：

1. `一级部门不存在`
2. `二级部门不存在`
3. `二级部门不属于当前一级部门`
4. `部门已停用`
5. `部门信息必须同时填写一级和二级`

### 8. 前端搜索选择交互

用户要求在选择部门时支持搜索，因此选择器不能只是普通原生 `select`。

推荐交互：

1. 一级部门：可搜索下拉
2. 二级部门：在一级选定后启用，且只在当前一级下搜索
3. 搜索匹配项显示完整路径，方便确认

搜索行为建议：

1. 输入一级名可命中一级
2. 输入二级名可命中具体二级
3. 当未选一级时，搜索结果展示为 `一级 / 二级`
4. 当已选一级时，二级下拉只展示当前一级下的匹配项

实现方式推荐：

1. 页面初始化时加载启用部门树
2. 前端本地搜索过滤

原因：

1. 典型部门数量不会太大
2. 选择器的实时搜索体验更稳定
3. 可以减少输入时的后端往返

### 9. 个人中心强制流程扩展

`/profile` 页面新增“部门信息”卡片，和现有密码、安全问题并列。

强制流程规则：

1. `is_first_login = true` 时，仍然强制改密码
2. `require_security_questions_setup = true` 时，仍然强制设置安全问题
3. `require_department_setup = true` 时，新增强制补全部门

路由守卫和登录页都要纳入第三个标记：

1. 登录响应若返回 `require_department_setup=true`，登录后跳 `/profile`
2. 路由守卫在每次 `getMe()` 后检查该标记
3. 本地缓存用户信息时同步持久化该标记

页面行为：

1. 强制时自动展开部门表单
2. 未完成时不允许通过 `/`、`/admin` 等其它页面
3. 当密码、安全问题、部门三项要求全部完成后，才允许正常跳转离开

### 10. 错误处理与兼容

后端错误语义需要明确：

1. `PRIMARY_DEPARTMENT_NOT_FOUND`
2. `SECONDARY_DEPARTMENT_NOT_FOUND`
3. `DEPARTMENT_RELATION_INVALID`
4. `DEPARTMENT_DISABLED`
5. `DEPARTMENT_REQUIRED`

前端错误提示应直接使用后端明确文案，不要把部门错误混进现有“获取用户信息失败”这种泛化提示。

兼容策略：

1. 已有老用户没有部门数据时，系统会在下次登录后强制补全
2. 已有老用户如果已经手工回填部门字段，则不触发
3. 旧版前端如果暂未部署，新字段只会被忽略，不影响登录基本成功；真正的功能生效以新前端部署为准

## API Surface

### 1. Auth

保留现有接口，并新增或扩展：

1. `POST /api/auth/login`
2. `GET /api/auth/me`
3. `GET /api/auth/departments/tree`
4. `PUT /api/auth/department`

### 2. Admin Users

扩展现有接口：

1. `GET /api/admin/users`
2. `POST /api/admin/users`
3. `PUT /api/admin/users/{user_id}/department`
4. `POST /api/admin/users/batch-import`
5. `GET /api/admin/users/import-template`

### 3. Departments Admin

新增接口：

1. `GET /api/admin/departments/tree`
2. `POST /api/admin/departments/primary`
3. `PUT /api/admin/departments/primary/{primary_id}`
4. `PUT /api/admin/departments/primary/{primary_id}/status`
5. `POST /api/admin/departments/secondary`
6. `PUT /api/admin/departments/secondary/{secondary_id}`
7. `PUT /api/admin/departments/secondary/{secondary_id}/status`

## Data Flow

### 1. 管理员单建用户

1. 管理员打开创建用户弹窗
2. 前端加载启用部门树
3. 管理员可搜索并选择一级/二级，或者保持两者都为空
4. 提交到 `POST /api/admin/users`
5. 后端校验部门关系并创建用户
6. 若部门为空，该用户后续登录将得到 `require_department_setup=true`

### 2. 管理员批量导入

1. 管理员下载模板
2. 以名称填写一级/二级部门
3. 上传到 `POST /api/admin/users/batch-import`
4. 后端逐行匹配字典并导入
5. 部门双空则成功，单空或无效关系则失败

### 3. 用户首次登录补全部门

1. 用户登录
2. `login` 返回 `require_department_setup`
3. 前端跳转 `/profile`
4. 用户进入部门信息卡片，搜索并选择一级/二级
5. 提交 `PUT /api/auth/department`
6. 后端保存成功后，`require_department_setup=false`
7. 路由守卫允许进入其它页面

### 4. 管理员后续清空用户部门

1. 管理员在用户管理里编辑部门
2. 将一级和二级都清空后提交
3. 后端保存空值
4. 该用户下次 `me` 或重新登录时得到 `require_department_setup=true`
5. 被强制拦回 `/profile`

## Migration Plan

数据库迁移至少包括：

1. 基于 live `agentcode.users` 做增量迁移，而不是重建 `users`
2. 新建 `primary_departments`
3. 新建 `secondary_departments`
4. 给 `users` 增加 `primary_department_id`
5. 给 `users` 增加 `secondary_department_id`
6. 增加必要索引和外键

建议的迁移原则：

1. 不改动 `users.role` 现有枚举定义
2. 不改动 `users.user_type` 现有语义
3. 不顺手改 `is_first_login`、`must_set_security_questions` 的默认值
4. 新增部门列时保持可空，避免对现有用户数据做高风险回填

应用迁移至少包括：

1. `public-service` 注册新模块路由
2. `gateway` 增加新接口代理与路由表
3. `auth` 扩展用户 payload 与部门更新能力
4. `admin_users` 扩展单建、列表、用户部门修改、导入模板、导入校验
5. 前端新增部门管理入口与用户部门选择器
6. 登录页和路由守卫接入 `require_department_setup`

## Testing

后端测试至少覆盖：

1. `login` 响应暴露 `require_department_setup`
2. `me` 响应暴露部门信息
3. 用户自助保存部门成功与失败路径
4. 管理员创建用户时部门为空/完整/半空/非法关系
5. 管理员修改用户部门成功与清空路径
6. 部门字典新增、修改、启停用
7. 停用部门后不可再绑定
8. 批量导入按名称匹配成功与失败路径
9. gateway 代理新接口的 contract test

前端验证至少覆盖：

1. 登录后因 `require_department_setup` 跳转 `/profile`
2. 路由守卫在缓存与实时 `getMe()` 两条路径都能拦截
3. 个人中心部门选择器搜索行为
4. 一级变化后二级候选被正确重置
5. 管理员创建用户时可搜索选择部门
6. 管理员编辑用户部门时可清空并保存
7. 管理员部门管理页的新增、修改、启停用交互
8. `cd frontend-vue && npm run build`

## Risks

1. 如果只在前端实现搜索、不在后端做严格关系校验，导入和接口调用仍可能写入非法组合，因此后端关系校验是必需的。
2. 如果把 `require_department_setup` 存成单独布尔字段，后续管理员清空或修正部门时容易出现状态漂移，因此本设计刻意采用动态计算。
3. 如果停用部门后直接把老用户字段清空，会造成大规模强制补全；本轮需求没有要求这样做，因此默认保留旧绑定。
4. 如果管理员界面继续平铺在单页中而不做分区，页面复杂度会快速上升；建议至少引入页签。

## Assumptions

1. 部门数量处于“可前端一次性加载并本地搜索”的规模。
2. 停用部门不会自动触发老用户重新选择，只有空部门才会触发强制补全。
3. 导入按名称精确匹配即可，不需要同义词、别名、拼音、编码映射。

如果上述假设中第 2 条未来需要改成“停用即强制重填”，可以在实施前调整规则，但那会改变本设计当前定义的用户影响范围。
