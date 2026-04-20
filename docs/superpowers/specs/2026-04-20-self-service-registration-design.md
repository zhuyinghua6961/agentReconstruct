# Self-Service Registration Design

**Date:** 2026-04-20

## Summary

本设计定义当前系统的“用户自助注册”方案，目标是在现有账号体系上新增一个单独的注册页面，让用户在注册时一次性完成账号创建、部门选择、人员校验绑定、安全问题设置，并在注册成功后自动登录进入系统。

本次设计采用单次完成式注册，核心原则如下：

1. 登录页新增“注册账号”入口，跳转到独立 `/register` 页面。
2. 注册页一次性收集并校验：`用户名`、`密码`、`确认密码`、`三级部门`、`工号`、`姓名`、`校验码`、`1-3 个安全问题`。
3. 密码强度要求与当前非管理员在个人中心修改密码时的规则完全一致。
4. 人员信息继续复用现有人员主档校验逻辑，必须通过 `工号 + 姓名 + 校验码` 绑定。
5. 部门信息继续复用现有三级部门选择与有效性校验逻辑，注册时不得留空。
6. 注册成功后直接签发 token 并自动登录，不再要求补密码、补部门、补人员、补安全问题。
7. 自助注册创建的账号默认是“超级用户”，即 `role='user'` 且 `user_type=2`，不是管理员账号。

本设计不引入新的身份主档表、部门表或安全问题表，优先复用当前已经上线的人员、部门和安全问题基础能力。

## Scope

本设计覆盖：

1. `frontend-vue/src/views/Login.vue`
2. 新增 `frontend-vue/src/views/Register.vue`
3. `frontend-vue/src/router/index.js`
4. `frontend-vue/src/services/auth.js`
5. 复用 `frontend-vue/src/components/DepartmentSelector.vue`
6. `public-service/backend/app/modules/auth/api.py`
7. `public-service/backend/app/modules/auth/schemas.py`
8. `public-service/backend/app/modules/auth/service.py`
9. `public-service/backend/app/modules/auth/repository.py`
10. 复用 `public-service/backend/app/modules/departments/service.py`
11. 复用 `public-service/backend/app/modules/personnel/service.py`
12. 与注册流程直接相关的前端测试、后端测试、构建校验

本设计原则上不要求修改：

1. `gateway/app/routers/public_proxy.py`
2. `gateway/app/services/route_table.py`

原因是当前 gateway 已经代理 `/api/auth/register`，本次只扩展请求体与后端语义，不新增 auth 路由。

本设计不覆盖：

1. 邀请码、注册码、审批制开户
2. 手机号、邮箱验证码、短信验证码
3. 管理员自助注册
4. 人员唯一绑定约束
5. 自由输入部门或自由创建人员
6. 注册后的欢迎页、引导页、营销文案
7. 忘记密码流程重构
8. 当前未挂载的 demo 级认证组件重构；但如果它们继续暴露注册能力，最终实现必须避免留下过时接口契约

## Hard Boundaries

以下边界是强约束：

1. 当前生效链路仍然是 `frontend-vue -> gateway -> public-service`，不在 `highThinkingQA` 的 deprecated auth 路由上实现注册。
2. 注册必须是单次提交完成，不采用“先建账号、再跳个人中心补数据”的两段式流程。
3. 注册页必须要求填写完整三级部门，不允许空部门，也不允许 legacy 两级完成态。
4. 注册页必须要求填写完整人员信息，并复用当前 `工号 + 姓名 + 校验码` 校验规则。
5. 同一人员记录允许绑定多个账号；注册流程不得新增“一人只能注册一个账号”的限制。
6. 注册成功创建的账号默认是超级用户，即 `user_type=2`；`role` 仍然保持现有 `user` / `admin` 两值模型，不引入 `role='super'`。
7. 注册成功后账户必须处于“已完成资料”的状态：`is_first_login=false`、`require_security_questions_setup=false`、`require_department_setup=false`、`require_personnel_setup=false`。
8. 注册成功后必须直接自动登录并进入普通用户主界面 `/`，不能再强制跳 `/profile`。
9. 注册写库必须是原子性的，不能出现“用户创建成功但部门/人员/安全问题只写了一半”的半成品账号。
10. 密码规则必须与当前非管理员在个人中心改密码规则保持一致，不能单独再造一套注册密码规则。

## Current State

### 1. 当前系统已经有轻量注册接口，但能力不完整

当前 `public-service` 已提供：

1. `POST /api/auth/register`
2. 请求体只有 `username` 和 `password`
3. 注册逻辑只创建账号，不写部门、不绑人员、不写安全问题

当前实现位置：

