# quota 依赖注入、precheck 与 finalize

对应代码：
- `backend/app/modules/quota/deps.py`
- `backend/tests/test_quota.py`
- `backend/app/modules/documents/api.py`
- `backend/app/modules/conversation/api.py`
- `backend/app/modules/ask_gateway/api.py`

## 1. 这层才是 quota 作为公共能力的核心

`service.py` 解决的是“怎么算”。

`deps.py` 解决的是：
- 怎么接到业务路由上
- 什么时候抛异常
- 什么时候真正计数

也正是这一层，让 quota 能横切复用。

## 2. precheck_quota()

输入：
- `user_id`
- `quota_type`
- `strict_config`

输出：
- `QuotaGrant`
- 或抛异常
- 或对豁免用户直接返回 `None`

### 2.1 豁免规则

`_is_quota_exempt()` 把这些 user_type 视为免配额：
- `1`
- `2`

也就是：
- admin
- super

这和 uploads 自己那套特例不同，uploads 只免 `2`。

## 3. strict_config 的真实语义

如果 `check_quota()` 返回：
- `config_missing=true`

那么：

### 3.1 非 strict

允许通过。

### 3.2 strict

抛：
- `QuotaConfigMissingError`
- HTTP `503`
- code `QUOTA_CONFIG_MISSING`

这就是为什么：
- documents summarize/translate 会把缺配置当错误
- 其他某些业务会把缺配置视为“未启用限制”

## 4. quota exceed / check failure 的异常模型

### 4.1 超额

抛：
- `QuotaExceededError`
- HTTP `429`
- code `QUOTA_EXCEEDED`

### 4.2 检查失败

抛：
- `QuotaCheckFailedError`
- HTTP `503`
- code 通常来自 checked payload

而且测试明确验证了：
- check 失败 payload 会保留在 `extra_payload` 里

## 5. require_quota() 的真实作用

`require_quota(quota_type, strict_config=False)` 会返回一个 FastAPI dependency。

这条 dependency 自己又依赖：
- `require_auth_context`

所以只要你把它挂到路由上，实际上就同时得到了：
- 强制登录
- precheck
- 豁免逻辑
- strict_config 语义

因此有一个非常重要的副作用：
- 即使某个路由自己还写了 `get_optional_auth_context`
- 只要同时挂了 `require_quota()`，最终就还是强制登录

`documents.view_pdf` 就是这种情况。

## 6. finalize_quota() 为什么重要

`finalize_quota(grant, result, status_code=None)` 不是无脑加 1，而是会先判断：

- grant 是否存在
- config_active 是否为真
- 当前结果是否应该计数

其中最关键的是：
- `should_count_result()`

## 7. 什么结果会被计数

### 7.1 一般会计数

- 普通 `Response` 且状态码 < 400
- `FileResponse`
- `RedirectResponse`
- 成功 JSON 且没有 `error`

### 7.2 不会计数

- 状态码 >= 400
- JSON payload 里 `success=false`
- JSON payload 里有 `error`

测试也明确固定了：
- `JSONResponse(status=200, {"error":"..."})` 不计数
- `Response(status=200)` 会计数

## 8. 这套语义如何影响业务

### 8.1 documents.view_pdf

只有真正返回 PDF 响应时才计 `file_view`。

### 8.2 documents.summarize_pdf / translate

只有业务成功时才 finalize。

### 8.3 ask_gateway

只有 ask_stream 结果按成功路径完成时才会增加 ask query 配额。

### 8.4 conversation 文件下载

只有真正返回 redirect/file 时才计 `file_view`。

## 9. 与 uploads 的特例对比

标准 quota 接入方式是：

1. `require_quota()` precheck
2. 业务执行
3. `finalize_quota()`

但 `uploads` 模块不是这样：
- 它自己在控制器里直接 `check_quota() + increment_quota()`
- 没有 finalize 阶段

结果就是：
- 上传中途失败也可能已经扣了 quota

这不是 quota 模块的标准语义，而是上传模块的特例实现。

## 10. 这层机制为什么值得单独拆出来看

因为对公共能力来说，真正关键的不是 config CRUD，而是：
- 业务路由如何安全接 quota
- 失败结果如何避免误扣
- 缺配置什么时候放行，什么时候阻断

这三个问题，都是 `deps.py` 决定的。
