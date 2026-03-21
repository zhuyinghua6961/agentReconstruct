# auth 前端会话流与兼容层备注

对应代码：
- `frontend-vue/src/api/auth.js`
- `frontend-vue/src/services/auth.js`
- `frontend-vue/src/features/auth/composables/useAuthSession.js`
- `frontend-vue/src/features/auth/components/AuthBar.vue`
- `frontend-vue/src/views/Login.vue`
- `frontend-vue/src/views/ForgotPassword.vue`
- `frontend-vue/src/views/UserProfile.vue`
- `frontend-vue/src/router/index.js`

## 1. 前端实际上有两套 auth 调用面

第一套，较新的轻量封装：

- `src/api/auth.js`
- `src/features/auth/composables/useAuthSession.js`
- `src/features/auth/components/AuthBar.vue`

第二套，页面级历史封装：

- `src/services/auth.js`
- `src/views/Login.vue`
- `src/views/ForgotPassword.vue`
- `src/views/UserProfile.vue`
- `src/router/index.js`

这不是简单重复代码，而是两个不同阶段的前端接入面并存。

## 2. 两套本地存储 key 不一致

`useAuthSession.js` 使用：

- `agentcode.auth.token.v1`
- `agentcode.auth.user.v1`

而页面和路由守卫使用：

- `token`
- `user`

这意味着当前前端并不存在单一全局会话源。

直接后果：

- `useAuthSession()` 登录成功后写的新 key，页面路由守卫未必读取
- 页面登录页写的旧 key，`useAuthSession()` 未必立即消费

所以 auth 前端层不是完全统一状态管理，而是并行兼容。

## 3. API 封装风格也不一致

`src/api/auth.js`：

- 依赖 `getJson/postJson/putJson`
- 更像公共 HTTP 客户端上的资源封装

`src/services/auth.js`：

- 直接写 `fetch`
- 自己做 token header
- 自己做错误处理和 401/403 清理

这会带来：

- 错误处理逻辑分散
- token 获取来源分散
- 返回 payload 使用方式也不一致

## 4. 路由守卫是当前页面体系的核心 auth 编排器

`router.beforeEach()` 负责：

- 认证页面跳转
- admin 权限页拦截
- token 有效性校验
- 首次登录强制改密
- 强制设置安全问题
- admin 首页重定向

其中 token 校验还有缓存：

- `tokenValidated`
- `lastValidationTime`
- `VALIDATION_CACHE_TIME = 5 分钟`

也就是：

- 不是每次路由跳转都请求 `/me`
- 但缓存分支也会继续检查本地 `user.is_first_login`
- 和 `user.require_security_questions_setup`

## 5. 登录页承担了比“登录”更多的职责

`Login.vue` 在登录成功后除了保存 token，还会处理：

- 首次登录提示
- 强制安全流程跳转
- 密码过期 warning
- 账户锁定倒计时显示

它依赖的返回字段既有：

- `result.data.is_first_login`
- `result.data.has_security_questions`

也有顶层：

- `result.require_password_change`
- `result.require_security_questions_setup`
- `result.warning`

所以后端 auth 返回体目前已经和页面行为强绑定。

## 6. 个人中心页复制了后端密码规则

`UserProfile.vue` 在提交改密前先在前端做了一轮规则校验：

- admin 至少 12 位且 4 类全有
- 普通用户至少 8 位且 4 类中满足 3 类

`ForgotPassword.vue` 也复制了普通用户密码规则。

这说明前端并没有只依赖后端错误返回，而是把核心密码规则又写了一份。

风险点很明确：

- 一旦后端规则变更，前端两处都要同步

## 7. 忘记密码页面依赖 auth 的顺序型问题数组契约

`ForgotPassword.vue` 的步骤是：

1. 输入用户名
2. 请求 `initiatePasswordReset`
3. 展示返回的问题文本数组
4. 按显示顺序收集 `answers[]`
5. 连同 `new_password` 提交

这里前端没有为问题项保留显式 `id`。

因此它完全依赖后端“返回顺序就是校验顺序”的契约。

## 8. logout 在前端只是本地清理

`services/auth.js` 的 `logout()` 明确写了：

- 后端没有 dedicated logout endpoint
- 这里只保留 API 形状

页面实际动作是：

- 清 `localStorage`
- 跳转 `/login`

这再次说明前端当前会话撤销不是服务端事件。

## 9. 历史页面体系和新 composable 体系没有完全接通

`AuthBar.vue` + `useAuthSession.js` 看起来是更组件化的新接法。

但路由、登录页、个人中心、找回密码等主路径仍在使用：

- `services/auth.js`
- 旧 key
- 页面内直接 `window.location.href`

所以现在的 auth 前端形态更接近：

- “新旧两套能力同时存活”

而不是：

- “一套公共 auth SDK 被全站统一复用”

## 10. 这部分对公共能力拆分的启示

如果以后要把 auth 抽成独立公共服务，前端至少要同时梳理三层东西：

- 接口契约
- 本地存储 key
- 路由守卫与强制安全流程

否则只迁接口，不迁前端状态机，系统行为仍会分裂。