1. [auth/api.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/auth/api.py)
2. [auth/schemas.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/auth/schemas.py)
3. [auth/service.py](/home/cqy/worktrees/highThinking/public-service/backend/app/modules/auth/service.py)

现状注册后的行为是：

1. 创建普通用户 `user_type=3`
2. `is_first_login=true`
3. `must_set_security_questions=true`
4. `personnel_id` 为空
5. 部门为空
6. 登录后仍会被现有 profile 强制补全链路拦截

这与本次“注册即完成资料”的目标相反。

### 2. 当前首次登录/资料补全链路已经存在并且生效

当前非管理员用户会因为以下条件被拦到 `/profile`：

1. `is_first_login`
2. `require_security_questions_setup`
3. `require_department_setup`
4. `require_personnel_setup`

前端拦截位置：

1. 登录页 `frontend-vue/src/views/Login.vue`
2. 路由守卫 `frontend-vue/src/router/index.js`
3. 统一判断逻辑 `frontend-vue/src/router/profileSetup.js`

后端标志位来源：

1. `auth.service._build_user_payload()`
2. `auth.service.login()`
3. `auth.service.get_user_info()`

这意味着本次注册设计不应该新造另一套“注册完成标志”，而是应该让注册结果直接满足现有 profile 完成条件。

### 3. 当前系统已经具备部门、人员、安全问题的独立能力

当前系统已经上线并可复用：

1. 用户自助绑定人员信息
2. 用户自助保存三级部门
3. 用户自助设置 1-3 个安全问题
4. 管理员维护人员主档
5. 管理员维护三级部门字典

因此本次注册的关键不是新增数据模型，而是把这些已有校验与写入逻辑收敛进一个原子化注册入口。

### 4. 当前没有单独的注册页面

当前正式登录页 `frontend-vue/src/views/Login.vue` 只有：

1. 用户名输入
2. 密码输入
3. 登录
4. 忘记密码

没有“注册账号”入口，也没有 `/register` 路由。

仓库里虽有轻量认证组件与 `registerAuth()` 封装，但当前主流程没有挂载它们；本次注册设计应以当前 canonical 登录页和 canonical `auth.js` 服务为准。

### 5. 当前数据表已经足够承载注册所需信息

本次注册不需要新增表：

1. `users` 已承载部门引用、人员绑定引用、首次登录标记、用户类型
2. `user_security_questions` 已承载安全问题
3. `password_history` 已承载密码历史
4. `personnel_records` 已承载人员主档

因此数据库层重点不是 schema 扩张，而是：

1. 扩展注册请求契约
2. 补齐 repository 的原子化写入能力
3. 明确注册成功后的状态值

## Goals

1. 用户可以从登录页进入独立注册页完成开户。
2. 注册时一次性填写账号、部门、人员、安全问题。
3. 注册密码强度与当前非管理员改密码规则一致。
4. 注册时即绑定有效人员和有效三级部门。
5. 注册成功的账号默认是超级用户。
6. 注册成功后自动登录进入 `/`。
7. 注册成功后不再被首次登录或资料补全流程拦截。
8. 注册失败时不产生半成品账号。

## Non-Goals

1. 不开放管理员账号自助注册。
2. 不引入邮件/短信校验。
3. 不增加邀请码逻辑。
4. 不改动人员唯一绑定策略。
5. 不改造个人中心现有资料补全页面，只要求注册完成后不再触发该流程。
6. 不重构现有忘记密码能力。
7. 不在本次注册需求中引入实名审核后台。

## Options Considered

### Option A: 扩展现有 `/api/auth/register` 为单次完成式注册

优点：

1. 复用现有 gateway 代理路径
2. 注册和登录仍然走同一个 auth 模块
3. 最符合“注册成功直接自动登录”的产品目标
4. 不需要额外暴露注册中间态接口

缺点：

1. 需要明显扩展当前注册请求体和 service 语义
2. 需要补一个原子化事务写入路径

结论：

推荐方案。

### Option B: 保持 `/api/auth/register` 简单，只在前端串联注册后补资料

优点：

1. 代码表面改动看起来更小
2. 可以更多复用当前 `/profile` 的表单逻辑

缺点：

1. 和“注册时一次填完”的目标冲突
2. 自动登录后仍会经历强制补全流程，用户体验绕
3. 需要在前端维护更多注册中间态

结论：

不采用。

### Option C: 新增单独的 `/api/auth/register-complete`

优点：

1. 不破坏现有轻量 `/register` 契约
2. 新旧能力边界更清晰

缺点：

1. 维护两套注册入口意义不大
2. gateway 和前后端都会多一条 auth surface
3. 当前仓库内已经没有真正使用中的旧注册页，保留双轨收益很低

结论：

