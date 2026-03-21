# quota 接口与管理面

对应代码：
- `backend/app/modules/quota/api.py`
- `backend/app/modules/quota/schemas.py`
- `backend/tests/test_quota.py`

## 1. 对外接口总表

当前公开路由：

- `GET /api/v1/quota/my`
- `GET /api/v1/quota/configs`
- `POST /api/v1/quota/configs`
- `PUT /api/v1/quota/configs/{quota_type:path}`
- `POST /api/v1/quota/reset/{target_user_id}/{quota_type:path}`
- `GET /api/v1/quota/users/{target_user_id}`

同时保留 `/api/quota/...` 兼容路径。

## 2. 权限边界

### 2.1 普通登录用户

只能访问：
- `GET /quota/my`

依赖：
- `require_auth_context`

### 2.2 管理员

才能访问：
- configs 列表
- create config
- update config
- reset 指定用户 quota
- 查询任意用户 quota

依赖：
- `require_admin_context`

注意：
- 这里要求的是 role=`admin`
- 不是所有登录用户都能管理 quota

## 3. 状态码映射

`api.py` 自己做了一层 `_status()` 映射：

- success -> 路由声明的成功状态码
- `VALIDATION_ERROR` -> `400`
- `NOT_FOUND` -> `404`
- `ALREADY_EXISTS` -> `409`
- `DB_UNAVAILABLE` -> `503`
- 其他失败 -> `500`

所以 quota 模块的 HTTP 语义相对标准。

## 4. create / update 请求模型

### 4.1 CreateQuotaConfigRequest

字段：
- `quota_type`
- `quota_name`
- `default_limit`
- `daily_limit`
- `weekly_limit`
- `monthly_limit`
- `is_active`
- `period`
- `period_days`

### 4.2 UpdateQuotaConfigRequest

字段：
- `default_limit`
- `daily_limit`
- `weekly_limit`
- `monthly_limit`
- `is_active`
- `period`
- `period_days`

更新接口里：
- 不能改 `quota_type`
- 也不能改 `quota_name`

这说明 quota 的名字和类型在当前模型里更像“创建后稳定”的配置主键/标签。

## 5. create / update 如何判断是否是多窗口配置

路由层会自己算：
- `multi_limits_provided = any(daily_limit, weekly_limit, monthly_limit is not None)`

然后把这个布尔值显式传给 service。

这是个关键实现点，因为 service 不是单靠字段值推断模式，而是同时看：
- period/default_limit
- multi_limits_provided

## 6. `GET /quota/my` 和 `GET /quota/users/{id}` 返回的不是同一种前端友好结构

后端返回统一是：
- `data.quotas` 数组
- 每项含 `quota_type / quota_name / current / limit / remaining / windows / reset_hint`

但前端 `services/quota.js` 会把 `getMyQuotas()` 再转成：
- 按 quota_type keyed object

这意味着：
- API contract 是数组
- 某些前端消费面已经二次归一化成对象映射

## 7. reset 的返回语义

`reset_user_quota()` 成功后不会只返回 “ok”，而是：
- 再调一次 `check_quota()`
- 把 reset 后的最新 quota 状态作为 `data` 返回

这对管理端是有价值的，因为可以立即刷新显示。

## 8. quota API 的管理面其实有两套

后端只暴露一套管理 API，但前端有两套管理消费面：

- 独立管理页 `QuotaManagement.vue`
- 控制面里的轻量管理 `useQuotaAdmin + ControlsPanel`

这两套前端对同一个 API surface 的使用方式并不完全一致。

## 9. 当前接口面的关键结论

- 后端 API 足够完整，支持 full quota config lifecycle
- 但前端管理面没有完全把后端能力都暴露出来
- create/update 的多窗口语义是由路由层显式传给 service 的
- `quota_type` 已经是路由路径的一部分，是平台级稳定标识符
