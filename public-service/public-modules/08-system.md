# system 模块代码细读

模块路径：
- `backend/app/modules/system/api.py`
- `backend/app/modules/system/service.py`
- `backend/app/modules/system/schemas.py`
- `backend/app/core/runtime.py`
- `backend/tests/test_system.py`
- `backend/tests/test_health.py`
- `backend/tests/test_redis_runtime.py`
- `frontend-vue/src/services/api.js`
- `frontend-vue/src/api/chat.js`

模块定位：
- 明确属于公共能力
- 但内部已经混入平台观测、后台 worker 调试、QA 知识库运维三类信息

已细拆到：

- `system/README.md`
- `system/01-api-health-and-http-semantics.md`
- `system/02-background-status-and-cache-debug.md`
- `system/03-kb-runtime-and-cache-ops.md`
- `system/04-runtime-dependencies-and-schema-gaps.md`
- `system/05-frontend-usage-and-security-boundaries.md`

本模块的关键结论：

- `health` 不是简单存活探针，而是基于 `runtime.component_status` 的进程内依赖快照
- `background_status` 和 `conversation_cache_debug` 已经形成了比较实用的平台观测/调试接口
- `kb_info`、`refresh_kb`、`clear_cache` 更像 QA 子系统运维接口，不是纯平台 system 信息
- `schemas.py` 与真实返回体明显脱节，真实契约主要靠 service 返回和测试固定
- 当前除了 `conversation_cache_debug` 之外，大多数 system 接口默认未鉴权，这在公共能力边界上是个很重要的问题

当前已确认问题与迁移修复点：

- `P1` `kb_info / refresh_kb / clear_cache` 当前只依赖 `get_runtime`，没有 `require_auth_context` 或 `require_admin_context`，未登录请求也可以触发知识库刷新和缓存清理。这是明确安全问题。
- `P2` `schemas.py` 与真实返回体明显脱节，而且大多数 API 未声明 `response_model`；如果后续把这部分直接作为公共后端契约输出，schema 会误导实现和对接方。
- `P3` `system` 当前同时混合：
  - 平台健康与后台线程观测
  - conversation cache 调试
  - QA 知识库运维动作
- 所以后续不应简单理解成“把 system 模块整包迁走”，而应把它视为一组需要重新分层的公共运维能力。

建议阅读顺序：

1. 先看 `system/01-api-health-and-http-semantics.md`
2. 再看 `system/02-background-status-and-cache-debug.md`
3. 然后看 `system/03-kb-runtime-and-cache-ops.md`
4. 如果要梳理依赖和 schema 偏差，再看 `system/04-runtime-dependencies-and-schema-gaps.md`
5. 如果要讨论接口暴露面和前端接入，再看 `system/05-frontend-usage-and-security-boundaries.md`