暂不采用，优先直接扩展现有 `/api/auth/register`。

## Recommended Design

### 1. Frontend User Flow

#### 1.1 登录页入口

登录页新增“注册账号”入口：

1. 放在当前登录表单下方，与“忘记密码”同层级展示
2. 点击后跳转到 `/register`
3. 不弹 modal，不在登录页内内嵌展开注册表单

#### 1.2 注册页结构

注册页为独立页面，建议分成四个逻辑区块：

1. 账号信息
2. 部门信息
3. 人员信息
4. 安全问题

建议字段如下：

1. 用户名
2. 密码
3. 确认密码
4. 一级、二级、三级部门选择
5. 工号
6. 姓名
7. 校验码
8. 1-3 个安全问题及答案

页面交互要求：

1. 注册按钮提交前做基础前端校验
2. 提交中禁用重复点击
3. 提供“返回登录”入口
4. 密码区块显示与当前非管理员改密码一致的规则提示
5. 部门区块复用当前可搜索的 `DepartmentSelector`
6. 安全问题区块复用当前个人中心的 1-3 个问题模型
7. 安全问题首版复用当前个人中心的预置问题列表选择 UI，不新增“自定义问题文本输入”能力；答案仍为自由输入

#### 1.3 注册成功后的行为

注册成功后：

1. 直接保存 token 和 user 信息到当前认证存储
2. 不显示“首次登录需补全资料”的提示
3. 直接跳转 `/`
4. 路由守卫应将其视作完整用户，不再跳 `/profile`

### 2. Register Request Contract

`POST /api/auth/register` 的请求体扩展为完整结构：

1. `username: string`
2. `password: string`
3. `primary_department_id: int`
4. `secondary_department_id: int`
5. `tertiary_department_id: int`
6. `employee_no: string`
7. `full_name: string`
8. `verification_code: string`
9. `security_questions: [{ question: string, answer: string }]`

说明：

1. `confirm_password` 仅用于前端校验，不下发给后端
2. `security_questions` 数量必须在 1-3 之间
3. 注册接口不接受空部门、空人员信息
4. `/api/v1/auth/register` 与 `/api/auth/register` 必须保持相同请求体和相同行为，不能出现 v1 与非 v1 两套不同注册契约

返回体建议与当前登录成功结构对齐，避免前端维护两套成功态解析：

1. `success: true`
2. `message: "register_success"`
3. `data.token`
4. `data.user`
5. `data.is_first_login: false`
6. `data.has_security_questions: true`
7. `data.require_security_questions_setup: false`
8. `data.require_department_setup: false`
9. `data.require_personnel_setup: false`

同时外层不返回：

1. `require_password_change`
2. `require_security_questions_setup=true`
3. `require_department_setup=true`
4. `require_personnel_setup=true`

### 3. Backend Validation Rules

注册提交时，后端应按以下顺序校验：

1. 用户名基础合法性与唯一性
2. 密码强度，完全复用当前非管理员改密码规则
3. 部门选择合法性，要求三级完整、关系正确、状态为 active
4. 人员信息合法性，必须通过 `工号 + 姓名 + 校验码` 校验，且人员状态为 active
5. 安全问题列表合法性，数量 1-3，每项都必须有问题和答案

密码规则必须复用当前 `AuthService._validate_password_strength(password, role='user')` 对非管理员的语义：

1. 长度至少 8 位
2. 数字 / 小写 / 大写 / 符号四类中至少三类

人员绑定规则必须复用当前 `PersonnelService.verify_personnel_identity()`：

1. 工号不存在则失败
2. 姓名不匹配则失败
3. 校验码不匹配则失败
4. 人员已停用则失败

部门规则必须复用当前 `DepartmentService.validate_department_selection()`，但参数固定为：

1. `require_active=True`
2. `allow_empty=False`
3. `allow_legacy_two_level=False`

### 4. Atomic Persistence

注册写库必须在一个事务中完成，最少包含以下步骤：

1. 创建用户
2. 写入部门引用
3. 写入人员绑定
4. 写入密码历史
5. 写入安全问题

关键状态要求：

1. `role='user'`
2. `user_type=2`
3. `status='active'`
4. `is_first_login=false`
5. `must_set_security_questions=false`
6. `primary_department_id / secondary_department_id / tertiary_department_id` 写入注册提交的有效部门
7. `personnel_id` 写入校验通过的人员记录 id

实现边界上，不应继续沿用当前“每个 repository 方法单独开连接单独提交”的方式拼接注册，因为那样无法满足原子性要求。规划和实现必须提供一个明确的事务写入方案，形态可以是：

1. 在 `auth.repository` 新增专用的事务化注册写入方法
2. 或者新增显式事务上下文，在一个连接内串行完成所有步骤

