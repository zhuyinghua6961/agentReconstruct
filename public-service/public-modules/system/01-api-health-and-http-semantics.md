# system 接口面、health 语义与 HTTP 风格

对应代码：
- `backend/app/modules/system/api.py`
- `backend/app/modules/system/service.py`
- `backend/app/modules/system/schemas.py`
- `backend/tests/test_system.py`
- `backend/tests/test_health.py`

## 1. system 路由面同时保留了裸路径和 `/api/v1` 路径

当前接口包括：

- `GET /health`
- `GET /api/v1/health`
- `GET /api/background_status`
- `GET /api/v1/background_status`
- `GET /api/cache_debug/conversation`
- `GET /api/v1/cache_debug/conversation`
- `GET /api/kb_info`
- `GET /api/v1/kb_info`
- `POST /api/refresh_kb`
- `POST /api/v1/refresh_kb`
- `POST /api/clear_cache`
- `POST /api/v1/clear_cache`

这说明 system 同时服务：

- 直接健康探针/运维入口
- 前端当前使用的 `/api/v1` 路径

## 2. 只有 conversation cache debug 需要登录

API 层的依赖关系很明确：

- `conversation_cache_debug()` 依赖 `require_auth_context`
- 其余接口只依赖 `get_runtime`

因此当前默认暴露给未登录请求的有：

- `health`
- `background_status`
- `kb_info`
- `refresh_kb`
- `clear_cache`

这对“系统公共能力”来说是个很重的边界事实，因为它不只是读状态，还包含运维动作。

## 3. `health` 的返回不是简单存活探针

`build_health()` 返回：

- `status`
- `agent_initialized`
- `generation_runtime_initialized`
- `vector_db_initialized`
- `storage_backend`
- `components`
- `qa_cache`
- `timestamp`

判断 overall status 的规则也很直接：

- 默认 `healthy`
- 只要任何 component 的 `status == degraded`
- 总体就变成 `degraded`

所以它不是传统的：

- 进程活着就是 200/ok

而是一个进程内依赖状态快照。

## 4. `health` 的顶层不返回 `success`

测试 `test_health_contract()` 还固定了一个细节：

- body 里不应该出现 `success`

这与 `background_status`、`kb_info` 这类接口不同。

所以 system 接口不是统一 envelope 风格，而是至少分成两类：

- health 风格：直接状态快照
- 其他风格：`success + payload`

## 5. 多数 system 操作失败时仍然返回 200

`build_kb_info()`：

- 即使异常，也返回 `(payload, 200)`

`refresh_kb()`：

- agent 未初始化、init_agent 缺失、刷新失败、异常，统统还是 `200`

`clear_cache()`：

- 异常时也仍返回 `200`

真正会返回 `500` 的主要是：

- `background_status` 内部异常
- `conversation_cache_debug` 内部异常

所以这个模块的 HTTP 语义明显偏“运维命令返回体”，而不是严格用状态码表达业务成败。

## 6. `system/schemas.py` 只是简化草图，不是强约束契约

schema 定义了：

- `HealthResponse`
- `BackgroundStatusResponse`
- `KbInfoResponse`
- `MessageResponse`

但 API 层没有显式 `response_model=`。

而 service 返回字段又明显比 schema 多，例如：

- `health` 的 `components`
- `qa_cache`
- `storage_backend`
- `background_status` 的 `conversation_outbox`
- `upload_processing`

所以当前真正的契约来源是：

- service 的实际返回
- 测试固定的字段

而不是 schema 文件本身。

## 7. 测试把 `/health` 和 `/api/v1/health` 都钉住了

`test_health_routes_registered()` 固定：

- `/health`
- `/api/v1/health`

都必须存在。

`test_system_routes_registered()` 也固定了：

- background status
- cache debug
- kb_info
- refresh_kb
- clear_cache

都必须注册在 app 上。

## 8. system API 层本身非常薄

`api.py` 几乎只做：

- 注入 runtime
- 注入 auth context
- 调用 `system_service`
- 用 `_json()` 组 JSONResponse

所以 system 的真实复杂度几乎全部在 service 层和 runtime 依赖图里。

## 9. 这个模块的一个核心问题是接口安全边界未收紧

从代码事实看，当前任何未登录请求都能触发：

- `refresh_kb`
- `clear_cache`

即便这些操作最后可能只影响 QA runtime，它们仍然不是纯只读接口。

因此把 system 视为公共能力时，必须把“默认未鉴权”写成一等事实。
