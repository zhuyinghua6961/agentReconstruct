# system 细拆索引

对应代码：
- `backend/app/modules/system/api.py`
- `backend/app/modules/system/service.py`
- `backend/app/modules/system/schemas.py`
- `backend/app/core/runtime.py`
- `backend/tests/test_system.py`
- `backend/tests/test_health.py`
- `backend/tests/test_redis_runtime.py`
- `frontend-vue/src/services/api.js`
- `frontend-vue/src/api/chat.js`

本目录把 `system` 再拆成 5 个视角：

- `01-api-health-and-http-semantics.md`
  说明 system 对外接口、鉴权情况、状态码风格和 health 返回语义。
- `02-background-status-and-cache-debug.md`
  说明后台线程状态、upload worker 状态、QA cache 快照和 conversation cache debug 的真实输出。
- `03-kb-runtime-and-cache-ops.md`
  说明 `kb_info`、`refresh_kb`、`clear_cache` 这些 QA 运维接口的行为和边界。
- `04-runtime-dependencies-and-schema-gaps.md`
  说明 system 对 runtime/redis/retrieval 的依赖，以及 schema 文件和真实返回体之间的脱节。
- `05-frontend-usage-and-security-boundaries.md`
  说明前端如何消费 system 接口，以及当前默认未鉴权带来的公共能力边界问题。

总体判断：
- `system` 明确属于公共能力，但它不是一个单一职责模块。
- 当前它混合了两类东西：
  - 平台健康/后台线程/缓存调试
  - QA 子系统知识库与答案缓存运维
- 后续如果做公共服务拆分，`system` 很可能要继续分层，而不是整体迁走。

当前已确认问题：
- `kb_info / refresh_kb / clear_cache` 当前默认未鉴权。
- `schemas.py` 与真实返回体明显脱节。
- `system` 同时混合平台观测与 QA 运维动作，边界并不干净。
