# system 后台状态与 conversation cache 调试

对应代码：
- `backend/app/modules/system/service.py`
- `backend/app/modules/conversation/cache.py`
- `backend/tests/test_system.py`

## 1. `background_status` 是一个混合型后台状态快照

`build_background_status()` 会返回：

- `current_answer_context` 是否存在
- 当前答案上下文预览
- 最新 background programmatic insert 日志文件
- `conversation_outbox`
- `upload_processing`
- `qa_cache`

这说明它不是单一后台 worker 的状态页，而是多个后台链路拼接成的运行面板。

## 2. conversation outbox 状态来自 runtime 内存对象

读取来源：

- `runtime.conversation_outbox_thread`
- `runtime.conversation_outbox_status`

如果 thread 对象有 `is_alive()`，会覆盖/补充：

- `thread_alive`

如果 runtime 里还没有 outbox 状态，则 service 自己构造默认值：

- `state = uninitialized`
- `thread_alive = False`
- `loops = 0`
- `last_summary = None`
- `last_error = ""`
- `last_run_at = None`

所以这个接口对“后台线程未初始化”是有明确表达的。

## 3. upload processing 状态来自 component_status + worker 实例

先读：

- `runtime.component_status["upload_processing"]`

再看：

- `runtime.upload_processing_worker`

如果 worker 存在，会补：

- `enabled`
- `_active_keys` 的长度，记作 `active_tasks`

这意味着它不是只读 runtime 状态字典，还会直接窥视 worker 实例内部字段。

## 4. latest background file 是读本地日志目录

逻辑会扫：

- `runtime.logs_dir/background_programmatic_insert_*.json`

按 `mtime` 倒序取最新一份，返回：

- `latest_background_file`
- `latest_background_file_mtime`

所以这个后台状态接口不仅看内存和 Redis，还会扫本地磁盘日志目录。

## 5. `qa_cache` 快照被嵌进两个 system 接口里

`_cache_status()` 会同时输出：

- `metrics`
- `config`

配置快照来自环境变量：

- `QA_CACHE_LOCK_ENABLED`
- `QA_CACHE_WAIT_MS`
- `QA_CACHE_LOCK_TTL_SECONDS`
- `QA_STAGE1_CACHE_TTL_SECONDS`
- `QA_STAGE2_CACHE_TTL_SECONDS`
- `PDF_TEXT_CACHE_TTL_SECONDS`
- conversation cache 相关 TTL 和 recent pages 配置

这个快照不仅被 `background_status` 用，也被 `health` 用。

所以 QA cache 已经成了 system 观测的一部分。

## 6. `conversation_cache_debug` 是相对干净的公共运维接口

相比 `kb_info` 等 QA 侧接口，`build_conversation_cache_debug()` 更像真正的平台公共调试能力。

它会：

1. 构造带 prefix 的 `RedisService`
2. 读取 conversation list version
3. 读取 recent pages
4. 检查首页 `(1,20)` 和 recent pages 对应的缓存页
5. 可选读取指定 conversation detail cache

返回体里会包含：

- Redis 是否可用
- key prefix
- list cache version
- recent pages key 与 TTL
- 各页 cache key / TTL / conversation_count / preview
- detail cache version / TTL / message_count / uploaded_files_count / last_message_preview

## 7. 它只允许看当前登录用户自己的 cache

`conversation_cache_debug()` 的 user_id 来源是：

- `require_auth_context()` 注入的当前用户

而不是 query 参数。

因此这个接口虽然是 debug 面，但没有开放成：

- 任意 user_id 任意探查

这点比 system 里其他未鉴权接口的边界要好很多。

## 8. recent pages 的策略也暴露在这个接口里

调试接口会：

- 固定检查 `(1,20)`
- 再把 recent pages 补进 pages_to_check

这和 conversation 模块自己的 recent-pages 缓存设计相互印证。

所以它不仅是“看缓存内容”，也是“看缓存访问策略是否生效”的调试入口。

## 9. 测试固定了这些关键行为

`test_background_status_contract()` 固定：

- outbox thread alive 会被正确反映
- upload worker active tasks 会被统计
- latest background file 会按 mtime 取最新
- qa_cache metrics 会聚合到 `all`

`test_conversation_cache_debug_contract()` 固定：

- recent pages TTL
- list/detail cache present
- detail message_count / uploaded_files_count

所以这两个接口虽然看着像临时运维页，实际上已有相对稳定的 contract。

## 10. 这部分说明 system 里已经长出“平台可观测性”雏形

尤其是：

- background status
- conversation cache debug

已经不只是工具函数，而是平台内部状态面板 API。

只是目前它和 QA cache/runtime 状态仍然混在一起，没有独立分层。
