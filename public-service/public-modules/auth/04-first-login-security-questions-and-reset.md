# auth 首次登录、安全问题与找回密码流程

对应代码：
- `backend/app/modules/auth/service.py`
- `backend/app/modules/auth/repository.py`
- `backend/app/modules/auth/api.py`
- `frontend-vue/src/views/Login.vue`
- `frontend-vue/src/views/UserProfile.vue`
- `frontend-vue/src/views/ForgotPassword.vue`
- `frontend-vue/src/router/index.js`

## 1. 注册后不是普通“已登录”，而是进入强制安全初始化

`register()` 的用户创建逻辑会主动设置：

- `role = user`
- `user_type = 3`
- `is_first_login = True`
- `must_set_security_questions = True`

同时返回：

- `data.is_first_login = True`
- `data.has_security_questions = False`
- `data.require_security_questions_setup = True`
- 顶层 `require_password_change = True`
- 顶层 `require_security_questions_setup = True`

所以注册成功的真实语义不是“拿到 token 直接使用系统”，而是：

1. 自动登录
2. 进入首次安全配置阶段
3. 至少完成改密
4. 最好补齐安全问题

## 2. 首次登录强制信号分成两层

当前有两种相关信号：

第一层，数据库状态：

- `is_first_login`
- `must_set_security_questions`

第二层，接口返回控制位：

- `require_password_change`
- `require_security_questions_setup`

其中：

- `require_password_change` 是顶层返回字段，只在登录/注册成功时显式给前端
- `require_security_questions_setup` 既可能在顶层出现，也会在 `/me` 的 `data` 里出现

这解释了为什么前端在不同页面里会混用：

- `result.require_password_change`
- `result.require_security_questions_setup`
- `result.data.is_first_login`
- `result.data.require_security_questions_setup`

## 3. `/me` 会把安全问题强制状态重新计算一遍

`_build_user_payload()` 并不是直接把 `must_set_security_questions` 原样回传，而是计算：

- `require_security_questions_setup = must_set_security_questions and not has_security_questions`

这条逻辑很关键。

它意味着：

- 就算数据库里 `must_set_security_questions` 还没清掉
- 只要已经查到真实存在的安全问题记录
- `/me` 仍会告诉前端“不需要再强制设置”

这是一个典型的“以真实附属数据修正主表标志位”的容错设计。

## 4. set_security_questions() 是替换，不是增量编辑

限制：

- 数量必须在 `1..3`
- 任一问题或答案为空都报 `VALIDATION_ERROR`

保存流程：

1. 逐项读取 `question` 和 `answer`
2. 对答案做 `_normalize_answer()`
3. 用 PBKDF2 hash 存储答案
4. `replace_security_questions()` 先删旧再插新
5. `set_security_setup_required(..., required=False)`

所以它的真实语义是：

- 全量替换当前问题集

而不是：

- 新增一个问题
- 单独修改第 N 个问题

## 5. 安全问题答案不是原样比较

答案比较前会做归一化：

- `strip()`
- 全部转小写
- 合并连续空白为单空格

例如这两种输入会被视为相同：

- `Beijing`
- `  beijing  `

这也是前端 `ForgotPassword.vue` 提示“答案不区分大小写”的真实代码依据。

## 6. 忘记密码是两阶段，而不是发验证码式流程

阶段一：

- `POST /auth/forgot-password/initiate`
- 输入用户名
- 返回：
  - 是否设置过安全问题
  - 问题文本列表

阶段二：

- `POST /auth/forgot-password/verify`
- 传：
  - `username`
  - `answers`
  - `new_password`

后端会：

1. 再查用户
2. 再查问题列表
3. 校验回答数量是否足够
4. 逐题比对答案 hash
5. 校验新密码强度
6. 校验当前密码和历史密码复用
7. 更新密码并清安全状态

这说明忘记密码能力完全建立在：

- 用户名可查
- 安全问题表可读
- 安全问题答案哈希可验证

之上，没有短信、邮箱、验证码中间层。

## 7. 问题顺序是契约的一部分

`verify_and_reset_password()` 用：

- `enumerate(question_rows)`

逐项比对 `answers[index]`。

而 repository 取题目时按：

- `sort_order ASC`
- `id ASC`

排序。

因此：

- 前端必须按后端给出的顺序展示
- 提交答案时也必须按同序组成数组

当前 `ForgotPassword.vue` 的实现确实就是按题目列表顺序收集答案。

## 8. 找回密码成功也会解除首次改密标记

`verify_and_reset_password()` 成功后会：

- `mark_first_login_completed()`
- `reset_login_attempts()`

所以如果用户在首次登录前就通过安全问题重置了密码，系统会把“首次口令安全动作”视为已完成。

这会改变后续登录返回：

- 不再强制 `require_password_change`

## 9. 前端强制流分散在登录页、路由守卫、个人中心三处

登录页：

- 登录成功后如果有 `require_password_change` 或 `require_security_questions_setup`
- 先显示提示
- 3 秒后跳到 `/profile?...`

路由守卫：

- 每次认证校验通过后看 `/me` 返回
- 若仍需改密或设置安全问题，则强制跳 `/profile`

个人中心页：

- 读取 query 参数
- 展开改密表单和安全问题表单
- 改密成功后如果仍需设置安全问题则继续留在当前页
- 设题成功后如果不再有强制项则返回首页

所以首登安全流不是一个单页面完成，而是多个前端层级协同完成。

## 10. 这个流程依赖表存在，且会静默退化

如果 `user_security_questions` 表不存在：

- `initiate_password_reset()` 会返回 `has_security_questions = false`
- `verify_and_reset_password()` 会报 `NO_SECURITY_QUESTIONS`
- `set_security_questions()` 可能返回成功但没有实际持久化
- `/me` 中 `has_security_questions` 会一直是 `False`

如果 `must_set_security_questions` 列也不存在：

- 强制设置安全问题标志也无法落库

因此“首次登录必须设置安全问题”这条能力在数据库兼容模式下并不是绝对可靠，而是：

- 列和表齐全时才完整成立

## 11. 当前流程没有管理员兜底重置口令接口

从 auth API 看，找回密码只有：

- 用户自己回答安全问题重置

并没有公开的：

- 管理员强制重置某用户密码

如果要做这件事，只能靠其他用户管理模块，不能把它误记成 auth 自带能力。