但无论采用哪种代码形式，结果必须保证“任何一步失败，整个注册不落库”。

### 5. User Payload After Registration

注册成功返回的用户对象应当已经是“完整可用态”，至少包含：

1. `id`
2. `username`
3. `role='user'`
4. `user_type=2`
5. `primary_department_id`
6. `primary_department_name`
7. `secondary_department_id`
8. `secondary_department_name`
9. `tertiary_department_id`
10. `tertiary_department_name`
11. `department_completion_level='complete'`
12. `require_department_setup=false`
13. `personnel_id`
14. `employee_no`
15. `full_name`
16. `personnel_binding_status='bound_active'`
17. `require_personnel_setup=false`
18. `has_security_questions=true`
19. `require_security_questions_setup=false`
20. `is_first_login=false`

这保证：

1. 前端可以直接复用当前登录成功后的 token/user 存储逻辑
2. 路由守卫不会再把新注册用户拦去 `/profile`

### 6. Frontend Route Behavior

新增 `/register` 路由，属于游客页，不要求登录。

路由行为建议：

1. 未登录访问 `/register`，正常显示注册页
2. 已登录且资料完整，访问 `/register` 时直接跳转到当前默认落点：
   1. 管理员去 `/admin`
   2. 非管理员去 `/`
3. 已登录但仍有强制补全任务，访问 `/register` 时仍跳 `/profile`

这样可以保证 `/login` 与 `/register` 的 guest-only 行为一致，不会让已登录用户误入注册页。

### 7. Error Handling

注册失败时，后端返回第一条明确业务错误，不做多错误聚合。

至少需要覆盖的失败类型：

1. 用户名为空或格式不合法
2. 用户名已存在
3. 密码不满足复杂度要求
4. 部门为空
5. 部门关系错误
6. 部门已停用
7. 工号/姓名/校验码校验失败
8. 人员已停用
9. 安全问题数量不在 1-3
10. 安全问题缺题目或缺答案
11. 数据库写入失败

前端处理原则：

1. 表单顶部展示通用错误
2. 可以按区块补充局部提示，但不要求首版做后端字段级错误映射
3. 提交失败后保留用户已输入内容，避免整表单清空

### 8. Impact on Existing Forced Setup Flow

本次注册不会删除当前的首次登录/资料补全模型，而是让“注册成功用户”直接满足该模型的完成条件。

保留不变的现有场景：

1. 管理员手工创建的账号仍然可以继续走首次登录改密码/补资料流程
2. 已有老账号如果部门为空、人员未绑定、人员停用，仍然由当前 `/profile` 流程兜底
3. 人员后续被停用时，已注册账号也仍然会被现有逻辑强制拦截重新绑定

这意味着本次注册是对“新开户路径”的增强，而不是替换现有 profile 补全机制。

## Testing Strategy

后端至少需要覆盖：

1. 注册成功创建超级用户并返回自动登录 token
2. 注册成功后 `is_first_login=false`
3. 注册成功后 `has_security_questions=true`
4. 注册成功后 `require_security_questions_setup=false`
5. 注册成功后 `require_department_setup=false`
6. 注册成功后 `require_personnel_setup=false`
7. 注册成功写入正确的三级部门和人员绑定
8. 弱密码注册失败
9. 重名用户名注册失败
10. 非法部门注册失败
11. 已停用部门注册失败
12. 人员校验失败注册失败
13. 已停用人员注册失败
14. 非法安全问题列表注册失败
15. 注册中途写入失败时整体回滚

前端至少需要覆盖：

1. 登录页显示“注册账号”入口
2. `/register` 路由存在
3. 注册页渲染账号/部门/人员/安全问题表单
4. 注册页使用现有部门选择组件
5. 密码确认不一致时前端阻止提交
6. 注册成功后正确写入 token 和 user
7. 注册成功后跳转 `/`
8. 已登录用户访问 `/register` 的重定向行为
9. `npm run build` 通过

## Risks

1. 当前 auth repository 的写入方法是分散的，若实现阶段没有补事务，最容易出现半成品账号。
2. 当前仓库里仍有一套未挂载的 register API 封装，如果不统一处理，容易留下旧签名和新签名并存的死代码。
3. 新注册账号默认升为超级用户，会影响默认权限面；这是明确产品要求，但实现和测试时必须确认不会误入管理员路由。

## Open Decisions Resolved

本次设计已确认以下产品决策：

1. 注册入口放在登录页，文案为“注册账号”
2. 使用独立注册页，不在登录页内展开
3. 注册页内一次性填写安全问题，不走注册后补填
4. 注册成功后自动登录
5. 注册创建的账号默认是超级用户
