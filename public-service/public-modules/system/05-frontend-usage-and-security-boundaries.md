# system 前端使用面与安全边界

对应代码：
- `frontend-vue/src/services/api.js`
- `frontend-vue/src/api/chat.js`
- `backend/app/modules/system/api.py`

## 1. 前端已经把部分 system 接口当成聊天/知识库功能的一部分

`frontend-vue/src/api/chat.js` 直接暴露：

- `getKbInfo()`
- `refreshKb()`
- `clearCache()`

说明在前端认知里，这些接口不是独立“运维后台 API”，而是聊天产品的一部分。

## 2. 也存在另一套页面适配层调用面

`frontend-vue/src/services/api.js` 里也有：

- `getKbInfo()`

所以和 auth/documents 一样，system 相关调用面也并不只有一套。

## 3. 前端当前没有消费 `health` 和 `background_status` 的统一面板代码

至少在当前检索到的前端代码里，直接调用的是：

- `kb_info`
- `refresh_kb`
- `clear_cache`

没有看到：

- 前端统一 health dashboard
- background worker dashboard
- conversation cache debug UI

这意味着 system 的一部分接口更像：

- 预留运维接口

而另一部分已经直接服务产品页面。

## 4. `kb_info`/`refresh_kb`/`clear_cache` 当前默认未鉴权

API 层代码显示：

- 这些接口没有 `require_auth_context`
- 也没有 `require_admin_context`

而前端调用时通常也不会特意强调管理员上下文，只是按当前页面 token 环境请求。

从安全边界看，这意味着：

- 即使前端某些页面没暴露按钮
- 只要接口可达，未登录请求理论上也能直接命中这些操作

这点在整理公共能力边界时必须明确记下。

## 5. `conversation_cache_debug` 的安全边界相对更合理

这条接口：

- 需要登录
- user_id 来自当前 auth context

所以它虽然是 debug 接口，但没有成为“任意读全站 Redis cache”的危险入口。

也正因为这样，它更接近可以长期保留的平台公共调试接口。

## 6. 当前 system 的真正风险不在返回内容，而在操作权限

只读接口例如：

- `/health`
- `/background_status`

暴露范围宽一些，通常还可以讨论。

但动作型接口：

- `/refresh_kb`
- `/clear_cache`

在当前代码里也是开放的。

所以 system 这块最需要未来收敛的不是格式，而是：

- 哪些接口匿名可读
- 哪些接口至少要登录
- 哪些接口必须 admin

## 7. 对公共能力拆分的结论

system 目前可以算公共能力，但更准确地说它是：

- 平台观测能力 + QA 运维开关 的混合入口

因此后续拆服务时，system 不能直接按当前文件粒度整体迁移，最好先按安全级别和子系统归属拆开。
