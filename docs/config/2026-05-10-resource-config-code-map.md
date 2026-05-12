# Resource Config Code Map

日期：2026-05-10

范围：`resource/config/**` 中定义的配置项，对应 `gateway`、`public-service`、`fastQA`、`highThinkingQA`、`patent`、`scripts` 的生产代码和启动脚本。未扫描 tests、历史文档、日志、构建产物、pytest 临时目录和 egg-info 元数据。

安全说明：真实 `config.secret.env` 中的值只标记为 `<redacted:set>` 或 `<blank>`，不在本文档回显。`.secret.env.example` 只作为模板记录。

方法说明：表格由 `resource/config` 变量清单和生产代码直接字符串引用生成；“代码默认值/硬编码值”是启发式提取，复杂 shell 参数展开或多层 Python fallback 需要到列出的代码位置复核。

## 2026-05-11 实施后状态

2026-05-11 的 resource config simplification 已按目标命名完成代码和 `resource/config/**` 清理。本文档后半部分的“自动排查项”和逐变量命中表是 2026-05-10 的迁移前扫描快照，只作为历史定位依据；判断当前配置面时，以本节和上方“最终精简策略”为准，并以当前代码/配置 grep 为最终依据。

当前运行契约：

- 文档问答 LLM 只使用统一 `LLM_*` 命名；`OPENAI_*`、`DASHSCOPE_*`、`PATENT_OPENAI_*`、阶段专属 model key 和 thinking 开关不再作为运行配置。
- fastQA/patent rerank endpoint 只使用统一 `RERANK_*` 命名；保留 `QA_RETRIEVAL_RERANK_CANDIDATES`、`PATENT_STAGE2_RERANK_CANDIDATES`、`PATENT_STAGE2_RERANK_TOP_PATENTS` 作为检索规模参数。
- fastQA/patent embedding 使用共享 `EMBEDDING_*` / `EMBEDDING_API_*`；highThinkingQA embedding 使用专属 `HIGHTHINKINGQA_EMBEDDING_*`。
- Redis、MinIO proxy、chat persistence、upload processing、gateway admission、graph KB、rerank 主流程、patent shared pool / hot pool / upstream gate 等已确认能力在代码中固定启用；warmup/preheat 类能力固定关闭。
- 仍保留连接、凭据、路径、容量、超时、worker、graph、auth 和检索规模类配置。

## Subagent 审查修正说明

2026-05-10 使用 `gpt-5.5` / `xhigh` subagent 对本文档做了只读审查。结论是全量映射和 secret 脱敏大体可信，但本文档不能直接作为删配置清单使用，原因如下：

- `与代码默认值相同的自动排查项` 是按变量名聚合生成，不是按“服务 + 配置文件条目”逐项判断。同名变量在一个服务等于代码默认值、在另一个服务覆盖代码默认值时，会被归到同一行。
- `.env.example` 是模板，不是当前运行配置。表格中的模板值只用于说明配置形态，不参与“是否可删/是否应写死”的结论。
- `*_SERVICE_*_ROOT` 这类变量可能通过 `SERVICE_CODE` 动态拼接读取，直接字符串扫描会漏掉。`未命中直接字符串引用` 只表示没有直接字符串命中，不等价于完全未使用。
- `scripts/test_*` 命中不作为生产删改依据；已从已知混入行中移除。
- “代码默认值/硬编码值”列存在启发式截断，例如复杂 Python fallback 或 shell 参数展开，需要以右侧代码位置为准复核。

已知高风险例子：`ASK_STREAM_MAX_CONCURRENT` 中 fastQA 的 `20` 与代码默认值一致，但 highThinkingQA 的 `20` 覆盖了 `highThinkingQA/config.py:331` 的默认 `5`。按最终策略，ask stream 并发上限属于后端最大请求数，不写死，两个服务都保留配置。

## 最终精简策略

本节是后续精简配置时的最高优先级规则。

本轮新增决策：对已经确认“当前部署必不可少、不能关闭”的布尔开关，只写死其启用状态，并从 `resource/config` 退役对应开关；但这些能力依赖的连接信息、容量数值、超时、worker 数、路径、密钥仍然保留为配置项。例如 Redis 可以写死为启用，但 `REDIS_HOST`、`REDIS_PORT`、`REDIS_DB`、`REDIS_PASSWORD`、`REDIS_KEY_PREFIX`、socket timeout 等仍保留配置。

下面这些配置即使出现在自动排查表里，也不写死到代码里，仍保留为配置项：

- 连接信息：各服务 host、port、backend base URL、外部 LLM/embedding/rerank base URL、service root、runtime root、state root、asset root、上传目录、向量库路径、模型路径、数据目录等部署相关路径或地址。OCR 当前不用，不纳入最终保留配置。
- JWT / auth：`JWT_SECRET`、`JWT_EXPIRE_SECONDS`、`PASSWORD_EXPIRE_DAYS`、登录失败锁定策略、内部服务 token、admission control token 等认证和安全策略。
- 数据库 / 对象存储 / 图数据库 / 缓存：`MYSQL_*`、`MINIO_*`、`REDIS_*`、`NEO4J_*`，以及 `FASTQA_NEO4J_*`、`PUBLIC_SERVICE_NEO4J_*`、`PATENT_NEO4J_*` 这类服务前缀变体。例外：只表示“是否启用”的必开布尔开关按下方“写死启用”清单处理，例如 `REDIS_ENABLED`、`MINIO_USE_PROXY`。
- 请求限流：全局最大请求数、单用户最大请求数、各后端最大请求数、ask stream 并发上限和执行 worker 容量，具体包括 `INTERACTIVE_EXECUTION_MAX_CONCURRENT`、`INTERACTIVE_EXECUTION_PER_USER_MAX_ACTIVE`、`INTERACTIVE_EXECUTION_FAST_OR_PATENT_MAX_CONCURRENT`、`INTERACTIVE_EXECUTION_THINKING_MAX_CONCURRENT`、`ASK_STREAM_MAX_CONCURRENT`、`PATENT_ASK_STREAM_MAX_CONCURRENT`、`ASK_EXECUTOR_MAX_WORKERS`、`PATENT_ASK_EXECUTOR_MAX_WORKERS`。这些值用于运行期容量控制，不能写死。

本轮明确保留配置的变量族如下，后续即使在下方自动排查表中看到“配置值等于代码默认值”，也不作为写死候选：

| 保留类别 | 变量 / 模式 | 保留原因 |
| --- | --- | --- |
| 连接信息 | `*_HOST`、`*_PORT`、`*_BASE_URL`、`*_URL`、`*_ENDPOINT`，以及外部 LLM、embedding、rerank 的地址和部署相关根路径 | 跟部署拓扑、机器路径和外部依赖绑定，需要按环境调整；OCR 当前不用，不纳入最终保留配置。 |
| JWT / auth | `JWT_SECRET`、`JWT_EXPIRE_SECONDS`、`PASSWORD_EXPIRE_DAYS`、登录失败锁定策略、内部服务 token、admission control token | 属于认证、安全策略或密钥，不写入代码。 |
| 数据库 | `MYSQL_*` | 数据库地址、账号、库名、连接池和超时等运行环境差异需要保留。 |
| MinIO | `MINIO_ENDPOINT`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`MINIO_BUCKET`、`MINIO_SECURE`、`MINIO_REGION`、`MINIO_DOWNLOAD_EXPIRES` 等 | 对象存储 endpoint、bucket、凭据、secure、下载过期时间等部署差异需要保留；`MINIO_USE_PROXY` 已确认必开，进入“写死启用”清单。 |
| Redis | `REDIS_URL`、`REDIS_HOST`、`REDIS_PORT`、`REDIS_USERNAME`、`REDIS_PASSWORD`、`REDIS_DB`、`REDIS_KEY_PREFIX`、`REDIS_SOCKET_*`，以及 patent 对应连接参数 | 缓存、队列、锁和限流依赖 Redis，连接信息及 key namespace 需要保留；`REDIS_ENABLED` 已确认必开，进入“写死启用”清单。 |
| Neo4j | `NEO4J_*`、`FASTQA_NEO4J_*`、`PUBLIC_SERVICE_NEO4J_*`、`PATENT_NEO4J_*` | 图数据库连接、账号、库名和各服务图谱后端差异需要保留。 |
| 全局最大请求数 | `INTERACTIVE_EXECUTION_MAX_CONCURRENT` | 网关/调度层全局容量控制，不写死。 |
| 单用户最大请求数 | `INTERACTIVE_EXECUTION_PER_USER_MAX_ACTIVE` | 单用户并发保护策略，不写死。 |
| 各后端最大请求数 | `INTERACTIVE_EXECUTION_FAST_OR_PATENT_MAX_CONCURRENT`、`INTERACTIVE_EXECUTION_THINKING_MAX_CONCURRENT`、`ASK_STREAM_MAX_CONCURRENT`、`PATENT_ASK_STREAM_MAX_CONCURRENT`、`ASK_EXECUTOR_MAX_WORKERS`、`PATENT_ASK_EXECUTOR_MAX_WORKERS` | 后端容量、流式问答并发和执行 worker 容量控制，不写死。 |

### 模型配置收敛规则

三个文档后端的 LLM 调用已经确认使用同一个全局模型配置，后续只保留一套统一的 LLM 配置。服务级别或 provider 兼容别名不再作为独立配置长期保留。

| 能力 | 最终保留配置 | 退役 / 写死方向 | 说明 |
| --- | --- | --- | --- |
| 三个文档后端 LLM | `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`、`LLM_CONNECT_TIMEOUT_SECONDS`、`LLM_READ_TIMEOUT_SECONDS`、`LLM_STREAM_READ_TIMEOUT_SECONDS`、`LLM_WRITE_TIMEOUT_SECONDS`、`LLM_POOL_TIMEOUT_SECONDS`、`LLM_KEEPALIVE_EXPIRY_SECONDS`、`LLM_MAX_CONNECTIONS`、`LLM_MAX_KEEPALIVE_CONNECTIONS` | `OPENAI_API_KEY`、`DASHSCOPE_API_KEY`、`PATENT_OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`、`DASHSCOPE_BASE_URL`、`DASHSCOPE_MODEL`、`PATENT_OPENAI_BASE_URL`、`PATENT_OPENAI_MODEL`、`OPENAI_*_TIMEOUT_SECONDS`、`PATENT_OPENAI_TIMEOUT_SECONDS` 作为兼容别名逐步退役；`DECOMPOSE_MODEL`、`DIRECT_ANSWER_MODEL`、`SUB_ANSWER_MODEL`、`CHECKER_MODEL`、`QUERY_EXPANSION_MODEL`、`PDF_QA_MODEL` 等子流程模型配置并入 `LLM_MODEL`，不再单独配置；`PDF_QA_USE_DEDICATED_LLM` 不再作为运行配置。 | fastQA、highThinkingQA、patent 的文档问答 LLM 统一由 `LLM_*` 控制。当前只有 openai-compatible 调用形态，`LLM_PROVIDER` 不作为长期配置保留，按固定调用形态处理。 |
| LLM thinking 行为 | 不保留运行配置 | `LLM_ENABLE_THINKING`、`DIRECT_ANSWER_ENABLE_THINKING`、`DECOMPOSE_ENABLE_THINKING` 按目标流程写死。 | 这类是固定流程行为，不作为部署配置。 |
| Rerank | `RERANK_API_KEY`、`RERANK_PROVIDER`、`RERANK_BASE_URL`、`RERANK_MODEL`、`RERANK_TIMEOUT_SECONDS` | `QA_RETRIEVAL_RERANK_PROVIDER`、`QA_RETRIEVAL_RERANK_BASE_URL`、`QA_RETRIEVAL_RERANK_MODEL`、`QA_RETRIEVAL_RERANK_TIMEOUT`、`PATENT_STAGE2_RERANK_PROVIDER`、`PATENT_STAGE2_RERANK_BASE_URL`、`PATENT_STAGE2_RERANK_MODEL`、`PATENT_STAGE2_RERANK_TIMEOUT_SECONDS`、`PATENT_STAGE2_RERANK_ENDPOINT_FAMILY` 并入统一 `RERANK_*`。 | fastQA 和 patent 的 rerank 模型配置统一；候选数、topN 等检索规模参数仍按业务保留；fastQA/patent 的 rerank 启用开关属于流程开关，按必开能力写死启用，不属于 endpoint 配置。执行顺序必须是先改代码读取 `RERANK_*`，再删除旧服务级别 key，避免只删配置导致 endpoint 失效。 |
| fastQA + patent embedding | `EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL`、`EMBEDDING_MODEL`、`EMBEDDING_MODEL_TYPE`、`EMBEDDING_API_URL`、`EMBEDDING_API_MODEL`、`EMBEDDING_API_TIMEOUT_SECONDS` | `EMBEDDING_TIMEOUT_SECONDS` 当前无直接代码读取，作为旧/重复 timeout 配置退役；`PATENT_EMBEDDING_BASE_URL`、`PATENT_EMBEDDING_MODEL`、`PATENT_EMBEDDING_MODEL_TYPE`、`PATENT_EMBEDDING_API_URL`、`PATENT_EMBEDDING_API_MODEL`、`PATENT_EMBEDDING_API_TIMEOUT_SECONDS` 并入统一 embedding 配置。 | fastQA 和 patent 调用同一个 embedding 模型；统一 timeout 使用实际被代码读取的 `EMBEDDING_API_TIMEOUT_SECONDS`。 |
| highThinkingQA embedding | highThinkingQA 独立 embedding 配置，后续必须使用服务前缀命名，例如 `HIGHTHINKINGQA_EMBEDDING_BASE_URL`、`HIGHTHINKINGQA_EMBEDDING_MODEL`、`HIGHTHINKINGQA_EMBEDDING_DIMENSIONS`、`HIGHTHINKINGQA_EMBEDDING_API_KEY`；embedding 吞吐/并发/队列参数也使用同一前缀，例如 `HIGHTHINKINGQA_EMBEDDING_BATCH_SIZE`、`HIGHTHINKINGQA_EMBEDDING_API_RPM`、`HIGHTHINKINGQA_EMBEDDING_API_TPM`、`HIGHTHINKINGQA_EMBEDDING_CONCURRENCY`、`HIGHTHINKINGQA_EMBEDDING_MAX_CONCURRENT_REQUESTS`、`HIGHTHINKINGQA_EMBEDDING_MAX_INPUT_TOKENS`、`HIGHTHINKINGQA_EMBEDDING_MAX_RETRIES`、`HIGHTHINKINGQA_EMBEDDING_QUEUE_SIZE` | 不和 fastQA/patent 的 `EMBEDDING_*` 混用；当前 highThinkingQA service env 中的 `EMBEDDING_BASE_URL`、`EMBEDDING_MODEL`、`EMBEDDING_DIMENSIONS` 以及 `EMBED_BATCH_SIZE`、`EMBED_API_RPM`、`EMBED_API_TPM`、`EMBED_CONCURRENCY`、`EMBED_MAX_*`、`EMBED_QUEUE_SIZE` 属于命名冲突，后续迁到 highThinkingQA 专属前缀。 | highThinkingQA 使用自己的 embedding 模型和吞吐限制，避免同名变量与 fastQA/patent 全局 embedding 配置冲突。 |
| OCR | 不保留 | `OCR_BASE_URL`、`OCR_MODEL`、`OCR_TIMEOUT_SECONDS`、`OCR_API_KEY`、`OCR_CONCURRENCY`、`OCR_MAX_CONCURRENT_REQUESTS`、`OCR_PAGES_PER_BATCH`、`OCR_MAX_RETRIES`、`OCR_RETRY_BASE` 从最终配置清单移除。 | 当前后期本地部署用不到 OCR，后续重新启用时再单独设计。 |

### 模型 / 预热 / OCR 行级结论速查

下表用于覆盖后面自动生成表格里的逐行候选结论。自动表格如果没有重复写“最终策略”，仍以本节和上面的“模型配置收敛规则”为准。

| 变量 / 模式 | 最终处理 | 备注 |
| --- | --- | --- |
| `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL`、`LLM_*_TIMEOUT_SECONDS`、`LLM_MAX_CONNECTIONS`、`LLM_MAX_KEEPALIVE_CONNECTIONS`、`LLM_KEEPALIVE_EXPIRY_SECONDS` | 保留统一配置 | 三个文档后端统一使用这一套 LLM 配置。 |
| `OPENAI_API_KEY`、`DASHSCOPE_API_KEY`、`PATENT_OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`、`DASHSCOPE_BASE_URL`、`DASHSCOPE_MODEL`、`PATENT_OPENAI_BASE_URL`、`PATENT_OPENAI_MODEL`、`OPENAI_*_TIMEOUT_SECONDS`、`PATENT_OPENAI_TIMEOUT_SECONDS` | 退役 | 作为兼容别名，不再作为最终运行配置。 |
| `LLM_PROVIDER` | 不保留 | 当前固定为 openai-compatible 调用形态，后续写死调用形态。 |
| `DECOMPOSE_MODEL`、`DIRECT_ANSWER_MODEL`、`SUB_ANSWER_MODEL`、`CHECKER_MODEL`、`QUERY_EXPANSION_MODEL`、`PDF_QA_MODEL` | 退役 | 子阶段模型并入 `LLM_MODEL`，不再单独配置。 |
| `LLM_ENABLE_THINKING`、`DIRECT_ANSWER_ENABLE_THINKING`、`DECOMPOSE_ENABLE_THINKING` | 不保留 | 按目标流程写死 thinking 行为。 |
| `RERANK_API_KEY`、`RERANK_PROVIDER`、`RERANK_BASE_URL`、`RERANK_MODEL`、`RERANK_TIMEOUT_SECONDS` | 保留统一配置 | fastQA 和 patent 使用同一套 rerank 配置。 |
| `QA_RETRIEVAL_RERANK_*` 中的 provider/base/model/timeout/API key、`PATENT_STAGE2_RERANK_*` 中的 provider/base/model/timeout/API key/endpoint family | 退役 | 并入统一 `RERANK_*`。 |
| `RERANK_*` 统一执行顺序 | 先改代码，再删旧配置 | 当前代码仍读取 `QA_RETRIEVAL_RERANK_*` / `PATENT_STAGE2_RERANK_*`；实施时必须先让 fastQA 和 patent 读取 `RERANK_*`，确认兼容后再删除旧 key。 |
| `QA_RETRIEVAL_RERANK_ENABLED`、`PATENT_STAGE2_RERANK_ENABLED` | 写死启用 | rerank 是当前检索主流程能力，开关不再作为运行配置；provider/base/model/timeout/API key 仍统一到 `RERANK_*`。 |
| `QA_RETRIEVAL_RERANK_CANDIDATES`、`PATENT_STAGE2_RERANK_CANDIDATES`、`PATENT_STAGE2_RERANK_TOP_PATENTS` | 保留或按业务另行确认 | 这些是检索规模参数，不是 rerank 模型 endpoint 配置。 |
| fastQA + patent 的 `EMBEDDING_*` / `EMBEDDING_API_*` | 保留统一配置 | fastQA 和 patent 调用同一个 embedding 模型；`EMBEDDING_TIMEOUT_SECONDS` 是旧/重复变量，最终使用 `EMBEDDING_API_TIMEOUT_SECONDS`。 |
| `PATENT_EMBEDDING_*` | 退役 | 并入 fastQA + patent 共用的 `EMBEDDING_*`。 |
| highThinkingQA 当前 `EMBEDDING_BASE_URL`、`EMBEDDING_MODEL`、`EMBEDDING_DIMENSIONS`、`EMBEDDING_API_KEY`，以及 `EMBED_BATCH_SIZE`、`EMBED_API_RPM`、`EMBED_API_TPM`、`EMBED_CONCURRENCY`、`EMBED_MAX_CONCURRENT_REQUESTS`、`EMBED_MAX_INPUT_TOKENS`、`EMBED_MAX_RETRIES`、`EMBED_QUEUE_SIZE` | 重命名后保留 | 改成 `HIGHTHINKINGQA_EMBEDDING_*` 专属前缀，避免和 fastQA/patent 统一 embedding 配置冲突。 |
| `OCR_*` | 不保留 | 当前本地部署不用 OCR，不写进最终配置清单。 |
| `FASTQA_STAGE2_CHAT_WARMUP_ENABLED`、`FASTQA_STAGE2_RERANK_WARMUP_ENABLED`、`PDF_QA_WARMUP_ENABLED`、`PATENT_PLANNING_HOT_POOL_WARMUP_ENABLED` | 写死关闭 | 所有预热能力后续本地部署不需要。 |
| `FASTQA_STAGE2_*_WARM_INTERVAL_SECONDS`、`FASTQA_STAGE2_*_WARM_TIMEOUT_SECONDS`、`FASTQA_STAGE2_*_WARM_JITTER_SECONDS`、`FASTQA_STAGE2_BOOTSTRAP_WARM_*`、`FASTQA_STAGE2_WARM_ACTIVE_*`、`PATENT_PLANNING_HOT_POOL_WARM_*` | 不保留 | 预热关闭后对应参数没有运行期配置价值。 |

### 布尔开关写死清单

这些开关后续精简时不再作为运行配置暴露。除“所有预热能力”固定关闭外，其余已经确认不可关闭，在代码中固定为启用。注意只写死开关状态，不要顺手写死它们依赖的连接、容量、超时、worker 数或 endpoint。

| 服务/能力 | 退役配置开关 | 写死值 | 仍需保留的相关配置 |
| --- | --- | --- | --- |
| Redis 基础设施 | `REDIS_ENABLED`、`PATENT_REDIS_ENABLED` | 启用 | `REDIS_URL`、`REDIS_HOST`、`REDIS_PORT`、`REDIS_USERNAME`、`REDIS_PASSWORD`、`REDIS_DB`、`REDIS_KEY_PREFIX`、`REDIS_SOCKET_CONNECT_TIMEOUT_SEC`、`REDIS_SOCKET_TIMEOUT_SEC`，以及 patent Redis 连接和 namespace 参数。 |
| MinIO 下载代理 | `MINIO_USE_PROXY` | 启用 | `MINIO_ENDPOINT`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`MINIO_BUCKET`、`MINIO_SECURE`、`MINIO_REGION`、`MINIO_DOWNLOAD_EXPIRES`。 |
| 聊天持久化 | `CHAT_PERSIST_ENABLED` | 启用 | 会话存储目标、JSON/base dir、public-service authority 目标等持久化依赖配置。 |
| 聊天异步持久化 | `CHAT_PERSIST_ASYNC` | 启用 | `CHAT_PERSIST_ASYNC_WORKERS`。 |
| 上传文件后台处理 | `UPLOAD_FILE_PROCESSING_ENABLED` | 启用 | `UPLOAD_PROCESSING_WORKER_MAX_WORKERS`、`UPLOAD_PROCESSING_MAX_PDF_PAGES`、`UPLOAD_PROCESSING_POLL_INTERVAL_MS`、`UPLOAD_PROCESSING_RECOVERY_SCAN_LIMIT`。 |
| 上传 PDF QA sidecar | `UPLOAD_QA_USE_SIDECAR` | 启用 | `UPLOAD_QA_SIDECAR_MODE`、`PDFQA_SIDECAR_BASE_URL_INTERNAL`、`PDFQA_SIDECAR_SELF_PORT`、`UPLOAD_QA_FIRST_TOKEN_TIMEOUT_SEC`。 |
| Gateway admission | `GATEWAY_ADMISSION_ENABLED` | 启用 | `INTERACTIVE_EXECUTION_MAX_CONCURRENT`、`INTERACTIVE_EXECUTION_PER_USER_MAX_ACTIVE`、`INTERACTIVE_EXECUTION_FAST_OR_PATENT_MAX_CONCURRENT`、`INTERACTIVE_EXECUTION_THINKING_MAX_CONCURRENT`、queue TTL/size 和 Redis 连接。 |
| Gateway admission dispatcher / worker | `GATEWAY_ADMISSION_DISPATCHER_ENABLED`、`GATEWAY_ADMISSION_WORKER_ENABLED` | 启用 | admission worker/runtime role、queue TTL/size、容量数值、Redis 连接；`GATEWAY_ADMISSION_WORKER_ENABLED` 是顶层启动门禁，删除配置前必须把启动脚本默认行为改成启用。 |
| Patent LLM 共享 HTTP 池 | `PATENT_LLM_HTTP_SHARED_POOL_ENABLED` | 启用 | `PATENT_LLM_HTTP_*_TIMEOUT_SECONDS`、`PATENT_LLM_HTTP_MAX_CONNECTIONS`、`PATENT_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS`、keepalive/pool 参数。 |
| Patent planning hot pool | `PATENT_PLANNING_HOT_POOL_ENABLED` | 启用 | `PATENT_PLANNING_HOT_POOL_LANE_COUNT`、`PATENT_PLANNING_HOT_POOL_LANE_DEGRADED_AFTER_SECONDS`、上游 HTTP timeout/keepalive 参数；warm interval、warm timeout、warm jitter、warm active window 属于预热参数，不再保留配置。 |
| Patent planning upstream gate | `PATENT_PLANNING_UPSTREAM_GATE_ENABLED` | 启用 | `PATENT_PLANNING_UPSTREAM_GATE_LIMIT` 及相关 poll/容量参数。 |
| fastQA 图谱主路径 | `FASTQA_GRAPH_KB_ENABLED`、`FASTQA_GRAPH_KB_V2_ENABLED`、`FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED` | 启用 | `FASTQA_NEO4J_*`、`NEO4J_*` 连接信息、`FASTQA_GRAPH_KB_TIMEOUT_MS`、`FASTQA_GRAPH_KB_MAX_ROWS`、`FASTQA_GRAPH_MAX_DOI_CANDIDATES` 等图谱连接和规模参数。 |
| fastQA / patent rerank 主流程 | `QA_RETRIEVAL_RERANK_ENABLED`、`PATENT_STAGE2_RERANK_ENABLED` | 启用 | 统一 `RERANK_*` endpoint 配置，以及 `QA_RETRIEVAL_RERANK_CANDIDATES`、`PATENT_STAGE2_RERANK_CANDIDATES`、`PATENT_STAGE2_RERANK_TOP_PATENTS` 等检索规模参数。 |
| 所有预热能力 | `FASTQA_STAGE2_CHAT_WARMUP_ENABLED`、`FASTQA_STAGE2_RERANK_WARMUP_ENABLED`、`PDF_QA_WARMUP_ENABLED`、`PATENT_PLANNING_HOT_POOL_WARMUP_ENABLED` 等 warmup 开关 | 关闭 | 后期本地部署不需要预热，所有预热开关写死关闭；对应 `FASTQA_STAGE2_*_WARM_INTERVAL_SECONDS`、`FASTQA_STAGE2_*_WARM_TIMEOUT_SECONDS`、`FASTQA_STAGE2_*_WARM_JITTER_SECONDS`、`FASTQA_STAGE2_BOOTSTRAP_WARM_*`、`FASTQA_STAGE2_WARM_ACTIVE_*`、`PATENT_PLANNING_HOT_POOL_WARM_*` 等预热参数不再保留配置。`FASTQA_STAGE2_CHAT_WARMUP_ENABLED` 当前未出现在 `resource/config`，但代码会读取，仍按写死关闭处理。 |

`PATENT_REDIS_ENABLED` 是 patent 自己的 Redis 启用开关，已确认不可关闭，和 `REDIS_ENABLED` 同批写死启用；仍保留 patent Redis 连接和 namespace 参数。

其余配置才进入“可写死/可删除”排查：如果配置值等于代码默认值，可优先考虑删配置；如果配置值覆盖代码默认值，只有确认该值不再需要部署差异后，才考虑把当前值写入代码默认值再删配置。

## 读配置入口

| 服务/层 | 代码位置 | 作用 |
| --- | --- | --- |
| 公共 shell loader | `scripts/env_file_loader.sh:13` | 按冒号分隔读取 env 文件，保留原始进程环境变量优先级。 |
| 总控脚本 | `scripts/_service_common.sh:17` | 拼接 shared env 文件；为各服务设置 service root；启动/状态脚本使用共享端口默认值。 |
| gateway | `gateway/app/core/env_loader.py:100` | 加载 legacy gateway env、resource shared env、resource service env。 |
| gateway settings | `gateway/app/core/config.py:113` | 集中读取 gateway、Redis、后端 URL、admission、routing 等运行配置。 |
| public-service | `public-service/backend/app/core/env_loader.py:82` | 加载 legacy public-service env、resource shared env、resource service env。 |
| public-service settings | `public-service/backend/app/core/config.py:191` | 集中读取 API、MySQL、Redis、MinIO、Neo4j、路径和会话配置。 |
| fastQA | `fastQA/app/core/env_loader.py:128` | 加载 legacy fastQA env、resource shared env、resource service env。 |
| fastQA settings | `fastQA/app/core/config.py:272` | 集中读取 FastAPI、MySQL、Redis、MinIO、Neo4j、graph KB、stage2 hot pool、路径等配置。 |
| highThinkingQA | `highThinkingQA/env_loader.py:130` | 加载 legacy highThinkingQA env、resource shared env、resource service env。 |
| highThinkingQA settings | `highThinkingQA/config.py:242` | 集中读取模型、OCR、embedding、chunk/retrieval、Gunicorn、SSE、路径等配置。 |
| patent | `patent/config.py:77` | 加载 legacy patent env、resource shared env、resource service env。 |
| patent settings | `patent/config.py:251` | 集中读取 Patent HTTP/Gunicorn、Redis、authority、graph KB、LLM HTTP、hot pool 等配置。 |
| patent start script | `patent/scripts/start_gunicorn.sh:21` | 拼接 PATENT_ENV_FILES，并从 Redis 公共配置合成 PATENT_REDIS_URL。 |

## 扫描结论

- `resource/config` 中共读取到 508 条配置记录，382 个不同变量。
- 有直接生产代码/启动脚本引用的变量：354 个。
- 未命中直接字符串引用的变量：28 个。这类配置可能是模板、迁移遗留、只由外部进程消费、动态拼接 env key 使用，或通过尚未纳入扫描的服务使用。
- 自动聚合统计中，有代码默认值且至少一个非 secret 配置值与某处代码默认值相同的变量：176 个。该数字只表示“可优先排查”，不是删除数量。
- 自动聚合统计中，有代码默认值但至少一个配置文件值与某处代码默认值不同的变量：83 个。该数字只提示存在环境差异风险，需要按服务和配置条目复核；`.env.example` 仅表示模板差异。

## 与代码默认值相同的自动排查项（2026-05-10 历史快照）

下表只列 2026-05-10 扫描时“至少有一个配置值与某处代码默认值相同”的自动候选。它只能作为历史排查入口，不能逐行直接删除；需要先套用“最终精简策略”的不写死规则，再按服务、配置文件条目、加载顺序和当前运行值逐项复核。`.env.example` 模板值不应作为当前运行配置参与删除决策。

| 变量 | 配置位置和值 | 代码默认值/位置 |
| --- | --- | --- |
| `APP_ENV` | `resource/config/services/fastQA/config.shared.env:4` = `development`<br>`resource/config/services/highThinkingQA/config.shared.env:58` = `dev`<br>`resource/config/services/public-service/config.shared.env:4` = `development` | "development"<br>"dev"<br>""<br>`fastQA/app/core/config.py:284` default="development"<br>`highThinkingQA/config.py:326` default="dev"<br>`highThinkingQA/config.py:347` default="dev"<br>+6 more |
| `APP_LOG_LEVEL` | `resource/config/services/highThinkingQA/config.env.example:16` = `INFO`<br>`resource/config/services/highThinkingQA/config.shared.env:59` = `INFO` | "INFO"<br>`highThinkingQA/config.py:329` default="INFO"<br>`highThinkingQA/config.py:427` |
| `APP_PORT` | `resource/config/services/fastQA/config.env.example:4` = `8009`<br>`resource/config/services/highThinkingQA/config.env.example:15` = `8008` | 8008<br>"8008"<br>8009<br>`fastQA/scripts/start_gunicorn.sh:33` default=8008<br>`fastQA/scripts/start_gunicorn.sh:34`<br>`fastQA/scripts/start_gunicorn.sh:40`<br>+15 more |
| `ASK_STREAM_MAX_CONCURRENT` | `resource/config/services/fastQA/config.shared.env:10` = `20`<br>`resource/config/services/highThinkingQA/config.shared.env:70` = `20` | 20<br>5<br>`fastQA/app/core/config.py:364` default=20<br>`highThinkingQA/config.py:331` default=5<br>`highThinkingQA/config.py:429`<br>+3 more<br>最终策略：ask stream 并发上限属于后端最大请求数，不写死，保留配置。 |
| `ASK_TIMEOUT_SECONDS` | `resource/config/services/highThinkingQA/config.shared.env:72` = `1800` | 1800<br>`highThinkingQA/config.py:333` default=1800<br>`highThinkingQA/config.py:431`<br>`highThinkingQA/server_fastapi/app.py:75`<br>+3 more |
| `BACKEND_CORS_ORIGINS` | `resource/config/services/fastQA/config.shared.env:7` = `*` | "*"<br>`fastQA/app/core/config.py:276` default="*"<br>`public-service/backend/app/core/config.py:193` default="*" |
| `CHAT_JSON_BASE_DIR` | `resource/config/services/fastQA/config.shared.env:43` = `/home/cqy/worktrees/highThinking/resource/fastqa/data/conversations`<br>`resource/config/services/highThinkingQA/config.shared.env:54` = `data/conversations`<br>`resource/config/services/public-service/config.shared.env:16` = `data/conversations` | config.CHAT_JSON_BASE_DIR<br>"data/conversations"<br>`fastQA/app/core/config.py:447`<br>`highThinkingQA/config.py:312`<br>`highThinkingQA/config.py:423`<br>+3 more |
| `CHAT_JSON_STORAGE_PREFIX` | `resource/config/services/highThinkingQA/config.shared.env:55` = `conversations` | "conversations"<br>`highThinkingQA/server/services/conversation/chat_json_store.py:41` default="conversations"<br>`public-service/backend/app/modules/conversation/json_store.py:48` default="conversations" |
| `CHAT_PERSIST_ASYNC_WORKERS` | `resource/config/services/highThinkingQA/config.shared.env:76` = `4` | "4"<br>4<br>`fastQA/app/services/ordered_dispatcher.py:77` default="4"<br>`highThinkingQA/config.py:337` default=4<br>`highThinkingQA/config.py:435`<br>+2 more |
| `CHECKER_MODEL` | `resource/config/services/highThinkingQA/config.shared.env:30` = `qwen3.5-plus` | "qwen3.5-plus"<br>`highThinkingQA/agent_core/checker.py:309`<br>`highThinkingQA/agent_core/reviser.py:88`<br>`highThinkingQA/config.py:308` default="qwen3.5-plus"<br>+1 more |
| `CHROMA_COLLECTION_NAME` | `resource/config/services/highThinkingQA/config.shared.env:52` = `lfp_papers` | "lfp_papers"<br>`highThinkingQA/config.py:291` default="lfp_papers"<br>`highThinkingQA/config.py:400`<br>`highThinkingQA/ingest/vector_store.py:58`<br>+5 more |
| `CONVERSATION_ASSISTANT_WRITE_TARGET` | `resource/config/services/highThinkingQA/config.shared.env:78` = `public_service` | "legacy"<br>"public_service"<br>`fastQA/app/core/config.py:112` default="legacy"<br>`highThinkingQA/config.py:87` default="public_service"<br>`highThinkingQA/config.py:443`<br>+2 more |
| `CONVERSATION_DETAIL_CACHE_TOUCH_ON_HIT` | `resource/config/services/public-service/config.shared.env:44` = `1` | "1"<br>`public-service/backend/app/modules/conversation/cache.py:28` default="1"<br>`public-service/backend/app/modules/system/service.py:47` default="1" |
| `CONVERSATION_DETAIL_CACHE_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:43` = `30` | "30"<br>`public-service/backend/app/modules/conversation/cache.py:20` default="30"<br>`public-service/backend/app/modules/system/service.py:46` default="30" |
| `CONVERSATION_LIST_CACHE_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:42` = `60` | "60"<br>`public-service/backend/app/modules/conversation/cache.py:12` default="60"<br>`public-service/backend/app/modules/system/service.py:45` default="60" |
| `CONVERSATION_LIST_RECENT_PAGES_LIMIT` | `resource/config/services/public-service/config.shared.env:46` = `8` | "8"<br>`public-service/backend/app/modules/conversation/cache.py:49` default="8"<br>`public-service/backend/app/modules/system/service.py:49` default="8" |
| `CONVERSATION_LIST_RECENT_PAGES_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:45` = `900` | "900"<br>`public-service/backend/app/modules/conversation/cache.py:41` default="900"<br>`public-service/backend/app/modules/system/service.py:48` default="900" |
| `CONVERSATION_LOCK_RETRY_INTERVAL_MS` | `resource/config/services/public-service/config.shared.env:66` = `100` | 100<br>`public-service/backend/app/modules/conversation/json_store.py:39` default=100 |
| `CONVERSATION_LOCK_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:64` = `30` | 30<br>`public-service/backend/app/modules/conversation/json_store.py:37` default=30 |
| `CONVERSATION_LOCK_WAIT_SECONDS` | `resource/config/services/public-service/config.shared.env:65` = `10` | 10<br>`public-service/backend/app/modules/conversation/json_store.py:38` default=10 |
| `CORS_ORIGINS` | `resource/config/services/highThinkingQA/config.env.example:17` = `*`<br>`resource/config/services/highThinkingQA/config.shared.env:69` = `*` | "*"<br>`highThinkingQA/config.py:339` default="*"<br>`highThinkingQA/config.py:437`<br>`highThinkingQA/server_fastapi/app.py:81`<br>+1 more |
| `DASHSCOPE_BASE_URL` | `resource/config/shared/model-endpoints.shared.env:16` = `https://dashscope.aliyuncs.com/compatible-mode/v1` | "https://dashscope.aliyuncs.com/compatible-mode/v1"<br>""<br>`fastQA/app/core/runtime.py:468`<br>`fastQA/app/modules/generation_pipeline/query_expander.py:38`<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:82`<br>+12 more |
| `DASHSCOPE_MODEL` | `resource/config/shared/model-endpoints.shared.env:17` = `deepseek-v3.1` | "qwen-plus"<br>"unknown"<br>"deepseek-v3.1"<br>""<br>`fastQA/app/modules/generation_pipeline/query_expander.py:41` default="qwen-plus"<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:85`<br>`fastQA/app/modules/qa_cache/stage1_cache.py:97` default="unknown"<br>+10 more |
| `DELETED_FILE_CLEANUP_RECONCILE_LIMIT` | `resource/config/services/public-service/config.shared.env:63` = `3` | "3"<br>`public-service/backend/app/modules/conversation/service.py:768` default="3" |
| `EMBEDDING_API_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:35` = `120` | "120"<br>"20"<br>`fastQA/app/modules/microscopic_runtime/embedding_client.py:19` default="120"<br>`patent/server/patent/runtime.py:326` default="20" |
| `EMBEDDING_DIMENSIONS` | `resource/config/services/highThinkingQA/config.shared.env:18` = `2048` | 2048<br>`highThinkingQA/config.py:275` default=2048<br>`highThinkingQA/config.py:391`<br>`highThinkingQA/ingest/embedder.py:91`<br>+2 more |
| `EMBEDDING_MODEL` | `resource/config/services/highThinkingQA/config.shared.env:17` = `text-embedding-v4`<br>`resource/config/shared/model-endpoints.shared.env:30` = `bge-local` | "text-embedding-v4"<br>`highThinkingQA/config.py:273` default="text-embedding-v4"<br>`highThinkingQA/config.py:389`<br>`highThinkingQA/ingest/embedder.py:132` |
| `EMBED_API_RPM` | `resource/config/services/highThinkingQA/config.shared.env:40` = `1800` | 1800<br>`highThinkingQA/config.py:298` default=1800<br>`highThinkingQA/config.py:407` |
| `EMBED_BATCH_SIZE` | `resource/config/services/highThinkingQA/config.shared.env:39` = `10` | 10<br>`highThinkingQA/config.py:297` default=10<br>`highThinkingQA/config.py:406`<br>`highThinkingQA/ingest/embedder.py:27` |
| `EMBED_CONCURRENCY` | `resource/config/services/highThinkingQA/config.shared.env:42` = `2` | 2<br>`highThinkingQA/config.py:300` default=2<br>`highThinkingQA/config.py:409`<br>`highThinkingQA/ingest/pipeline.py:6`<br>+1 more |
| `EMBED_MAX_CONCURRENT_REQUESTS` | `resource/config/services/highThinkingQA/config.shared.env:43` = `4` | 4<br>`highThinkingQA/config.py:301` default=4<br>`highThinkingQA/config.py:410`<br>`highThinkingQA/ingest/embedder.py:30` |
| `EMBED_MAX_INPUT_TOKENS` | `resource/config/services/highThinkingQA/config.shared.env:44` = `8000` | 8000<br>`highThinkingQA/config.py:302` default=8000<br>`highThinkingQA/config.py:411`<br>`highThinkingQA/ingest/embedder.py:63`<br>+1 more |
| `EMBED_MAX_RETRIES` | `resource/config/services/highThinkingQA/config.shared.env:45` = `5` | 5<br>`highThinkingQA/config.py:303` default=5<br>`highThinkingQA/config.py:412`<br>`highThinkingQA/ingest/embedder.py:126` |
| `EMBED_QUEUE_SIZE` | `resource/config/services/highThinkingQA/config.shared.env:46` = `200` | 200<br>`highThinkingQA/config.py:304` default=200<br>`highThinkingQA/config.py:413`<br>`highThinkingQA/ingest/pipeline.py:6`<br>+1 more |
| `FASTQA_GRAPH_KB_MAX_ROWS` | `resource/config/services/fastQA/config.env.example:8` = `20`<br>`resource/config/services/fastQA/config.shared.env:16` = `20` | 20<br>`fastQA/app/core/config.py:350` default=20 |
| `FASTQA_GRAPH_KB_TIMEOUT_MS` | `resource/config/services/fastQA/config.env.example:7` = `3000`<br>`resource/config/services/fastQA/config.shared.env:15` = `3000` | 3000<br>`fastQA/app/core/config.py:349` default=3000 |
| `FASTQA_GRAPH_MAX_DOI_CANDIDATES` | `resource/config/services/fastQA/config.env.example:11` = `20`<br>`resource/config/services/fastQA/config.shared.env:19` = `20` | 20<br>`fastQA/app/core/config.py:358` default=20 |
| `FASTQA_NEO4J_DATABASE` | `resource/config/shared/graph.shared.env:3` = `neo4j` | "neo4j"<br>`fastQA/app/core/config.py:346` default="neo4j" |
| `FASTQA_NEO4J_USERNAME` | `resource/config/shared/graph.shared.env:2` = `neo4j` | "neo4j"<br>`fastQA/app/core/config.py:342` default="neo4j" |
| `FASTQA_PORT` | `resource/config/shared/infrastructure.shared.env:9` = `8008` | ${APP_PORT:-8008<br>${FASTAPI_PORT:-$APP_PORT<br>${BACKEND_PORT:-$FASTAPI_PORT<br>8008<br>`fastQA/app/core/config.py:287`<br>`fastQA/scripts/start_gunicorn.sh:41` default=${APP_PORT:-8008<br>`fastQA/scripts/start_gunicorn.sh:44` default=${FASTAPI_PORT:-$APP_PORT<br>+7 more |
| `FAST_BACKEND_BASE_URL` | `resource/config/shared/infrastructure.shared.env:18` = `http://127.0.0.1:8008` | "http://127.0.0.1:8008"<br>http://127.0.0.1:8008<br>`gateway/app/core/config.py:116` default="http://127.0.0.1:8008"<br>`gateway/scripts/run_gunicorn_foreground.sh:23` default=http://127.0.0.1:8008<br>`gateway/scripts/start_gunicorn.sh:31` default=http://127.0.0.1:8008 |
| `GATEWAY_ADMISSION_DISPATCHER_ENABLED` | `resource/config/services/gateway/config.shared.env:4` = `1` | admission_enabled<br>1<br>`gateway/app/core/config.py:159` default=admission_enabled<br>`gateway/scripts/run_admission_worker_foreground.sh:19` default=1<br>`gateway/scripts/start_admission_worker.sh:25` default=1<br>最终策略：admission dispatcher 已确认必开，开关写死启用；dispatcher/worker 运行参数、队列和容量数值继续保留配置。 |
| `GATEWAY_ADMISSION_ENABLED` | `resource/config/services/gateway/config.shared.env:3` = `1` | False<br>1<br>`gateway/app/core/config.py:123` default=False<br>`gateway/scripts/run_admission_worker_foreground.sh:18` default=1<br>`gateway/scripts/start_admission_worker.sh:24` default=1<br>最终策略：gateway admission 已确认必开，开关写死启用；全局/单用户/各后端并发上限继续保留配置。 |
| `GATEWAY_HOST` | `resource/config/shared/infrastructure.shared.env:4` = `0.0.0.0` | "0.0.0.0"<br>`gateway/app/core/config.py:133` default="0.0.0.0" |
| `GATEWAY_PORT` | `resource/config/shared/infrastructure.shared.env:5` = `8101` | "8101"<br>8101<br>`gateway/app/core/config.py:134` default="8101"<br>`gateway/scripts/run_gunicorn_foreground.sh:17` default=8101<br>`gateway/scripts/run_gunicorn_foreground.sh:35`<br>+7 more |
| `GUNICORN_KEEPALIVE` | `resource/config/services/highThinkingQA/config.shared.env:65` = `15` | 15<br>`highThinkingQA/config.py:367` default=15<br>`highThinkingQA/config.py:451`<br>`highThinkingQA/server_fastapi/gunicorn.conf.py:12` |
| `GUNICORN_MAX_REQUESTS` | `resource/config/services/highThinkingQA/config.shared.env:66` = `1000` | 1000<br>`highThinkingQA/config.py:368` default=1000<br>`highThinkingQA/config.py:452`<br>`highThinkingQA/server_fastapi/gunicorn.conf.py:13` |
| `GUNICORN_MAX_REQUESTS_JITTER` | `resource/config/services/highThinkingQA/config.shared.env:67` = `100` | 100<br>`highThinkingQA/config.py:369` default=100<br>`highThinkingQA/config.py:453`<br>`highThinkingQA/server_fastapi/gunicorn.conf.py:14` |
| `GUNICORN_THREADS` | `resource/config/services/highThinkingQA/config.env.example:20` = `8`<br>`resource/config/services/highThinkingQA/config.shared.env:63` = `8` | 8<br>`highThinkingQA/config.py:365` default=8<br>`highThinkingQA/config.py:449`<br>`highThinkingQA/server_fastapi/gunicorn.conf.py:10` |
| `GUNICORN_TIMEOUT` | `resource/config/services/highThinkingQA/config.env.example:21` = `1800`<br>`resource/config/services/highThinkingQA/config.shared.env:64` = `1800` | 1800<br>`highThinkingQA/config.py:366` default=1800<br>`highThinkingQA/config.py:450`<br>`highThinkingQA/server_fastapi/gunicorn.conf.py:11` |
| `GUNICORN_WORKER_CLASS` | `resource/config/services/highThinkingQA/config.env.example:18` = `uvicorn.workers.UvicornWorker`<br>`resource/config/services/highThinkingQA/config.shared.env:61` = `uvicorn.workers.UvicornWorker` | "uvicorn.workers.UvicornWorker"<br>`highThinkingQA/config.py:363` default="uvicorn.workers.UvicornWorker"<br>`highThinkingQA/config.py:447`<br>`highThinkingQA/server_fastapi/gunicorn.conf.py:8` |
| `HIGHTHINKINGQA_HOST` | `resource/config/shared/infrastructure.shared.env:11` = `0.0.0.0` | "0.0.0.0"<br>`highThinkingQA/config.py:327` default="0.0.0.0" |
| `HIGHTHINKINGQA_PORT` | `resource/config/shared/infrastructure.shared.env:12` = `8009` | "8008"<br>${APP_PORT:-8009<br>8009<br>`highThinkingQA/config.py:319` default="8008"<br>`highThinkingQA/scripts/start_fastapi_gunicorn.sh:39` default=${APP_PORT:-8009<br>`highThinkingQA/scripts/status_fastapi_gunicorn.sh:27` default=${APP_PORT:-8009<br>+3 more |
| `HT_QA_CACHE_EPOCH` | `resource/config/services/highThinkingQA/config.shared.env:88` = `0` | "0"<br>`highThinkingQA/server/services/stage_cache.py:93` default="0" |
| `HT_QA_CACHE_LOCK_TTL_SECONDS` | `resource/config/services/highThinkingQA/config.shared.env:94` = `30` | 30<br>`highThinkingQA/server/services/stage_cache.py:133` default=30 |
| `HT_QA_CACHE_WAIT_MS` | `resource/config/services/highThinkingQA/config.shared.env:93` = `400` | 400<br>`highThinkingQA/server/services/stage_cache.py:129` default=400 |
| `HT_QA_DECOMPOSE_CACHE_TTL_SECONDS` | `resource/config/services/highThinkingQA/config.shared.env:90` = `43200` | 43200<br>`highThinkingQA/server/services/stage_cache.py:117` default=43200 |
| `HT_QA_DIRECT_CACHE_TTL_SECONDS` | `resource/config/services/highThinkingQA/config.shared.env:89` = `43200` | 43200<br>`highThinkingQA/server/services/stage_cache.py:113` default=43200 |
| `HT_QA_RETRIEVE_CACHE_TTL_SECONDS` | `resource/config/services/highThinkingQA/config.shared.env:91` = `43200` | 43200<br>`highThinkingQA/server/services/stage_cache.py:121` default=43200 |
| `JWT_EXPIRE_SECONDS` | `resource/config/services/highThinkingQA/config.shared.env:81` = `86400`<br>`resource/config/services/public-service/config.shared.env:28` = `86400` | "86400"<br>86400<br>`highThinkingQA/server/services/auth_service.py:55` default="86400"<br>`patent/config.py:293` default=86400<br>`public-service/backend/app/modules/auth/service.py:211` default="86400"<br>最终策略：JWT / auth 策略不写死，保留配置。 |
| `LLM_MODEL` | `resource/config/services/highThinkingQA/config.env.example:6` = `qwen3-max`<br>`resource/config/services/highThinkingQA/config.shared.env:9` = `qwen3-max`<br>`resource/config/shared/model-endpoints.shared.env:5` = `deepseek-v3.1` | "qwen-plus"<br>"OPENAI_MODEL"<br>os.getenv("OPENAI_MODEL", os.getenv("DASHSCOPE_MODEL", "deepseek-v3.1"<br>"qwen3-max"<br>""<br>`fastQA/app/modules/generation_pipeline/query_expander.py:41` default="qwen-plus"<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:85` default="OPENAI_MODEL"<br>`fastQA/app/modules/qa_pdf/llm_factory.py:62` default=os.getenv("OPENAI_MODEL", os.getenv("DASHSCOPE_MODEL", "deepseek-v3.1"<br>+16 more |
| `LOGIN_FAILURE_LOCK_MINUTES` | `resource/config/services/highThinkingQA/config.shared.env:84` = `5`<br>`resource/config/services/public-service/config.shared.env:31` = `5` | "5"<br>`highThinkingQA/server/services/auth_service.py:89` default="5"<br>`public-service/backend/app/modules/auth/service.py:250` default="5" |
| `LOGIN_FAILURE_LOCK_THRESHOLD` | `resource/config/services/highThinkingQA/config.shared.env:83` = `5`<br>`resource/config/services/public-service/config.shared.env:30` = `5` | "5"<br>`highThinkingQA/server/services/auth_service.py:88` default="5"<br>`public-service/backend/app/modules/auth/service.py:249` default="5" |
| `LOG_LEVEL` | `resource/config/services/fastQA/config.shared.env:5` = `INFO` | "INFO"<br>`fastQA/app/main.py:40` default="INFO"<br>`patent/server_fastapi/app.py:727` default="INFO" |
| `MAX_CHECK_LOOPS` | `resource/config/services/highThinkingQA/config.shared.env:31` = `2` | 2<br>`highThinkingQA/agent_core/graph.py:393`<br>`highThinkingQA/agent_core/graph.py:423`<br>`highThinkingQA/config.py:309` default=2<br>+1 more |
| `MAX_CHUNK_TOKENS` | `resource/config/services/highThinkingQA/config.shared.env:23` = `4000` | 4000<br>`highThinkingQA/config.py:286` default=4000<br>`highThinkingQA/config.py:395`<br>`highThinkingQA/ingest/chunker.py:258`<br>+5 more |
| `MAX_PDF_PAGES` | `resource/config/services/highThinkingQA/config.shared.env:85` = `50`<br>`resource/config/services/public-service/config.shared.env:69` = `50` | "50"<br>`fastQA/app/modules/documents/service.py:99` default="50"<br>`highThinkingQA/server/services/documents_service.py:28` default="50"<br>`public-service/backend/app/modules/documents/service.py:118` default="50" |
| `MINIO_BUCKET` | `resource/config/services/highThinkingQA/config.env.example:34` = `agentcode`<br>`resource/config/shared/infrastructure.shared.env:40` = `agentcode` | "agentcode"<br>""<br>`fastQA/app/core/config.py:323` default="agentcode"<br>`fastQA/app/modules/storage/paper_storage.py:110` default=""<br>`highThinkingQA/server/storage/minio_backend.py:29` default=""<br>+5 more |
| `MINIO_DOWNLOAD_EXPIRES` | `resource/config/shared/infrastructure.shared.env:43` = `3600` | "3600"<br>`highThinkingQA/server/storage/file_delivery_service.py:63` default="3600"<br>`public-service/backend/app/modules/conversation/service.py:3416` default="3600" |
| `MINIO_SECURE` | `resource/config/services/highThinkingQA/config.env.example:35` = `0`<br>`resource/config/shared/infrastructure.shared.env:41` = `0` | False<br>"0"<br>`fastQA/app/core/config.py:324` default=False<br>`fastQA/app/modules/storage/paper_storage.py:111` default="0"<br>`fastQA/app/modules/storage/upload_materializer.py:54` default="0"<br>+6 more |
| `MINIO_USE_PROXY` | `resource/config/shared/infrastructure.shared.env:42` = `1` | "1"<br>`highThinkingQA/server/storage/file_delivery_service.py:61`<br>`public-service/backend/app/modules/conversation/service.py:3414` default="1"<br>最终策略：MinIO 代理下载已确认必开，开关写死启用；MinIO endpoint、bucket、凭据、secure、region、下载过期时间继续保留配置。 |
| `MYSQL_HOST` | `resource/config/services/highThinkingQA/config.env.example:24` = `127.0.0.1`<br>`resource/config/shared/infrastructure.shared.env:29` = `127.0.0.1` | "127.0.0.1"<br>""<br>`fastQA/app/core/config.py:315` default="127.0.0.1"<br>`highThinkingQA/server/database/connection.py:39` default=""<br>`public-service/backend/app/core/config.py:227` default="127.0.0.1" |
| `MYSQL_PORT` | `resource/config/services/highThinkingQA/config.env.example:25` = `3306`<br>`resource/config/shared/infrastructure.shared.env:30` = `3306` | 3306<br>`fastQA/app/core/config.py:316` default=3306<br>`highThinkingQA/server/database/connection.py:40`<br>`public-service/backend/app/core/config.py:228` default=3306 |
| `MYSQL_USER` | `resource/config/services/highThinkingQA/config.env.example:26` = `root` | "root"<br>""<br>`fastQA/app/core/config.py:317` default="root"<br>`highThinkingQA/server/database/connection.py:41` default=""<br>`highThinkingQA/server/database/connection.py:48`<br>+1 more |
| `NEO4J_USERNAME` | `resource/config/shared/graph.shared.env:13` = `neo4j` | "neo4j"<br>`fastQA/app/core/config.py:342` default="neo4j"<br>`public-service/backend/app/core/config.py:250` default="neo4j"<br>`public-service/backend/app/modules/retrieval/service.py:44` default="neo4j" |
| `NUM_SUB_QUESTIONS` | `resource/config/services/highThinkingQA/config.shared.env:29` = `5` | 5<br>`highThinkingQA/agent_core/decomposer.py:38`<br>`highThinkingQA/agent_core/graph.py:421`<br>`highThinkingQA/config.py:307` default=5<br>+1 more |
| `OCR_CONCURRENCY` | `resource/config/services/highThinkingQA/config.shared.env:34` = `40` | 40<br>`highThinkingQA/config.py:292` default=40<br>`highThinkingQA/config.py:401`<br>`highThinkingQA/ingest/pipeline.py:289` |
| `OCR_MAX_CONCURRENT_REQUESTS` | `resource/config/services/highThinkingQA/config.shared.env:35` = `40` | 40<br>`highThinkingQA/config.py:293` default=40<br>`highThinkingQA/config.py:402`<br>`highThinkingQA/ingest/pdf_parser.py:12`<br>+2 more |
| `OCR_MAX_RETRIES` | `resource/config/services/highThinkingQA/config.shared.env:37` = `5` | 5<br>`highThinkingQA/config.py:295` default=5<br>`highThinkingQA/config.py:404`<br>`highThinkingQA/ingest/pdf_parser.py:157` |
| `OCR_MODEL` | `resource/config/services/highThinkingQA/config.shared.env:20` = `qwen-vl-ocr-2025-11-20`<br>`resource/config/shared/model-endpoints.shared.env:59` = `qwen-vl-ocr` | "qwen-vl-ocr-2025-11-20"<br>`highThinkingQA/config.py:284` default="qwen-vl-ocr-2025-11-20"<br>`highThinkingQA/config.py:393`<br>`highThinkingQA/ingest/pdf_parser.py:120`<br>+1 more |
| `OCR_PAGES_PER_BATCH` | `resource/config/services/highThinkingQA/config.shared.env:36` = `3` | 3<br>`highThinkingQA/config.py:294` default=3<br>`highThinkingQA/config.py:403`<br>`highThinkingQA/ingest/pdf_parser.py:7`<br>+4 more |
| `OCR_RETRY_BASE` | `resource/config/services/highThinkingQA/config.shared.env:38` = `3` | 3<br>`highThinkingQA/config.py:296` default=3<br>`highThinkingQA/config.py:405`<br>`highThinkingQA/ingest/pdf_parser.py:158` |
| `OUTBOX_MAX_ATTEMPTS` | `resource/config/services/public-service/config.shared.env:59` = `20` | 20<br>`highThinkingQA/server/services/conversation/chat_json_outbox_worker.py:48` default=20<br>`public-service/backend/app/modules/conversation/outbox_worker.py:49` default=20 |
| `OUTBOX_PROCESSING_TIMEOUT_SECONDS` | `resource/config/services/public-service/config.shared.env:62` = `120` | 120<br>`highThinkingQA/server/services/conversation/chat_json_outbox_worker.py:52`<br>`public-service/backend/app/modules/conversation/outbox_worker.py:52` default=120 |
| `OUTBOX_RETRY_BASE_SECONDS` | `resource/config/services/public-service/config.shared.env:60` = `2` | 2<br>`highThinkingQA/server/services/conversation/chat_json_outbox_worker.py:49` default=2<br>`public-service/backend/app/modules/conversation/outbox_worker.py:50` default=2 |
| `OUTBOX_RETRY_MAX_SECONDS` | `resource/config/services/public-service/config.shared.env:61` = `300` | 300<br>`highThinkingQA/server/services/conversation/chat_json_outbox_worker.py:50` default=300<br>`public-service/backend/app/modules/conversation/outbox_worker.py:51` default=300 |
| `OUTBOX_WORKER_BATCH_SIZE` | `resource/config/services/public-service/config.shared.env:57` = `100` | 100<br>`highThinkingQA/server/services/conversation/chat_json_outbox_worker.py:46` default=100<br>`public-service/backend/app/modules/conversation/outbox_worker.py:47` default=100 |
| `OUTBOX_WORKER_POLL_INTERVAL_MS` | `resource/config/services/public-service/config.shared.env:58` = `1000` | 1000<br>`highThinkingQA/server/services/conversation/chat_json_outbox_worker.py:47` default=1000<br>`public-service/backend/app/modules/conversation/outbox_worker.py:48` default=1000 |
| `PASSWORD_EXPIRE_DAYS` | `resource/config/services/highThinkingQA/config.shared.env:82` = `180`<br>`resource/config/services/public-service/config.shared.env:29` = `180` | "180"<br>`highThinkingQA/server/services/auth_service.py:87` default="180"<br>`public-service/backend/app/modules/auth/service.py:248` default="180" |
| `PATENT_ASK_EXECUTOR_MAX_WORKERS` | `resource/config/services/patent/config.shared.env:6` = `4` | 4<br>`patent/config.py:276` default=4 |
| `PATENT_ASK_STREAM_MAX_CONCURRENT` | `resource/config/services/patent/config.shared.env:5` = `8` | 8<br>`patent/config.py:275` default=8<br>最终策略：ask stream 并发上限属于后端最大请求数，不写死，保留配置。 |
| `PATENT_BACKEND_BASE_URL` | `resource/config/shared/infrastructure.shared.env:20` = `http://127.0.0.1:8010` | "http://127.0.0.1:8010"<br>http://127.0.0.1:8010<br>`gateway/app/core/config.py:119` default="http://127.0.0.1:8010"<br>`gateway/scripts/run_gunicorn_foreground.sh:25` default=http://127.0.0.1:8010<br>`gateway/scripts/start_gunicorn.sh:33` default=http://127.0.0.1:8010 |
| `PATENT_DURABLE_AUTHORITY_ENABLED` | `resource/config/services/patent/config.shared.env:31` = `true` | False<br>true<br>`patent/config.py:289` default=False<br>`patent/scripts/start.sh:26` default=true<br>`patent/scripts/start_gunicorn.sh:31` default=true |
| `PATENT_DURABLE_MODE_ENABLED` | `resource/config/services/patent/config.shared.env:30` = `true` | True<br>true<br>`patent/config.py:258` default=True<br>`patent/scripts/start.sh:25` default=true<br>`patent/scripts/start_gunicorn.sh:30` default=true |
| `PATENT_EMBEDDING_API_TIMEOUT_SECONDS` | `resource/config/services/patent/config.shared.env:33` = `20`<br>`resource/config/shared/model-endpoints.shared.env:42` = `20` | "20"<br>`patent/server/patent/runtime.py:326` default="20" |
| `PATENT_ENV` | `resource/config/services/patent/config.shared.env:1` = `dev` | "dev"<br>`patent/config.py:254` default="dev"<br>`public-service/backend/app/modules/conversation/service.py:424` default="dev" |
| `PATENT_GUNICORN_THREADS` | `resource/config/services/patent/config.shared.env:3` = `8` | 8<br>`patent/config.py:267` default=8 |
| `PATENT_GUNICORN_TIMEOUT` | `resource/config/services/patent/config.shared.env:4` = `120` | 120<br>`patent/config.py:268` default=120 |
| `PATENT_HOST` | `resource/config/shared/infrastructure.shared.env:13` = `0.0.0.0` | "0.0.0.0"<br>`patent/config.py:262` default="0.0.0.0" |
| `PATENT_NEO4J_DATABASE` | `resource/config/shared/graph.shared.env:6` = `neo4j` | "neo4j"<br>`patent/config.py:332` default="neo4j" |
| `PATENT_NEO4J_URL` | `resource/config/shared/graph.shared.env:4` = `bolt://127.0.0.1:8687` | "bolt://127.0.0.1:8687"<br>`patent/config.py:329` default="bolt://127.0.0.1:8687" |
| `PATENT_NEO4J_USERNAME` | `resource/config/shared/graph.shared.env:5` = `neo4j` | "neo4j"<br>`patent/config.py:330` default="neo4j" |
| `PATENT_OPENAI_TIMEOUT_SECONDS` | `resource/config/services/patent/config.shared.env:35` = `30` | "30"<br>30.0<br>`patent/server/patent/answering.py:959` default="30"<br>`patent/server/patent/hybrid_synthesis.py:286` default=30.0<br>`patent/server/patent/pdf_service.py:894` default=30.0<br>+2 more |
| `PATENT_PORT` | `resource/config/shared/infrastructure.shared.env:14` = `8010` | 8787<br>8010<br>`patent/config.py:263` default=8787<br>`patent/scripts/start.sh:24` default=8010<br>`patent/scripts/start_gunicorn.sh:29` default=8010<br>+11 more |
| `PATENT_REDIS_ENABLED` | `resource/config/services/patent/config.shared.env:28` = `true` | False<br>true<br>`patent/config.py:280` default=False<br>`patent/scripts/start.sh:29` default=true<br>`patent/scripts/start_gunicorn.sh:34` default=true<br>最终策略：patent Redis 已确认必开，开关写死启用；patent Redis 连接和 namespace 参数继续保留配置。 |
| `PATENT_REDIS_KEY_PREFIX` | `resource/config/services/patent/config.shared.env:29` = `patent` | "patent"<br>`patent/config.py:255` default="patent"<br>`public-service/backend/app/modules/conversation/service.py:425` default="patent" |
| `PATENT_STAGE2_ENTITY_LOCK_ENABLED` | `resource/config/services/patent/config.shared.env:9` = `true` | True<br>`patent/server/patent/stage2_controls.py:66` default=True |
| `PATENT_STAGE2_FORCE_KEYWORD_INJECTION` | `resource/config/services/patent/config.shared.env:8` = `true` | True<br>`patent/server/patent/stage2_controls.py:65` default=True |
| `PATENT_STAGE2_MAX_GLOBAL_PATENTS` | `resource/config/services/patent/config.shared.env:20` = `20` | 20<br>`patent/server/patent/stage2_controls.py:72` default=20 |
| `PATENT_STAGE2_MAX_RESULTS_PER_CLAIM` | `resource/config/services/patent/config.shared.env:19` = `5` | 5<br>`patent/server/patent/stage2_controls.py:71` default=5 |
| `PATENT_STAGE2_MIN_RESULTS_PER_CLAIM` | `resource/config/services/patent/config.shared.env:18` = `2` | 2<br>`patent/server/patent/stage2_controls.py:70` default=2 |
| `PATENT_STAGE2_RERANK_CANDIDATES` | `resource/config/services/patent/config.shared.env:11` = `80` | 80<br>`patent/server/patent/stage2_controls.py:68` default=80 |
| `PATENT_STAGE2_RERANK_ENABLED` | `resource/config/services/patent/config.shared.env:10` = `true` | True<br>`patent/server/patent/stage2_controls.py:67` default=True |
| `PATENT_STAGE2_RERANK_ENDPOINT_FAMILY` | `resource/config/services/patent/config.shared.env:17` = ``<br>`resource/config/shared/model-endpoints.shared.env:56` = `` | ""<br>`patent/server/patent/stage2_controls.py:81` default="" |
| `PATENT_STAGE2_RERANK_MODEL` | `resource/config/services/patent/config.shared.env:15` = `qwen3-vl-rerank`<br>`resource/config/shared/model-endpoints.shared.env:54` = `qwen3-vl-rerank` | "qwen3-vl-rerank"<br>""<br>`patent/server/patent/rerank_service.py:195` default="qwen3-vl-rerank"<br>`patent/server/patent/stage2_controls.py:78` default="" |
| `PATENT_STAGE2_RERANK_TOP_PATENTS` | `resource/config/services/patent/config.shared.env:12` = `20` | 20<br>`patent/server/patent/stage2_controls.py:69` default=20 |
| `PATENT_STAGE2_VALIDATION_ENABLED` | `resource/config/services/patent/config.shared.env:21` = `true` | True<br>`patent/server/patent/stage2_controls.py:73` default=True |
| `PATENT_STAGE4_MIN_CITATIONS` | `resource/config/services/patent/config.shared.env:26` = `10` | ""<br>10<br>`patent/server/patent/orchestrators/generation.py:156` default=""<br>`patent/server/patent/stages/synthesis.py:454` default=10 |
| `PATENT_STAGE4_REFERENCE_TOPK` | `resource/config/services/patent/config.shared.env:25` = `20` | ""<br>20<br>`patent/server/patent/orchestrators/generation.py:155` default=""<br>`patent/server/patent/stages/synthesis.py:453` default=20 |
| `PDF_QA_MAX_PDF_CHARS` | `resource/config/services/fastQA/config.shared.env:95` = `50000` | "50000"<br>default=12000<br>`fastQA/app/services/file_route_service.py:104` default="50000"<br>`fastQA/app/services/file_routes.py:181` default=default=12000 |
| `PDF_QA_TEMPERATURE` | `resource/config/services/fastQA/config.shared.env:106` = `0.5` | 0.5<br>`fastQA/app/modules/qa_pdf/llm_factory.py:64` default=0.5 |
| `PDF_QA_TOP_P` | `resource/config/services/fastQA/config.shared.env:107` = `0.95` | 0.95<br>`fastQA/app/modules/qa_pdf/llm_factory.py:65` default=0.95 |
| `PUBLIC_BACKEND_BASE_URL` | `resource/config/shared/infrastructure.shared.env:17` = `http://127.0.0.1:8102` | "http://127.0.0.1:8102"<br>http://127.0.0.1:8102<br>`gateway/app/core/config.py:117` default="http://127.0.0.1:8102"<br>`gateway/scripts/run_gunicorn_foreground.sh:22` default=http://127.0.0.1:8102<br>`gateway/scripts/start_gunicorn.sh:30` default=http://127.0.0.1:8102 |
| `PUBLIC_SERVICE_API_PREFIX` | `resource/config/services/public-service/config.shared.env:7` = `/api` | "/api"<br>`public-service/backend/app/core/config.py:223` default="/api" |
| `PUBLIC_SERVICE_APP_NAME` | `resource/config/services/public-service/config.shared.env:5` = `agentCode Public Service` | "agentCode Public Service"<br>`public-service/backend/app/core/config.py:218` default="agentCode Public Service" |
| `PUBLIC_SERVICE_DOCS_URL` | `resource/config/services/public-service/config.shared.env:8` = `/docs` | "/docs"<br>`public-service/backend/app/core/config.py:224` default="/docs" |
| `PUBLIC_SERVICE_HOST` | `resource/config/shared/infrastructure.shared.env:6` = `0.0.0.0` | "0.0.0.0"<br>`public-service/backend/app/core/config.py:221` default="0.0.0.0" |
| `PUBLIC_SERVICE_NEO4J_DATABASE` | `resource/config/shared/graph.shared.env:9` = `neo4j` | "neo4j"<br>`public-service/backend/app/core/config.py:254` default="neo4j" |
| `PUBLIC_SERVICE_NEO4J_USERNAME` | `resource/config/shared/graph.shared.env:8` = `neo4j` | "neo4j"<br>`public-service/backend/app/core/config.py:250` default="neo4j" |
| `PUBLIC_SERVICE_OPENAPI_URL` | `resource/config/services/public-service/config.shared.env:9` = `/openapi.json` | "/openapi.json"<br>`public-service/backend/app/core/config.py:225` default="/openapi.json" |
| `PUBLIC_SERVICE_PORT` | `resource/config/shared/infrastructure.shared.env:7` = `8102` | 8102<br>`public-service/backend/app/core/config.py:222` default=8102<br>`public-service/scripts/start_gunicorn.sh:25` default=8102<br>`public-service/scripts/start_gunicorn.sh:57`<br>+5 more |
| `QA_RETRIEVAL_RERANK_API_KEY` | `resource/config/services/fastQA/config.shared.env:49` = `` | ""<br>`fastQA/app/core/runtime.py:576` default=""<br>`fastQA/app/modules/microscopic_expert.py:157` default="" |
| `QA_RETRIEVAL_RERANK_CANDIDATES` | `resource/config/services/fastQA/config.shared.env:48` = `50` | "50"<br>`fastQA/app/modules/generation_pipeline/stage2_retrieval.py:77`<br>`fastQA/app/modules/qa_cache/stage2_cache.py:87` default="50" |
| `QA_RETRIEVAL_RERANK_MODEL` | `resource/config/shared/model-endpoints.shared.env:50` = `qwen3-vl-rerank` | "qwen3-vl-rerank"<br>`fastQA/app/core/runtime.py:591` default="qwen3-vl-rerank"<br>`fastQA/app/modules/microscopic_expert.py:167` default="qwen3-vl-rerank"<br>`fastQA/app/modules/qa_cache/stage2_cache.py:89` default="qwen3-vl-rerank" |
| `QA_RETRIEVAL_RERANK_TIMEOUT` | `resource/config/shared/model-endpoints.shared.env:51` = `20` | "20"<br>`fastQA/app/modules/microscopic_expert.py:169` default="20" |
| `QA_STAGE1_CACHE_TTL_SECONDS` | `resource/config/services/fastQA/config.shared.env:63` = `43200` | "43200"<br>"3600"<br>`fastQA/app/modules/qa_cache/stage1_cache.py:17` default="43200"<br>`public-service/backend/app/modules/system/service.py:42` default="3600" |
| `QA_STAGE25_CACHE_TTL_SECONDS` | `resource/config/services/fastQA/config.shared.env:65` = `43200` | "43200"<br>`fastQA/app/modules/qa_cache/stage25_cache.py:21` default="43200" |
| `QA_STAGE25_MD_CHUNKS_PER_DOI` | `resource/config/services/fastQA/config.shared.env:71` = `5` | "5"<br>`fastQA/app/modules/generation_pipeline/md_expansion.py:32`<br>`fastQA/app/modules/qa_cache/stage25_cache.py:55` default="5" |
| `QA_STAGE25_MD_GLOBAL_MAX_NEW_DOIS` | `resource/config/services/fastQA/config.shared.env:74` = `5` | "5"<br>`fastQA/app/modules/generation_pipeline/md_expansion.py:38`<br>`fastQA/app/modules/qa_cache/stage25_cache.py:58` default="5" |
| `QA_STAGE25_MD_GLOBAL_MIN_SCORE` | `resource/config/services/fastQA/config.shared.env:75` = `0` | "0"<br>`fastQA/app/modules/generation_pipeline/md_expansion.py:39` default="0"<br>`fastQA/app/modules/qa_cache/stage25_cache.py:59` default="0" |
| `QA_STAGE25_MD_GLOBAL_TOPK` | `resource/config/services/fastQA/config.shared.env:73` = `20` | "20"<br>`fastQA/app/modules/generation_pipeline/md_expansion.py:37`<br>`fastQA/app/modules/qa_cache/stage25_cache.py:57` default="20" |
| `QA_STAGE25_MD_MAX_DOIS` | `resource/config/services/fastQA/config.shared.env:70` = `20` | "20"<br>`fastQA/app/modules/generation_pipeline/md_expansion.py:30`<br>`fastQA/app/modules/qa_cache/stage25_cache.py:54` default="20" |
| `QA_STAGE2_CACHE_TTL_SECONDS` | `resource/config/services/fastQA/config.shared.env:64` = `43200` | "43200"<br>"1800"<br>`fastQA/app/modules/qa_cache/stage2_cache.py:25` default="43200"<br>`public-service/backend/app/modules/system/service.py:43` default="1800" |
| `QA_STAGE2_RETRIEVAL_VERSION` | `resource/config/services/fastQA/config.shared.env:62` = `1` | "1"<br>`fastQA/app/modules/qa_cache/stage2_cache.py:21` default="1" |
| `QA_STAGE3_CACHE_TTL_SECONDS` | `resource/config/services/fastQA/config.shared.env:69` = `43200` | "43200"<br>`fastQA/app/modules/qa_cache/stage3_cache.py:22` default="43200" |
| `QA_STAGE4_EMPTY_FACTS_FALLBACK_MODE` | `resource/config/services/fastQA/config.shared.env:86` = `restricted_synthesis` | "restricted_synthesis"<br>`fastQA/app/modules/generation_pipeline/synthesis_streaming.py:856` default="restricted_synthesis" |
| `QUERY_EXPANSION_MODEL` | `resource/config/services/fastQA/config.shared.env:23` = `qwen3-8b` | "qwen-plus"<br>"qwen3-8b"<br>`fastQA/app/modules/generation_pipeline/query_expander.py:41` default="qwen-plus"<br>`fastQA/app/modules/qa_cache/stage2_cache.py:91` default="qwen3-8b" |
| `QUOTA_ACTIVE_LIST_CACHE_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:39` = `300` | "300"<br>`public-service/backend/app/modules/quota/cache.py:25` default="300" |
| `QUOTA_ALL_LIST_CACHE_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:40` = `300` | "300"<br>`public-service/backend/app/modules/quota/cache.py:33` default="300" |
| `QUOTA_CACHE_EPOCH` | `resource/config/services/public-service/config.shared.env:37` = `0` | "0"<br>`public-service/backend/app/modules/quota/cache.py:13` default="0" |
| `QUOTA_CONFIG_CACHE_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:38` = `600` | "600"<br>`public-service/backend/app/modules/quota/cache.py:17` default="600" |
| `QUOTA_LOCK_RETRY_INTERVAL_MS` | `resource/config/services/public-service/config.shared.env:49` = `100` | "100"<br>`public-service/backend/app/modules/quota/service.py:321` default="100" |
| `QUOTA_LOCK_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:47` = `30` | "30"<br>`public-service/backend/app/modules/quota/service.py:307` default="30" |
| `QUOTA_LOCK_WAIT_SECONDS` | `resource/config/services/public-service/config.shared.env:48` = `10` | "10"<br>`public-service/backend/app/modules/quota/service.py:314` default="10" |
| `QUOTA_OVERRIDE_CACHE_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:41` = `600` | "600"<br>`public-service/backend/app/modules/quota/cache.py:41` default="600" |
| `REDIS_DB` | `resource/config/shared/infrastructure.shared.env:25` = `0` | 0<br>`fastQA/app/core/config.py:332` default=0<br>`gateway/app/core/config.py:151` default=0<br>`highThinkingQA/server/services/redis_client.py:137` default=0<br>+5 more<br>最终策略：Redis 配置不写死，保留配置。 |
| `REDIS_HOST` | `resource/config/shared/infrastructure.shared.env:23` = `127.0.0.1` | "127.0.0.1"<br>127.0.0.1<br>`fastQA/app/core/config.py:328` default="127.0.0.1"<br>`gateway/app/core/config.py:147` default="127.0.0.1"<br>`highThinkingQA/server/services/redis_client.py:134` default="127.0.0.1"<br>+5 more<br>最终策略：Redis 配置不写死，保留配置。 |
| `REDIS_KEY_PREFIX` | `resource/config/services/fastQA/config.shared.env:111` = `fastqa`<br>`resource/config/services/gateway/config.shared.env:9` = `gateway`<br>`resource/config/services/highThinkingQA/config.shared.env:87` = `highthinkingqa`<br>`resource/config/services/public-service/config.shared.env:34` = `public_service` | "fastqa"<br>"gateway"<br>"highthinkingqa"<br>"agentcode"<br>`fastQA/app/core/config.py:333` default="fastqa"<br>`gateway/app/core/config.py:152` default="gateway"<br>`highThinkingQA/server/services/redis_client.py:145` default="highthinkingqa"<br>+1 more<br>最终策略：Redis 配置不写死，保留配置。 |
| `REDIS_PORT` | `resource/config/shared/infrastructure.shared.env:24` = `6379` | 6379<br>`fastQA/app/core/config.py:329` default=6379<br>`gateway/app/core/config.py:148` default=6379<br>`highThinkingQA/server/services/redis_client.py:135` default=6379<br>+5 more<br>最终策略：Redis 配置不写死，保留配置。 |
| `REDIS_SOCKET_CONNECT_TIMEOUT_SEC` | `resource/config/shared/infrastructure.shared.env:26` = `2` | 2<br>`fastQA/app/core/config.py:334` default=2<br>`gateway/app/core/config.py:153` default=2<br>`highThinkingQA/server/services/redis_client.py:146` default=2<br>+1 more<br>最终策略：Redis 配置不写死，保留配置。 |
| `REDIS_SOCKET_TIMEOUT_SEC` | `resource/config/shared/infrastructure.shared.env:27` = `2` | 2<br>`fastQA/app/core/config.py:335` default=2<br>`gateway/app/core/config.py:154` default=2<br>`highThinkingQA/server/services/redis_client.py:147` default=2<br>+1 more<br>最终策略：Redis 配置不写死，保留配置。 |
| `RETRIEVAL_PIPELINE_BATCH_SIZE` | `resource/config/services/highThinkingQA/config.shared.env:28` = `2` | 2<br>`highThinkingQA/agent_core/graph.py:424`<br>`highThinkingQA/config.py:306` default=2<br>`highThinkingQA/config.py:415` |
| `RETRIEVAL_TOP_K` | `resource/config/services/highThinkingQA/config.shared.env:27` = `3` | 3<br>`highThinkingQA/agent_core/graph.py:422`<br>`highThinkingQA/config.py:305` default=3<br>`highThinkingQA/config.py:414`<br>+6 more |
| `SEMANTIC_CHUNK_MAX_TOKENS` | `resource/config/services/highThinkingQA/config.shared.env:25` = `4000` | 4000<br>`highThinkingQA/config.py:288` default=4000<br>`highThinkingQA/config.py:397`<br>`highThinkingQA/ingest/chunker.py:127` |
| `SEMANTIC_CHUNK_MIN_TOKENS` | `resource/config/services/highThinkingQA/config.shared.env:24` = `2000` | 2000<br>`highThinkingQA/config.py:287` default=2000<br>`highThinkingQA/config.py:396`<br>`highThinkingQA/ingest/chunker.py:125` |
| `SSE_HEARTBEAT_SECONDS` | `resource/config/services/highThinkingQA/config.shared.env:73` = `15` | 15<br>`fastQA/app/core/config.py:367` default=15<br>`highThinkingQA/config.py:334` default=15<br>`highThinkingQA/config.py:432`<br>+2 more |
| `THINKING_BACKEND_BASE_URL` | `resource/config/shared/infrastructure.shared.env:19` = `http://127.0.0.1:8009` | "http://127.0.0.1:8009"<br>http://127.0.0.1:8009<br>`gateway/app/core/config.py:118` default="http://127.0.0.1:8009"<br>`gateway/scripts/run_gunicorn_foreground.sh:24` default=http://127.0.0.1:8009<br>`gateway/scripts/start_gunicorn.sh:32` default=http://127.0.0.1:8009 |
| `TIKTOKEN_ENCODING` | `resource/config/services/highThinkingQA/config.shared.env:26` = `cl100k_base` | "cl100k_base"<br>`highThinkingQA/config.py:289` default="cl100k_base"<br>`highThinkingQA/config.py:398`<br>`highThinkingQA/ingest/chunker.py:24`<br>+1 more |
| `TRANSLATION_CACHE_MAX_ENTRIES` | `resource/config/services/public-service/config.shared.env:21` = `10000` | "10000"<br>`public-service/backend/app/modules/documents/translation_cache_impl.py:40` default="10000" |
| `TRANSLATION_CACHE_OBJECT_NAME` | `resource/config/services/public-service/config.shared.env:20` = `translation_cache/translations.json` | "translation_cache/translations.json"<br>`public-service/backend/app/modules/documents/translation_cache_impl.py:48` default="translation_cache/translations.json" |
| `TRANSLATION_CACHE_REMOTE_SYNC_INTERVAL_SECONDS` | `resource/config/services/public-service/config.shared.env:22` = `5` | "5"<br>`public-service/backend/app/modules/documents/translation_cache_impl.py:43` default="5" |
| `UPLOAD_FILE_PROCESSING_ENABLED` | `resource/config/services/public-service/config.shared.env:52` = `1` | "1"<br>`public-service/backend/app/modules/conversation/upload_processing_worker.py:60` default="1"<br>最终策略：上传文件后台处理已确认必开，开关写死启用；worker 数、PDF 页数、poll interval、recovery scan limit 继续保留配置。 |
| `UPLOAD_PROCESSING_RECOVERY_SCAN_LIMIT` | `resource/config/services/public-service/config.shared.env:56` = `500` | "500"<br>`public-service/backend/app/modules/conversation/service.py:667` default="500" |
| `VECTOR_COLLECTION_NAME` | `resource/config/services/public-service/config.shared.env:18` = `lfp_papers` | "lfp_papers"<br>`fastQA/app/modules/microscopic_expert.py:131` default="lfp_papers"<br>`public-service/backend/app/modules/retrieval/service.py:40` default="lfp_papers" |
| `VECTOR_DB_MD_COLLECTION` | `resource/config/services/fastQA/config.shared.env:33` = `md_papers` | "md_papers"<br>`fastQA/app/modules/generation_pipeline/md_expansion.py:29` default="md_papers" |
| `VECTOR_DB_PATH` | `resource/config/services/fastQA/config.shared.env:28` = `/home/cqy/worktrees/highThinking/resource/fastqa/vector_database`<br>`resource/config/services/public-service/config.shared.env:17` = `vector_database` | default="vector_database"<br>"vector_database"<br>`fastQA/app/core/config.py:435`<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:94` default=default="vector_database"<br>`fastQA/app/modules/microscopic_expert.py:114` default="vector_database"<br>+2 more |

## 配置值不同于代码默认值的自动排查项

这些变量至少有一个配置文件值和某处代码默认值不同。该表按变量名自动聚合，只提示需要做产品/部署确认；不能直接推断同名变量在所有服务中都覆盖了默认值，也不能把 `.env.example` 模板值当作当前运行配置。若变量命中“最终精简策略”的不写死范围，以最终策略为准保留配置。

| 变量 | 配置位置和值 | 代码默认值/位置 |
| --- | --- | --- |
| `ASK_EXECUTOR_MAX_WORKERS` | `resource/config/services/highThinkingQA/config.shared.env:71` = `20` | 5<br>`highThinkingQA/config.py:332` default=5<br>`highThinkingQA/config.py:430`<br>`highThinkingQA/server/services/ask_service.py:57`<br>+1 more<br>最终策略：请求执行容量控制不写死，保留配置。 |
| `CHAT_PERSIST_ASYNC` | `resource/config/services/highThinkingQA/config.shared.env:75` = `1` | True<br>`fastQA/app/core/config.py:429` default=True<br>`highThinkingQA/config.py:336` default=True<br>`highThinkingQA/config.py:434`<br>+2 more<br>最终策略：聊天异步持久化已确认必开，开关写死启用；`CHAT_PERSIST_ASYNC_WORKERS` 继续保留配置。 |
| `CHAT_PERSIST_ENABLED` | `resource/config/services/highThinkingQA/config.shared.env:74` = `1` | True<br>`fastQA/app/core/config.py:428` default=True<br>`highThinkingQA/config.py:335` default=True<br>`highThinkingQA/config.py:433`<br>+3 more<br>最终策略：聊天持久化已确认必开，开关写死启用；会话存储路径、authority target、连接信息继续保留配置。 |
| `DASHSCOPE_API_KEY` | `resource/config/services/fastQA/config.secret.env:2` = `<redacted:set>`<br>`resource/config/services/highThinkingQA/config.env.example:5` = `your_dashscope_api_key_here`<br>`resource/config/services/highThinkingQA/config.secret.env:6` = `<redacted:set>` | ""<br>`fastQA/app/core/runtime.py:466`<br>`fastQA/app/core/runtime.py:583` default=""<br>`fastQA/app/modules/generation_pipeline/query_expander.py:33`<br>+19 more |
| `DECOMPOSE_ENABLE_THINKING` | `resource/config/services/highThinkingQA/config.env.example:12` = `0`<br>`resource/config/services/highThinkingQA/config.shared.env:15` = `0` | False<br>`highThinkingQA/agent_core/graph.py:419`<br>`highThinkingQA/config.py:264` default=False<br>`highThinkingQA/config.py:387` |
| `DECOMPOSE_MODEL` | `resource/config/services/highThinkingQA/config.env.example:8` = `qwen3-max`<br>`resource/config/services/highThinkingQA/config.shared.env:11` = `qwen3-max` | os.getenv("LLM_MODEL", "qwen3-max"<br>`highThinkingQA/agent_core/decomposer.py:45`<br>`highThinkingQA/agent_core/graph.py:502`<br>`highThinkingQA/config.py:260` default=os.getenv("LLM_MODEL", "qwen3-max"<br>+1 more |
| `DIRECT_ANSWER_ENABLE_THINKING` | `resource/config/services/highThinkingQA/config.env.example:11` = `0`<br>`resource/config/services/highThinkingQA/config.shared.env:14` = `0` | False<br>`highThinkingQA/agent_core/graph.py:416`<br>`highThinkingQA/config.py:263` default=False<br>`highThinkingQA/config.py:386` |
| `DIRECT_ANSWER_MODEL` | `resource/config/services/highThinkingQA/config.env.example:9` = `qwen3-max`<br>`resource/config/services/highThinkingQA/config.shared.env:12` = `qwen3-max` | os.getenv("LLM_MODEL", "qwen3-max"<br>`highThinkingQA/agent_core/direct_answerer.py:39`<br>`highThinkingQA/agent_core/graph.py:480`<br>`highThinkingQA/config.py:261` default=os.getenv("LLM_MODEL", "qwen3-max"<br>+1 more |
| `EMBEDDING_API_MODEL` | `resource/config/shared/model-endpoints.shared.env:34` = `bge-local` | ""<br>`fastQA/app/modules/microscopic_runtime/embedding_client.py:14` default=""<br>`patent/server/patent/runtime.py:328` |
| `EMBEDDING_API_URL` | `resource/config/shared/model-endpoints.shared.env:33` = `http://127.0.0.1:8001/v1/embeddings` | "EMBEDDING_BASE_URL"<br>""<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:90` default="EMBEDDING_BASE_URL"<br>`fastQA/app/modules/microscopic_expert.py:112` default=""<br>`fastQA/app/modules/microscopic_runtime/bootstrap.py:24`<br>+1 more |
| `EMBEDDING_MODEL_PATH` | `resource/config/services/fastQA/config.shared.env:27` = `/home/cqy/worktrees/highThinking/resource/fastqa/models/bge_model` | default="models/bge_model"<br>"models/bge_model"<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:92` default=default="models/bge_model"<br>`fastQA/app/modules/microscopic_expert.py:110` default="models/bge_model"<br>`patent/server/patent/runtime.py:68` |
| `EMBEDDING_MODEL_TYPE` | `resource/config/shared/model-endpoints.shared.env:32` = `remote` | default="local"<br>"local"<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:88` default=default="local"<br>`fastQA/app/modules/microscopic_expert.py:108` default="local"<br>`patent/server/patent/runtime.py:325` |
| `EMBED_API_TPM` | `resource/config/services/highThinkingQA/config.shared.env:41` = `1200000` | 1_200_000<br>`highThinkingQA/config.py:299` default=1_200_000<br>`highThinkingQA/config.py:408` |
| `ENABLE_CORS` | `resource/config/services/highThinkingQA/config.shared.env:68` = `1` | True<br>`highThinkingQA/config.py:338` default=True<br>`highThinkingQA/config.py:436`<br>`highThinkingQA/server_fastapi/app.py:80`<br>+1 more |
| `FASTQA_ALLOW_PLACEHOLDER_FALLBACK` | `resource/config/services/fastQA/config.shared.env:13` = `1` | True<br>`fastQA/app/core/config.py:362` default=True |
| `FASTQA_ENABLE_FILE_CONTEXT_FALLBACK` | `resource/config/services/fastQA/config.env.example:6` = `0`<br>`resource/config/services/fastQA/config.shared.env:12` = `1` | True<br>`fastQA/app/core/config.py:363` default=True |
| `FASTQA_FASTAPI_PORT` | `resource/config/shared/infrastructure.shared.env:10` = `8008` | ${FASTQA_PORT:-${APP_PORT:-8008<br>${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT<br>${FASTQA_PORT:-${BACKEND_PORT:-$FASTAPI_PORT<br>${FASTQA_PORT:-8008<br>`fastQA/app/core/config.py:286`<br>`fastQA/scripts/start_gunicorn.sh:41` default=${FASTQA_PORT:-${APP_PORT:-8008<br>`fastQA/scripts/start_gunicorn.sh:44` default=${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT<br>+7 more |
| `FASTQA_GENERATION_RUNTIME_ENABLED` | `resource/config/services/fastQA/config.shared.env:14` = `1` | False<br>`fastQA/app/core/config.py:336` default=False |
| `FASTQA_GRAPH_ALLOW_SUSPICIOUS_DOI_FOR_RAG` | `resource/config/services/fastQA/config.env.example:12` = `0`<br>`resource/config/services/fastQA/config.shared.env:20` = `0` | False<br>`fastQA/app/core/config.py:359` default=False |
| `FASTQA_GRAPH_KB_QUERY_LOGGING` | `resource/config/services/fastQA/config.env.example:9` = `0`<br>`resource/config/services/fastQA/config.shared.env:17` = `0` | False<br>`fastQA/app/core/config.py:351` default=False |
| `FASTQA_GUNICORN_WORKERS` | `resource/config/services/fastQA/config.shared.env:6` = `8` | 4<br>`fastQA/scripts/start_gunicorn.sh:36` default=4<br>`fastQA/scripts/start_gunicorn.sh:88` |
| `FASTQA_LLM_HTTP_SHARED_POOL_ENABLED` | `resource/config/services/fastQA/config.shared.env:24` = `1` | False<br>`fastQA/app/core/config.py:371` default=False<br>`fastQA/app/core/runtime.py:137` default=False |
| `FASTQA_NEO4J_URL` | `resource/config/shared/graph.shared.env:1` = `bolt://127.0.0.1:7688` | ""<br>`fastQA/app/core/config.py:340` default=""<br>最终策略：Neo4j 连接信息不写死，保留配置。 |
| `FASTQA_SERVICE_ASSET_ROOT` | `resource/config/shared/resource-roots.env.example:27` = `/home/cqy/worktrees/highThinking/resource/assets` | $ASSET_DIR_DEFAULT<br>"$RESOURCE_DIR/assets"<br>`fastQA/scripts/start_gunicorn.sh:29` default=$ASSET_DIR_DEFAULT<br>`scripts/_service_common.sh:132` default="$RESOURCE_DIR/assets" |
| `FASTQA_SERVICE_CONFIG_ROOT` | `resource/config/shared/resource-roots.env.example:24` = `/home/cqy/worktrees/highThinking/resource/config/services/fastQA` | $CONFIG_DIR_DEFAULT<br>"$RESOURCE_DIR/config/services/fastQA"<br>`fastQA/scripts/start_gunicorn.sh:26` default=$CONFIG_DIR_DEFAULT<br>`fastQA/scripts/start_gunicorn.sh:32`<br>`scripts/_service_common.sh:129` default="$RESOURCE_DIR/config/services/fastQA" |
| `FASTQA_SERVICE_RUNTIME_ROOT` | `resource/config/shared/resource-roots.env.example:26` = `/home/cqy/worktrees/highThinking/resource/runtime/dev/fastQA` | $RUNTIME_DIR_DEFAULT<br>"$RESOURCE_DIR/runtime/dev/fastQA"<br>`fastQA/scripts/start_gunicorn.sh:28` default=$RUNTIME_DIR_DEFAULT<br>`fastQA/scripts/start_gunicorn.sh:50`<br>`fastQA/scripts/start_gunicorn.sh:55`<br>+7 more |
| `FASTQA_SERVICE_STATE_ROOT` | `resource/config/shared/resource-roots.env.example:25` = `/home/cqy/worktrees/highThinking/resource/state/dev/fastQA` | $STATE_DIR_DEFAULT<br>"$RESOURCE_DIR/state/dev/fastQA"<br>`fastQA/scripts/start_gunicorn.sh:27` default=$STATE_DIR_DEFAULT<br>`scripts/_service_common.sh:130` default="$RESOURCE_DIR/state/dev/fastQA" |
| `FASTQA_STAGE2_RERANK_WARMUP_ENABLED` | `resource/config/services/fastQA/config.shared.env:51` = `false` | True<br>`fastQA/app/core/config.py:390` default=True |
| `GATEWAY_ADMISSION_WORKER_ENABLED` | `resource/config/services/gateway/config.secret.env:5` = `<redacted:set>`<br>`resource/config/services/gateway/config.shared.env:5` = `1` | 0<br>`scripts/_service_common.sh:247` default=0<br>`scripts/status_all.sh:23`<br>最终策略：gateway admission worker 是 admission 的顶层启动门禁，已确认必开，开关写死启用；删除配置前必须把启动脚本默认行为改成启用，worker 运行参数、队列、容量和 Redis 连接继续保留配置。 |
| `GATEWAY_GUNICORN_WORKERS` | `resource/config/services/gateway/config.shared.env:1` = `8` | 4<br>`gateway/scripts/run_gunicorn_foreground.sh:18` default=4<br>`gateway/scripts/run_gunicorn_foreground.sh:36`<br>`gateway/scripts/start_gunicorn.sh:26` default=4<br>+1 more |
| `GATEWAY_REFRESH_SURVIVABLE_QA_TASKS_ENABLED` | `resource/config/services/gateway/config.secret.env:2` = `<redacted:set>`<br>`resource/config/services/gateway/config.shared.env:2` = `1` | False<br>`gateway/app/core/config.py:178` default=False |
| `GUNICORN_WORKERS` | `resource/config/services/highThinkingQA/config.env.example:19` = `2`<br>`resource/config/services/highThinkingQA/config.shared.env:62` = `8` | 4<br>`highThinkingQA/config.py:364` default=4<br>`highThinkingQA/config.py:448`<br>`highThinkingQA/server_fastapi/gunicorn.conf.py:9` |
| `HIGHTHINKINGQA_SERVICE_ASSET_ROOT` | `resource/config/shared/resource-roots.env.example:21` = `/home/cqy/worktrees/highThinking/resource/assets` | $SERVICE_ASSET_ROOT_DEFAULT<br>"$RESOURCE_DIR/assets"<br>`highThinkingQA/scripts/start_fastapi_gunicorn.sh:27` default=$SERVICE_ASSET_ROOT_DEFAULT<br>`highThinkingQA/scripts/status_fastapi_gunicorn.sh:25` default=$SERVICE_ASSET_ROOT_DEFAULT<br>`scripts/_service_common.sh:147` default="$RESOURCE_DIR/assets"<br>+1 more |
| `HIGHTHINKINGQA_SERVICE_CONFIG_ROOT` | `resource/config/shared/resource-roots.env.example:18` = `/home/cqy/worktrees/highThinking/resource/config/services/highThinkingQA` | $SERVICE_CONFIG_ROOT_DEFAULT<br>"$RESOURCE_DIR/config/services/highThinkingQA"<br>`highThinkingQA/scripts/start_fastapi_gunicorn.sh:24` default=$SERVICE_CONFIG_ROOT_DEFAULT<br>`highThinkingQA/scripts/start_fastapi_gunicorn.sh:33`<br>`highThinkingQA/scripts/status_fastapi_gunicorn.sh:22` default=$SERVICE_CONFIG_ROOT_DEFAULT<br>+2 more |
| `HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT` | `resource/config/shared/resource-roots.env.example:20` = `/home/cqy/worktrees/highThinking/resource/runtime/dev/highThinkingQA` | $SERVICE_RUNTIME_ROOT_DEFAULT<br>"$RESOURCE_DIR/runtime/dev/highThinkingQA"<br>`highThinkingQA/scripts/start_fastapi_gunicorn.sh:26` default=$SERVICE_RUNTIME_ROOT_DEFAULT<br>`highThinkingQA/scripts/start_fastapi_gunicorn.sh:42`<br>`highThinkingQA/scripts/start_fastapi_gunicorn.sh:49`<br>+7 more |
| `HIGHTHINKINGQA_SERVICE_STATE_ROOT` | `resource/config/shared/resource-roots.env.example:19` = `/home/cqy/worktrees/highThinking/resource/state/dev/highThinkingQA` | $SERVICE_STATE_ROOT_DEFAULT<br>"$RESOURCE_DIR/state/dev/highThinkingQA"<br>`highThinkingQA/scripts/start_fastapi_gunicorn.sh:25` default=$SERVICE_STATE_ROOT_DEFAULT<br>`highThinkingQA/scripts/status_fastapi_gunicorn.sh:23` default=$SERVICE_STATE_ROOT_DEFAULT<br>`scripts/_service_common.sh:145` default="$RESOURCE_DIR/state/dev/highThinkingQA"<br>+1 more |
| `HT_QA_CACHE_LOCK_ENABLED` | `resource/config/services/highThinkingQA/config.shared.env:92` = `1` | True<br>`highThinkingQA/server/services/stage_cache.py:125` default=True |
| `INTERACTIVE_EXECUTION_FAST_OR_PATENT_MAX_CONCURRENT` | `resource/config/services/gateway/config.shared.env:7` = `50` | 20<br>`gateway/app/core/config.py:163` default=20<br>最终策略：各后端最大请求数不写死，保留配置。 |
| `INTERACTIVE_EXECUTION_MAX_CONCURRENT` | `resource/config/services/gateway/config.shared.env:6` = `50` | 20<br>`gateway/app/core/config.py:162` default=20<br>最终策略：全局最大请求数不写死，保留配置。 |
| `INTERACTIVE_EXECUTION_THINKING_MAX_CONCURRENT` | `resource/config/services/gateway/config.shared.env:8` = `20` | 5<br>`gateway/app/core/config.py:164` default=5<br>最终策略：各后端最大请求数不写死，保留配置。 |
| `JWT_SECRET` | `resource/config/services/highThinkingQA/config.env.example:38` = `change_me_for_production`<br>`resource/config/services/highThinkingQA/config.secret.env:26` = `<redacted:set>` | ""<br>`highThinkingQA/server/services/auth_service.py:46` default=""<br>`patent/config.py:292` default=""<br>`patent/server_fastapi/auth/deps.py:46`<br>+2 more<br>最终策略：JWT / auth 配置不写死，保留配置。 |
| `LLM_BASE_URL` | `resource/config/shared/model-endpoints.shared.env:4` = `https://dashscope.aliyuncs.com/compatible-mode/v1` | ""<br>`fastQA/app/modules/generation_pipeline/query_expander.py:36`<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:80`<br>`fastQA/app/modules/qa_pdf/llm_factory.py:55`<br>+12 more |
| `LLM_ENABLE_THINKING` | `resource/config/services/highThinkingQA/config.env.example:7` = `1`<br>`resource/config/services/highThinkingQA/config.shared.env:10` = `1`<br>`resource/config/shared/model-endpoints.shared.env:6` = `0` | True<br>`highThinkingQA/agent_core/graph.py:413`<br>`highThinkingQA/agent_core/llm_client.py:67`<br>`highThinkingQA/config.py:259` default=True<br>+1 more |
| `MINIO_ACCESS_KEY` | `resource/config/services/highThinkingQA/config.env.example:32` = `change_me`<br>`resource/config/services/highThinkingQA/config.secret.env:19` = `<redacted:set>` | ""<br>`fastQA/app/core/config.py:321` default=""<br>`fastQA/app/modules/storage/paper_storage.py:108` default=""<br>`fastQA/app/modules/storage/upload_materializer.py:52` default=""<br>+10 more<br>最终策略：MinIO 配置不写死，保留配置。 |
| `MINIO_ENDPOINT` | `resource/config/services/highThinkingQA/config.env.example:31` = `127.0.0.1:9000`<br>`resource/config/services/highThinkingQA/config.secret.env:18` = `<redacted:set>` | ""<br>`fastQA/app/core/config.py:320` default=""<br>`fastQA/app/modules/storage/paper_storage.py:107` default=""<br>`fastQA/app/modules/storage/upload_materializer.py:51` default=""<br>+10 more<br>最终策略：MinIO 连接信息不写死，保留配置。 |
| `MINIO_SECRET_KEY` | `resource/config/services/highThinkingQA/config.env.example:33` = `change_me`<br>`resource/config/services/highThinkingQA/config.secret.env:20` = `<redacted:set>` | ""<br>`fastQA/app/core/config.py:322` default=""<br>`fastQA/app/modules/storage/paper_storage.py:109` default=""<br>`fastQA/app/modules/storage/upload_materializer.py:53` default=""<br>+10 more<br>最终策略：MinIO 配置不写死，保留配置。 |
| `MYSQL_DATABASE` | `resource/config/services/highThinkingQA/config.env.example:28` = `agentcode`<br>`resource/config/services/highThinkingQA/config.secret.env:15` = `<redacted:set>`<br>`resource/config/shared/infrastructure.shared.env:31` = `agentcode` | "agent_reconstruct"<br>""<br>`fastQA/app/core/config.py:319` default="agent_reconstruct"<br>`highThinkingQA/server/database/connection.py:43` default=""<br>`highThinkingQA/server/database/connection.py:50`<br>+1 more<br>最终策略：MySQL 配置不写死，保留配置。 |
| `MYSQL_PASSWORD` | `resource/config/services/highThinkingQA/config.env.example:27` = `change_me`<br>`resource/config/services/highThinkingQA/config.secret.env:14` = `<redacted:set>` | ""<br>`fastQA/app/core/config.py:318` default=""<br>`highThinkingQA/server/database/connection.py:42` default=""<br>`public-service/backend/app/core/config.py:230` default=""<br>最终策略：MySQL 配置不写死，保留配置。 |
| `NEO4J_URL` | `resource/config/services/fastQA/config.secret.env:7` = `<redacted:set>`<br>`resource/config/shared/graph.shared.env:12` = `bolt://127.0.0.1:7688` | ""<br>`fastQA/app/core/config.py:340` default=""<br>`fastQA/app/core/runtime.py:743`<br>`public-service/backend/app/core/config.py:248` default=""<br>+2 more<br>最终策略：Neo4j 连接信息不写死，保留配置。 |
| `OPENAI_BASE_URL` | `resource/config/shared/model-endpoints.shared.env:18` = `https://dashscope.aliyuncs.com/compatible-mode/v1` | ""<br>`fastQA/app/core/runtime.py:468`<br>`fastQA/app/modules/generation_pipeline/query_expander.py:37`<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:81`<br>+13 more |
| `OPENAI_MODEL` | `resource/config/shared/model-endpoints.shared.env:19` = `deepseek-v3.1` | "qwen-plus"<br>os.getenv("DASHSCOPE_MODEL", "deepseek-v3.1"<br>""<br>`fastQA/app/modules/generation_pipeline/query_expander.py:41` default="qwen-plus"<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:85`<br>`fastQA/app/modules/qa_pdf/llm_factory.py:62` default=os.getenv("DASHSCOPE_MODEL", "deepseek-v3.1"<br>+8 more |
| `PATENT_GUNICORN_WORKERS` | `resource/config/services/patent/config.shared.env:2` = `8` | 4<br>`patent/config.py:266` default=4 |
| `PATENT_LLM_HTTP_SHARED_POOL_ENABLED` | `resource/config/services/patent/config.shared.env:36` = `true` | False<br>default=False<br>`patent/config.py:297` default=False<br>`patent/server/patent/upstream_http.py:58` default=default=False<br>最终策略：patent LLM 共享 HTTP 池已确认必开，开关写死启用；HTTP timeout、连接池大小、keepalive/pool 参数继续保留配置。 |
| `PATENT_PLANNING_HOT_POOL_ENABLED` | `resource/config/services/patent/config.shared.env:37` = `true` | False<br>`patent/config.py:308` default=False<br>`patent/server/patent/planning_hot_pool.py:61` default=False<br>最终策略：patent planning hot pool 已确认必开，开关写死启用；`PATENT_PLANNING_HOT_POOL_LANE_COUNT`、`PATENT_PLANNING_HOT_POOL_LANE_DEGRADED_AFTER_SECONDS` 继续保留配置；warm interval、warm timeout、warm jitter、warm active window 属于预热参数，不再保留配置。 |
| `PATENT_PLANNING_HOT_POOL_WARMUP_ENABLED` | `resource/config/services/patent/config.shared.env:38` = `true` | False<br>`patent/config.py:310` default=False<br>`patent/server/patent/planning_hot_pool.py:69` default=False |
| `PATENT_PLANNING_UPSTREAM_GATE_ENABLED` | `resource/config/services/patent/config.shared.env:39` = `true` | False<br>`patent/config.py:322` default=False<br>`patent/server/patent/upstream_gate.py:79` default=False<br>最终策略：patent planning upstream gate 已确认必开，开关写死启用；gate limit 和相关容量参数继续保留配置。 |
| `PATENT_STAGE2_CONVERGENCE_ENABLED` | `resource/config/services/patent/config.shared.env:7` = `true` | False<br>`patent/server/patent/stage2_controls.py:64` default=False |
| `PATENT_STAGE2_C_GLOBAL_CHUNK_RECALL_ENABLED` | `resource/config/services/patent/config.shared.env:23` = `true` | False<br>`patent/server/patent/stage2_controls.py:75` default=False |
| `PATENT_STAGE2_C_PATENT_SCORING_ENABLED` | `resource/config/services/patent/config.shared.env:22` = `true` | False<br>`patent/server/patent/stage2_controls.py:74` default=False |
| `PATENT_STAGE2_C_TABLE_METRIC_BOOST_ENABLED` | `resource/config/services/patent/config.shared.env:24` = `true` | False<br>`patent/server/patent/stage2_controls.py:76` default=False |
| `PATENT_STAGE2_RERANK_BASE_URL` | `resource/config/services/patent/config.shared.env:14` = `http://localhost:8084`<br>`resource/config/shared/model-endpoints.shared.env:53` = `http://localhost:8084` | default_base_url<br>""<br>`patent/server/patent/rerank_service.py:194` default=default_base_url<br>`patent/server/patent/stage2_controls.py:79` default="" |
| `PATENT_STAGE2_RERANK_PROVIDER` | `resource/config/services/patent/config.shared.env:13` = `local`<br>`resource/config/shared/model-endpoints.shared.env:52` = `local` | "none"<br>`patent/server/patent/rerank_service.py:186` default="none"<br>`patent/server/patent/stage2_controls.py:77` default="none" |
| `PATENT_STAGE2_RERANK_TIMEOUT_SECONDS` | `resource/config/services/patent/config.shared.env:16` = `20`<br>`resource/config/shared/model-endpoints.shared.env:55` = `20` | 20.0<br>`patent/server/patent/rerank_service.py:196`<br>`patent/server/patent/stage2_controls.py:80` default=20.0 |
| `PDF_QA_MAX_RETRIES` | `resource/config/services/fastQA/config.shared.env:104` = `0` | 3<br>`fastQA/app/modules/qa_pdf/llm_factory.py:67` default=3 |
| `PDF_QA_MAX_TOKENS` | `resource/config/services/fastQA/config.shared.env:105` = `1800` | 2500<br>"2500"<br>`fastQA/app/modules/qa_pdf/llm_factory.py:66` default=2500<br>`fastQA/app/services/file_route_service.py:79` default="2500"<br>`patent/server/patent/pdf_service.py:900` default=2500 |
| `PUBLIC_SERVICE_CORS_ORIGINS` | `resource/config/services/public-service/config.shared.env:10` = `http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:5174,http://localhost:5174` | os.getenv("BACKEND_CORS_ORIGINS", "*"<br>`public-service/backend/app/core/config.py:193` default=os.getenv("BACKEND_CORS_ORIGINS", "*" |
| `PUBLIC_SERVICE_DEBUG` | `resource/config/services/public-service/config.shared.env:6` = `0` | False<br>`public-service/backend/app/core/config.py:220` default=False |
| `PUBLIC_SERVICE_ENABLE_LEGACY_CONVERSATION_FALLBACK` | `resource/config/services/public-service/config.shared.env:25` = `0` | False<br>`public-service/backend/app/core/config.py:270` default=False |
| `PUBLIC_SERVICE_GUNICORN_WORKERS` | `resource/config/services/public-service/config.shared.env:11` = `8` | 4<br>`public-service/scripts/start_gunicorn.sh:27` default=4<br>`public-service/scripts/start_gunicorn.sh:58` |
| `PUBLIC_SERVICE_NEO4J_URL` | `resource/config/shared/graph.shared.env:7` = `bolt://127.0.0.1:7688` | ""<br>`public-service/backend/app/core/config.py:248` default="" |
| `QA_QUERY_PIPELINE_MODE` | `resource/config/services/fastQA/config.shared.env:47` = `new` | "new"<br>`fastQA/app/modules/qa_kb/service.py:65` default="new"<br>`fastQA/app/modules/qa_kb/service.py:78` |
| `QA_RETRIEVAL_RERANK_BASE_URL` | `resource/config/shared/model-endpoints.shared.env:49` = `http://localhost:8084` | rerank_default_base_url<br>default_base_url<br>`fastQA/app/core/runtime.py:588` default=rerank_default_base_url<br>`fastQA/app/modules/microscopic_expert.py:165` default=default_base_url |
| `QA_RETRIEVAL_RERANK_PROVIDER` | `resource/config/shared/model-endpoints.shared.env:48` = `local` | "dashscope"<br>`fastQA/app/core/runtime.py:573` default="dashscope"<br>`fastQA/app/modules/microscopic_expert.py:155` default="dashscope"<br>`fastQA/app/modules/qa_cache/stage2_cache.py:88` default="dashscope" |
| `QA_STAGE25_MD_GLOBAL_SUPPLEMENT_ENABLED` | `resource/config/services/fastQA/config.shared.env:72` = `true` | "1"<br>`fastQA/app/modules/generation_pipeline/md_expansion.py:36`<br>`fastQA/app/modules/qa_cache/stage25_cache.py:56` default="1" |
| `QA_STAGE2_QUERY_EXPANSION_ENABLED` | `resource/config/services/fastQA/config.shared.env:61` = `false` | "0"<br>`fastQA/app/modules/generation_pipeline/stage2_retrieval.py:732`<br>`fastQA/app/modules/qa_cache/stage2_cache.py:90` default="0" |
| `REDIS_ENABLED` | `resource/config/services/gateway/config.secret.env:6` = `<redacted:set>`<br>`resource/config/shared/infrastructure.shared.env:22` = `1` | False<br>`fastQA/app/core/config.py:326` default=False<br>`gateway/app/core/config.py:121` default=False<br>`highThinkingQA/server/services/redis_client.py:143` default=False<br>`public-service/backend/app/core/config.py:232` default=False<br>最终策略：Redis 已确认必开，开关写死启用；Redis URL/host/port/db/password/key prefix/socket timeout 继续保留配置。 |
| `REFERENCE_PREVIEW_MAX_WORKERS` | `resource/config/services/public-service/config.shared.env:70` = `4` | str(DEFAULT_PREVIEW_MAX_WORKERS<br>`public-service/backend/app/modules/documents/reference_preview.py:33` default=str(DEFAULT_PREVIEW_MAX_WORKERS |
| `RESOURCE_ROOT` | `resource/config/shared/resource-roots.env.example:3` = `/home/cqy/worktrees/highThinking/resource` | ""<br>`fastQA/app/core/config.py:25`<br>`fastQA/app/core/env_loader.py:59`<br>`fastQA/app/routers/health.py:6`<br>+6 more |
| `SUB_ANSWER_MODEL` | `resource/config/services/highThinkingQA/config.env.example:10` = `qwen3-max`<br>`resource/config/services/highThinkingQA/config.shared.env:13` = `qwen3-max` | os.getenv("LLM_MODEL", "qwen3-max"<br>`highThinkingQA/agent_core/sub_answerer.py:22`<br>`highThinkingQA/config.py:262` default=os.getenv("LLM_MODEL", "qwen3-max"<br>`highThinkingQA/config.py:385` |
| `TOPIC_INDEX_PATH` | `resource/config/services/fastQA/config.shared.env:34` = `/home/cqy/worktrees/highThinking/resource/fastqa/vector_db_topic_index.json` | ""<br>`fastQA/app/core/config.py:440`<br>`fastQA/app/modules/generation_pipeline/context_loading.py:15` default="" |
| `UPLOAD_DIR` | `resource/config/services/highThinkingQA/config.shared.env:53` = `uploads`<br>`resource/config/services/public-service/config.shared.env:14` = `uploads` | ""<br>`highThinkingQA/config.py:330`<br>`highThinkingQA/config.py:428`<br>`highThinkingQA/server_fastapi/app.py:72`<br>+2 more |
| `UPLOAD_QA_FIRST_TOKEN_TIMEOUT_SEC` | `resource/config/services/fastQA/config.shared.env:96` = `60` | "25"<br>`fastQA/app/modules/qa_pdf/service.py:425` default="25"<br>`fastQA/app/modules/qa_pdf/streaming.py:32` default="25" |
| `VECTOR_DB_MD_PATH` | `resource/config/services/fastQA/config.shared.env:32` = `/home/cqy/worktrees/highThinking/resource/fastqa/vector_database_md` | "vector_database_md"<br>`fastQA/app/core/config.py:439`<br>`fastQA/app/modules/generation_pipeline/md_expansion.py:28` default="vector_database_md" |

## 未命中直接字符串引用的配置

这些配置在生产代码/启动脚本扫描中没有直接字符串引用。该表不是“未使用配置”清单；优先检查是否是模板、迁移遗留、外部进程消费，或通过动态拼接 env key 使用，再决定是否删除。

| 变量 | 配置位置和值 |
| --- | --- |
| `BACKEND_SERVER` | `resource/config/services/highThinkingQA/config.shared.env:60` = `gunicorn` |
| `EMBEDDING_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:31` = `120` |
| `FASTQA_TRUST_GATEWAY_ROUTE` | `resource/config/services/fastQA/config.env.example:5` = `1` |
| `GATEWAY_SERVICE_ASSET_ROOT` | `resource/config/shared/resource-roots.env.example:9` = `/home/cqy/worktrees/highThinking/resource/assets` |
| `GATEWAY_SERVICE_CONFIG_ROOT` | `resource/config/shared/resource-roots.env.example:6` = `/home/cqy/worktrees/highThinking/resource/config/services/gateway` |
| `GATEWAY_SERVICE_RUNTIME_ROOT` | `resource/config/shared/resource-roots.env.example:8` = `/home/cqy/worktrees/highThinking/resource/runtime/dev/gateway` |
| `GATEWAY_SERVICE_STATE_ROOT` | `resource/config/shared/resource-roots.env.example:7` = `/home/cqy/worktrees/highThinking/resource/state/dev/gateway` |
| `LLM_PROVIDER` | `resource/config/shared/model-endpoints.shared.env:3` = `openai-compatible` |
| `MYSQL_READ_TIMEOUT_SECONDS` | `resource/config/shared/infrastructure.shared.env:33` = `30` |
| `MYSQL_WRITE_TIMEOUT_SECONDS` | `resource/config/shared/infrastructure.shared.env:34` = `30` |
| `OCR_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:60` = `120` |
| `OPENAI_STREAM_READ_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:25` = `600` |
| `PATENT_EMBEDDING_BASE_URL` | `resource/config/shared/model-endpoints.shared.env:37` = `http://127.0.0.1:8001/v1/embeddings` |
| `PATENT_EMBEDDING_MODEL` | `resource/config/shared/model-endpoints.shared.env:38` = `bge-local` |
| `PDF_QA_TIMEOUT_SECONDS` | `resource/config/services/fastQA/config.shared.env:103` = `45` |
| `PDF_QA_USE_DEDICATED_LLM` | `resource/config/services/fastQA/config.shared.env:101` = `true` |
| `PDF_QA_WARMUP_ENABLED` | `resource/config/services/fastQA/config.shared.env:108` = `false` |
| `PUBLIC_SERVICE_SERVICE_ASSET_ROOT` | `resource/config/shared/resource-roots.env.example:15` = `/home/cqy/worktrees/highThinking/resource/assets` |
| `PUBLIC_SERVICE_SERVICE_CONFIG_ROOT` | `resource/config/shared/resource-roots.env.example:12` = `/home/cqy/worktrees/highThinking/resource/config/services/public-service` |
| `PUBLIC_SERVICE_SERVICE_RUNTIME_ROOT` | `resource/config/shared/resource-roots.env.example:14` = `/home/cqy/worktrees/highThinking/resource/runtime/dev/public-service` |
| `PUBLIC_SERVICE_SERVICE_STATE_ROOT` | `resource/config/shared/resource-roots.env.example:13` = `/home/cqy/worktrees/highThinking/resource/state/dev/public-service` |
| `QA_STREAM_CLEAN_FLUSH_CHARS` | `resource/config/services/fastQA/config.shared.env:60` = `384` |
| `RERANK_API_KEY` | `resource/config/shared/model-endpoints.secret.env.example:5` = `` |
| `RERANK_BASE_URL` | `resource/config/shared/model-endpoints.shared.env:45` = `http://localhost:8084` |
| `RERANK_MODEL` | `resource/config/shared/model-endpoints.shared.env:46` = `qwen3-vl-rerank` |
| `RERANK_PROVIDER` | `resource/config/shared/model-endpoints.shared.env:44` = `local` |
| `RERANK_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:47` = `20` |
| `UPLOAD_PROCESSING_POLL_INTERVAL_MS` | `resource/config/services/public-service/config.shared.env:55` = `1000` |

## 全量配置到代码映射

每行是一个变量。重复出现在多个 env 文件中的变量会在“配置位置和值”中列出全部来源。

| 变量 | 配置位置和值 | 生产代码位置 | 代码默认值/硬编码值 |
| --- | --- | --- | --- |
| `APP_ENV` | `resource/config/services/fastQA/config.shared.env:4` = `development`<br>`resource/config/services/highThinkingQA/config.shared.env:58` = `dev`<br>`resource/config/services/public-service/config.shared.env:4` = `development` | `fastQA/app/core/config.py:284` default="development"<br>`highThinkingQA/config.py:326` default="dev"<br>`highThinkingQA/config.py:347` default="dev"<br>`highThinkingQA/config.py:424`<br>`highThinkingQA/server_fastapi/app.py:69`<br>`public-service/backend/app/core/config.py:195` default="development"<br>`public-service/backend/app/modules/conversation/internal_api.py:74` default=""<br>`public-service/backend/app/modules/conversation/upload_processing_worker.py:432` default="development"<br>+1 more | "development"<br>"dev"<br>"" |
| `APP_LOG_LEVEL` | `resource/config/services/highThinkingQA/config.env.example:16` = `INFO`<br>`resource/config/services/highThinkingQA/config.shared.env:59` = `INFO` | `highThinkingQA/config.py:329` default="INFO"<br>`highThinkingQA/config.py:427` | "INFO" |
| `APP_PORT` | `resource/config/services/fastQA/config.env.example:4` = `8009`<br>`resource/config/services/highThinkingQA/config.env.example:15` = `8008` | `fastQA/scripts/start_gunicorn.sh:33` default=8008<br>`fastQA/scripts/start_gunicorn.sh:34`<br>`fastQA/scripts/start_gunicorn.sh:40`<br>`fastQA/scripts/start_gunicorn.sh:41` default=8008<br>`fastQA/scripts/start_gunicorn.sh:44`<br>`fastQA/scripts/status_gunicorn.sh:14` default=8008<br>`fastQA/scripts/status_gunicorn.sh:15`<br>`fastQA/scripts/stop_gunicorn.sh:11` default=8008<br>+10 more | 8008<br>"8008"<br>8009 |
| `ASK_EXECUTOR_MAX_WORKERS` | `resource/config/services/highThinkingQA/config.shared.env:71` = `20` | `highThinkingQA/config.py:332` default=5<br>`highThinkingQA/config.py:430`<br>`highThinkingQA/server/services/ask_service.py:57`<br>`highThinkingQA/server_fastapi/app.py:74` | 5 |
| `ASK_STREAM_MAX_CONCURRENT` | `resource/config/services/fastQA/config.shared.env:10` = `20`<br>`resource/config/services/highThinkingQA/config.shared.env:70` = `20` | `fastQA/app/core/config.py:364` default=20<br>`highThinkingQA/config.py:331` default=5<br>`highThinkingQA/config.py:429`<br>`highThinkingQA/server_fastapi/app.py:73`<br>`highThinkingQA/server_fastapi/app.py:87`<br>`highThinkingQA/server_fastapi/routers/ask.py:59` | 20<br>5 |
| `ASK_TIMEOUT_SECONDS` | `resource/config/services/highThinkingQA/config.shared.env:72` = `1800` | `highThinkingQA/config.py:333` default=1800<br>`highThinkingQA/config.py:431`<br>`highThinkingQA/server_fastapi/app.py:75`<br>`highThinkingQA/server_fastapi/routers/ask.py:412`<br>`highThinkingQA/server_fastapi/routers/ask.py:424`<br>`highThinkingQA/server_fastapi/routers/ask.py:534` | 1800 |
| `BACKEND_CORS_ORIGINS` | `resource/config/services/fastQA/config.shared.env:7` = `*` | `fastQA/app/core/config.py:276` default="*"<br>`public-service/backend/app/core/config.py:193` default="*" | "*" |
| `BACKEND_SERVER` | `resource/config/services/highThinkingQA/config.shared.env:60` = `gunicorn` | 未命中直接字符串引用 | 无直接代码默认值 |
| `CHAT_JSON_BASE_DIR` | `resource/config/services/fastQA/config.shared.env:43` = `/home/cqy/worktrees/highThinking/resource/fastqa/data/conversations`<br>`resource/config/services/highThinkingQA/config.shared.env:54` = `data/conversations`<br>`resource/config/services/public-service/config.shared.env:16` = `data/conversations` | `fastQA/app/core/config.py:447`<br>`highThinkingQA/config.py:312`<br>`highThinkingQA/config.py:423`<br>`highThinkingQA/server/services/conversation/chat_json_store.py:36` default=config.CHAT_JSON_BASE_DIR<br>`public-service/backend/app/core/config.py:201`<br>`public-service/backend/app/modules/conversation/json_store.py:41` default="data/conversations" | config.CHAT_JSON_BASE_DIR<br>"data/conversations" |
| `CHAT_JSON_STORAGE_PREFIX` | `resource/config/services/highThinkingQA/config.shared.env:55` = `conversations` | `highThinkingQA/server/services/conversation/chat_json_store.py:41` default="conversations"<br>`public-service/backend/app/modules/conversation/json_store.py:48` default="conversations" | "conversations" |
| `CHAT_PERSIST_ASYNC` | `resource/config/services/highThinkingQA/config.shared.env:75` = `1` | `fastQA/app/core/config.py:429` default=True<br>`highThinkingQA/config.py:336` default=True<br>`highThinkingQA/config.py:434`<br>`highThinkingQA/server_fastapi/app.py:78`<br>`highThinkingQA/server_fastapi/routers/ask.py:167` | True<br>最终策略：聊天异步持久化已确认必开，开关写死启用；`CHAT_PERSIST_ASYNC_WORKERS` 继续保留配置。 |
| `CHAT_PERSIST_ASYNC_WORKERS` | `resource/config/services/highThinkingQA/config.shared.env:76` = `4` | `fastQA/app/services/ordered_dispatcher.py:77` default="4"<br>`highThinkingQA/config.py:337` default=4<br>`highThinkingQA/config.py:435`<br>`highThinkingQA/server/runtime/ordered_task_dispatcher.py:82` default="4"<br>`highThinkingQA/server_fastapi/app.py:79` | "4"<br>4 |
| `CHAT_PERSIST_ENABLED` | `resource/config/services/highThinkingQA/config.shared.env:74` = `1` | `fastQA/app/core/config.py:428` default=True<br>`highThinkingQA/config.py:335` default=True<br>`highThinkingQA/config.py:433`<br>`highThinkingQA/server_fastapi/app.py:77`<br>`highThinkingQA/server_fastapi/routers/ask.py:163`<br>`highThinkingQA/server_fastapi/routers/upload.py:104` | True<br>最终策略：聊天持久化已确认必开，开关写死启用；会话存储路径、authority target、连接信息继续保留配置。 |
| `CHECKER_MODEL` | `resource/config/services/highThinkingQA/config.shared.env:30` = `qwen3.5-plus` | `highThinkingQA/agent_core/checker.py:309`<br>`highThinkingQA/agent_core/reviser.py:88`<br>`highThinkingQA/config.py:308` default="qwen3.5-plus"<br>`highThinkingQA/config.py:417` | "qwen3.5-plus" |
| `CHROMA_COLLECTION_NAME` | `resource/config/services/highThinkingQA/config.shared.env:52` = `lfp_papers` | `highThinkingQA/config.py:291` default="lfp_papers"<br>`highThinkingQA/config.py:400`<br>`highThinkingQA/ingest/vector_store.py:58`<br>`highThinkingQA/ingest/vector_store.py:60`<br>`highThinkingQA/ingest/vector_store.py:65`<br>`highThinkingQA/server/services/ask_service.py:152`<br>`highThinkingQA/server/services/stage_cache.py:200`<br>`highThinkingQA/server/services/stage_cache.py:211` | "lfp_papers" |
| `CHROMA_PERSIST_DIR` | `resource/config/services/highThinkingQA/config.shared.env:51` = `../../../highThinkingQA/vectordb` | `highThinkingQA/config.py:290`<br>`highThinkingQA/config.py:399`<br>`highThinkingQA/ingest/vector_store.py:44`<br>`highThinkingQA/ingest/vector_store.py:58`<br>`highThinkingQA/ingest/vector_store.py:64`<br>`highThinkingQA/server/services/ask_service.py:151`<br>`highThinkingQA/server/services/stage_cache.py:199`<br>`highThinkingQA/server/services/stage_cache.py:210` |  |
| `CONVERSATION_ASSISTANT_WRITE_TARGET` | `resource/config/services/highThinkingQA/config.shared.env:78` = `public_service` | `fastQA/app/core/config.py:112` default="legacy"<br>`highThinkingQA/config.py:87` default="public_service"<br>`highThinkingQA/config.py:443`<br>`highThinkingQA/server/services/chat_persistence.py:718`<br>`public-service/backend/app/core/config.py:68` default="legacy" | "legacy"<br>"public_service" |
| `CONVERSATION_DETAIL_CACHE_TOUCH_ON_HIT` | `resource/config/services/public-service/config.shared.env:44` = `1` | `public-service/backend/app/modules/conversation/cache.py:28` default="1"<br>`public-service/backend/app/modules/system/service.py:47` default="1" | "1" |
| `CONVERSATION_DETAIL_CACHE_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:43` = `30` | `public-service/backend/app/modules/conversation/cache.py:20` default="30"<br>`public-service/backend/app/modules/system/service.py:46` default="30" | "30" |
| `CONVERSATION_EXECUTION_AUTHORITY_TARGET` | `resource/config/services/highThinkingQA/config.shared.env:77` = `public_service` | `fastQA/app/core/config.py:110`<br>`highThinkingQA/config.py:85`<br>`highThinkingQA/config.py:440`<br>`public-service/backend/app/core/config.py:66` |  |
| `CONVERSATION_LIST_CACHE_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:42` = `60` | `public-service/backend/app/modules/conversation/cache.py:12` default="60"<br>`public-service/backend/app/modules/system/service.py:45` default="60" | "60" |
| `CONVERSATION_LIST_RECENT_PAGES_LIMIT` | `resource/config/services/public-service/config.shared.env:46` = `8` | `public-service/backend/app/modules/conversation/cache.py:49` default="8"<br>`public-service/backend/app/modules/system/service.py:49` default="8" | "8" |
| `CONVERSATION_LIST_RECENT_PAGES_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:45` = `900` | `public-service/backend/app/modules/conversation/cache.py:41` default="900"<br>`public-service/backend/app/modules/system/service.py:48` default="900" | "900" |
| `CONVERSATION_LOCK_RETRY_INTERVAL_MS` | `resource/config/services/public-service/config.shared.env:66` = `100` | `public-service/backend/app/modules/conversation/json_store.py:39` default=100 | 100 |
| `CONVERSATION_LOCK_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:64` = `30` | `public-service/backend/app/modules/conversation/json_store.py:37` default=30 | 30 |
| `CONVERSATION_LOCK_WAIT_SECONDS` | `resource/config/services/public-service/config.shared.env:65` = `10` | `public-service/backend/app/modules/conversation/json_store.py:38` default=10 | 10 |
| `CORS_ORIGINS` | `resource/config/services/highThinkingQA/config.env.example:17` = `*`<br>`resource/config/services/highThinkingQA/config.shared.env:69` = `*` | `highThinkingQA/config.py:339` default="*"<br>`highThinkingQA/config.py:437`<br>`highThinkingQA/server_fastapi/app.py:81`<br>`highThinkingQA/server_fastapi/app.py:93` | "*" |
| `DASHSCOPE_API_KEY` | `resource/config/services/fastQA/config.secret.env:2` = `<redacted:set>`<br>`resource/config/services/fastQA/config.secret.env.example:3` = ``<br>`resource/config/services/highThinkingQA/config.env.example:5` = `your_dashscope_api_key_here`<br>`resource/config/services/highThinkingQA/config.secret.env:6` = `<redacted:set>`<br>`resource/config/services/highThinkingQA/config.secret.env.example:8` = ``<br>`resource/config/shared/infrastructure.secret.env.example:5` = ``<br>`resource/config/shared/model-endpoints.secret.env.example:3` = `` | `fastQA/app/core/runtime.py:466`<br>`fastQA/app/core/runtime.py:583` default=""<br>`fastQA/app/modules/generation_pipeline/query_expander.py:33`<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:78`<br>`fastQA/app/modules/microscopic_expert.py:162` default=""<br>`fastQA/app/modules/qa_pdf/llm_factory.py:53`<br>`fastQA/app/modules/qa_pdf/llm_factory.py:150`<br>`fastQA/app/services/file_route_service.py:62`<br>+14 more | "" |
| `DASHSCOPE_BASE_URL` | `resource/config/shared/model-endpoints.shared.env:16` = `https://dashscope.aliyuncs.com/compatible-mode/v1` | `fastQA/app/core/runtime.py:468`<br>`fastQA/app/modules/generation_pipeline/query_expander.py:38`<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:82`<br>`fastQA/app/modules/qa_pdf/llm_factory.py:57`<br>`fastQA/app/services/file_route_service.py:64`<br>`highThinkingQA/config.py:254`<br>`highThinkingQA/config.py:269` default="https://dashscope.aliyuncs.com/compatible-mode/v1"<br>`highThinkingQA/config.py:280` default="https://dashscope.aliyuncs.com/compatible-mode/v1"<br>+7 more | "https://dashscope.aliyuncs.com/compatible-mode/v1"<br>"" |
| `DASHSCOPE_MODEL` | `resource/config/shared/model-endpoints.shared.env:17` = `deepseek-v3.1` | `fastQA/app/modules/generation_pipeline/query_expander.py:41` default="qwen-plus"<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:85`<br>`fastQA/app/modules/qa_cache/stage1_cache.py:97` default="unknown"<br>`fastQA/app/modules/qa_cache/stage25_cache.py:37` default="unknown"<br>`fastQA/app/modules/qa_cache/stage2_cache.py:78` default="unknown"<br>`fastQA/app/modules/qa_pdf/llm_factory.py:62` default="deepseek-v3.1"<br>`patent/server/patent/answering.py:956`<br>`patent/server/patent/hybrid_synthesis.py:278`<br>+5 more | "qwen-plus"<br>"unknown"<br>"deepseek-v3.1"<br>"" |
| `DECOMPOSE_ENABLE_THINKING` | `resource/config/services/highThinkingQA/config.env.example:12` = `0`<br>`resource/config/services/highThinkingQA/config.shared.env:15` = `0` | `highThinkingQA/agent_core/graph.py:419`<br>`highThinkingQA/config.py:264` default=False<br>`highThinkingQA/config.py:387` | False |
| `DECOMPOSE_MODEL` | `resource/config/services/highThinkingQA/config.env.example:8` = `qwen3-max`<br>`resource/config/services/highThinkingQA/config.shared.env:11` = `qwen3-max` | `highThinkingQA/agent_core/decomposer.py:45`<br>`highThinkingQA/agent_core/graph.py:502`<br>`highThinkingQA/config.py:260` default=os.getenv("LLM_MODEL", "qwen3-max"<br>`highThinkingQA/config.py:383` | os.getenv("LLM_MODEL", "qwen3-max" |
| `DELETED_FILE_CLEANUP_RECONCILE_LIMIT` | `resource/config/services/public-service/config.shared.env:63` = `3` | `public-service/backend/app/modules/conversation/service.py:768` default="3" | "3" |
| `DIRECT_ANSWER_ENABLE_THINKING` | `resource/config/services/highThinkingQA/config.env.example:11` = `0`<br>`resource/config/services/highThinkingQA/config.shared.env:14` = `0` | `highThinkingQA/agent_core/graph.py:416`<br>`highThinkingQA/config.py:263` default=False<br>`highThinkingQA/config.py:386` | False |
| `DIRECT_ANSWER_MODEL` | `resource/config/services/highThinkingQA/config.env.example:9` = `qwen3-max`<br>`resource/config/services/highThinkingQA/config.shared.env:12` = `qwen3-max` | `highThinkingQA/agent_core/direct_answerer.py:39`<br>`highThinkingQA/agent_core/graph.py:480`<br>`highThinkingQA/config.py:261` default=os.getenv("LLM_MODEL", "qwen3-max"<br>`highThinkingQA/config.py:384` | os.getenv("LLM_MODEL", "qwen3-max" |
| `EMBEDDING_API_KEY` | `resource/config/services/fastQA/config.secret.env.example:5` = ``<br>`resource/config/services/highThinkingQA/config.secret.env.example:10` = ``<br>`resource/config/shared/infrastructure.secret.env.example:8` = ``<br>`resource/config/shared/model-endpoints.secret.env.example:4` = `` | `highThinkingQA/config.py:246` default=""<br>`highThinkingQA/config.py:390`<br>`highThinkingQA/ingest/embedder.py:48` | "" |
| `EMBEDDING_API_MODEL` | `resource/config/shared/model-endpoints.shared.env:34` = `bge-local` | `fastQA/app/modules/microscopic_runtime/embedding_client.py:14` default=""<br>`patent/server/patent/runtime.py:328` | "" |
| `EMBEDDING_API_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:35` = `120` | `fastQA/app/modules/microscopic_runtime/embedding_client.py:19` default="120"<br>`patent/server/patent/runtime.py:326` default="20" | "120"<br>"20" |
| `EMBEDDING_API_URL` | `resource/config/shared/model-endpoints.shared.env:33` = `http://127.0.0.1:8001/v1/embeddings` | `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:90` default="EMBEDDING_BASE_URL"<br>`fastQA/app/modules/microscopic_expert.py:112` default=""<br>`fastQA/app/modules/microscopic_runtime/bootstrap.py:24`<br>`patent/server/patent/runtime.py:327` | "EMBEDDING_BASE_URL"<br>"" |
| `EMBEDDING_BASE_URL` | `resource/config/services/highThinkingQA/config.shared.env:16` = `https://dashscope.aliyuncs.com/compatible-mode/v1`<br>`resource/config/shared/model-endpoints.shared.env:29` = `http://127.0.0.1:8001/v1/embeddings` | `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:90`<br>`highThinkingQA/config.py:267`<br>`highThinkingQA/config.py:388`<br>`highThinkingQA/ingest/embedder.py:51` |  |
| `EMBEDDING_DIMENSIONS` | `resource/config/services/highThinkingQA/config.shared.env:18` = `2048` | `highThinkingQA/config.py:275` default=2048<br>`highThinkingQA/config.py:391`<br>`highThinkingQA/ingest/embedder.py:91`<br>`highThinkingQA/ingest/embedder.py:96`<br>`highThinkingQA/ingest/embedder.py:190` | 2048 |
| `EMBEDDING_MODEL` | `resource/config/services/highThinkingQA/config.shared.env:17` = `text-embedding-v4`<br>`resource/config/shared/model-endpoints.shared.env:30` = `bge-local` | `highThinkingQA/config.py:273` default="text-embedding-v4"<br>`highThinkingQA/config.py:389`<br>`highThinkingQA/ingest/embedder.py:132` | "text-embedding-v4" |
| `EMBEDDING_MODEL_PATH` | `resource/config/services/fastQA/config.shared.env:27` = `/home/cqy/worktrees/highThinking/resource/fastqa/models/bge_model` | `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:92` default=default="models/bge_model"<br>`fastQA/app/modules/microscopic_expert.py:110` default="models/bge_model"<br>`patent/server/patent/runtime.py:68` | default="models/bge_model"<br>"models/bge_model" |
| `EMBEDDING_MODEL_TYPE` | `resource/config/shared/model-endpoints.shared.env:32` = `remote` | `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:88` default=default="local"<br>`fastQA/app/modules/microscopic_expert.py:108` default="local"<br>`patent/server/patent/runtime.py:325` | default="local"<br>"local" |
| `EMBEDDING_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:31` = `120` | 未命中直接字符串引用；最终策略：作为旧/重复 embedding timeout 退役，fastQA + patent 统一保留实际被代码读取的 `EMBEDDING_API_TIMEOUT_SECONDS`。 | 无直接代码默认值 |
| `EMBED_API_RPM` | `resource/config/services/highThinkingQA/config.shared.env:40` = `1800` | `highThinkingQA/config.py:298` default=1800<br>`highThinkingQA/config.py:407` | 1800 |
| `EMBED_API_TPM` | `resource/config/services/highThinkingQA/config.shared.env:41` = `1200000` | `highThinkingQA/config.py:299` default=1_200_000<br>`highThinkingQA/config.py:408` | 1_200_000 |
| `EMBED_BATCH_SIZE` | `resource/config/services/highThinkingQA/config.shared.env:39` = `10` | `highThinkingQA/config.py:297` default=10<br>`highThinkingQA/config.py:406`<br>`highThinkingQA/ingest/embedder.py:27` | 10 |
| `EMBED_CONCURRENCY` | `resource/config/services/highThinkingQA/config.shared.env:42` = `2` | `highThinkingQA/config.py:300` default=2<br>`highThinkingQA/config.py:409`<br>`highThinkingQA/ingest/pipeline.py:6`<br>`highThinkingQA/ingest/pipeline.py:271` | 2 |
| `EMBED_MAX_CONCURRENT_REQUESTS` | `resource/config/services/highThinkingQA/config.shared.env:43` = `4` | `highThinkingQA/config.py:301` default=4<br>`highThinkingQA/config.py:410`<br>`highThinkingQA/ingest/embedder.py:30` | 4 |
| `EMBED_MAX_INPUT_TOKENS` | `resource/config/services/highThinkingQA/config.shared.env:44` = `8000` | `highThinkingQA/config.py:302` default=8000<br>`highThinkingQA/config.py:411`<br>`highThinkingQA/ingest/embedder.py:63`<br>`highThinkingQA/ingest/embedder.py:82` | 8000 |
| `EMBED_MAX_RETRIES` | `resource/config/services/highThinkingQA/config.shared.env:45` = `5` | `highThinkingQA/config.py:303` default=5<br>`highThinkingQA/config.py:412`<br>`highThinkingQA/ingest/embedder.py:126` | 5 |
| `EMBED_QUEUE_SIZE` | `resource/config/services/highThinkingQA/config.shared.env:46` = `200` | `highThinkingQA/config.py:304` default=200<br>`highThinkingQA/config.py:413`<br>`highThinkingQA/ingest/pipeline.py:6`<br>`highThinkingQA/ingest/pipeline.py:270` | 200 |
| `ENABLE_CORS` | `resource/config/services/highThinkingQA/config.shared.env:68` = `1` | `highThinkingQA/config.py:338` default=True<br>`highThinkingQA/config.py:436`<br>`highThinkingQA/server_fastapi/app.py:80`<br>`highThinkingQA/server_fastapi/app.py:92` | True |
| `FASTQA_ALLOW_PLACEHOLDER_FALLBACK` | `resource/config/services/fastQA/config.shared.env:13` = `1` | `fastQA/app/core/config.py:362` default=True | True |
| `FASTQA_ENABLE_FILE_CONTEXT_FALLBACK` | `resource/config/services/fastQA/config.env.example:6` = `0`<br>`resource/config/services/fastQA/config.shared.env:12` = `1` | `fastQA/app/core/config.py:363` default=True | True |
| `FASTQA_FASTAPI_PORT` | `resource/config/shared/infrastructure.shared.env:10` = `8008` | `fastQA/app/core/config.py:286`<br>`fastQA/scripts/start_gunicorn.sh:41` default=${FASTQA_PORT:-${APP_PORT:-8008<br>`fastQA/scripts/start_gunicorn.sh:44` default=${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT<br>`fastQA/scripts/start_gunicorn.sh:47` default=${FASTQA_PORT:-${BACKEND_PORT:-$FASTAPI_PORT<br>`fastQA/scripts/status_gunicorn.sh:14` default=${FASTQA_PORT:-${APP_PORT:-8008<br>`fastQA/scripts/status_gunicorn.sh:15` default=${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT<br>`fastQA/scripts/stop_gunicorn.sh:11` default=${FASTQA_PORT:-${APP_PORT:-8008<br>`fastQA/scripts/stop_gunicorn.sh:12` default=${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT<br>+2 more | ${FASTQA_PORT:-${APP_PORT:-8008<br>${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT<br>${FASTQA_PORT:-${BACKEND_PORT:-$FASTAPI_PORT<br>${FASTQA_PORT:-8008 |
| `FASTQA_GENERATION_RUNTIME_ENABLED` | `resource/config/services/fastQA/config.shared.env:14` = `1` | `fastQA/app/core/config.py:336` default=False | False |
| `FASTQA_GRAPH_ALLOW_SUSPICIOUS_DOI_FOR_RAG` | `resource/config/services/fastQA/config.env.example:12` = `0`<br>`resource/config/services/fastQA/config.shared.env:20` = `0` | `fastQA/app/core/config.py:359` default=False | False |
| `FASTQA_GRAPH_DIRECT_ANSWER_MIN_CONFIDENCE` | `resource/config/services/fastQA/config.env.example:10` = `0.8`<br>`resource/config/services/fastQA/config.shared.env:18` = `0.8` | `fastQA/app/core/config.py:353` |  |
| `FASTQA_GRAPH_KB_ENABLED` | `resource/config/services/fastQA/config.secret.env:4` = `<redacted:set>` | `fastQA/app/core/config.py:337` default=True | True |
| `FASTQA_GRAPH_KB_MAX_ROWS` | `resource/config/services/fastQA/config.env.example:8` = `20`<br>`resource/config/services/fastQA/config.shared.env:16` = `20` | `fastQA/app/core/config.py:350` default=20 | 20 |
| `FASTQA_GRAPH_KB_QUERY_LOGGING` | `resource/config/services/fastQA/config.env.example:9` = `0`<br>`resource/config/services/fastQA/config.shared.env:17` = `0` | `fastQA/app/core/config.py:351` default=False | False |
| `FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED` | `resource/config/services/fastQA/config.secret.env:6` = `<redacted:set>` | `fastQA/app/core/config.py:339` default=True | True |
| `FASTQA_GRAPH_KB_TIMEOUT_MS` | `resource/config/services/fastQA/config.env.example:7` = `3000`<br>`resource/config/services/fastQA/config.shared.env:15` = `3000` | `fastQA/app/core/config.py:349` default=3000 | 3000 |
| `FASTQA_GRAPH_KB_V2_ENABLED` | `resource/config/services/fastQA/config.secret.env:5` = `<redacted:set>` | `fastQA/app/core/config.py:338` default=True | True |
| `FASTQA_GRAPH_MAX_DOI_CANDIDATES` | `resource/config/services/fastQA/config.env.example:11` = `20`<br>`resource/config/services/fastQA/config.shared.env:19` = `20` | `fastQA/app/core/config.py:358` default=20 | 20 |
| `FASTQA_GUNICORN_WORKERS` | `resource/config/services/fastQA/config.shared.env:6` = `8` | `fastQA/scripts/start_gunicorn.sh:36` default=4<br>`fastQA/scripts/start_gunicorn.sh:88` | 4 |
| `FASTQA_HOST` | `resource/config/shared/infrastructure.shared.env:8` = `0.0.0.0` | `fastQA/app/core/config.py:279` |  |
| `FASTQA_LLM_HTTP_SHARED_POOL_ENABLED` | `resource/config/services/fastQA/config.shared.env:24` = `1` | `fastQA/app/core/config.py:371` default=False<br>`fastQA/app/core/runtime.py:137` default=False | False |
| `FASTQA_NEO4J_DATABASE` | `resource/config/shared/graph.shared.env:3` = `neo4j` | `fastQA/app/core/config.py:346` default="neo4j" | "neo4j" |
| `FASTQA_NEO4J_PASSWORD` | `resource/config/shared/graph.secret.env.example:1` = `` | `fastQA/app/core/config.py:344` default="" | "" |
| `FASTQA_NEO4J_URL` | `resource/config/shared/graph.shared.env:1` = `bolt://127.0.0.1:7688` | `fastQA/app/core/config.py:340` default="" | "" |
| `FASTQA_NEO4J_USERNAME` | `resource/config/shared/graph.shared.env:2` = `neo4j` | `fastQA/app/core/config.py:342` default="neo4j" | "neo4j" |
| `FASTQA_PORT` | `resource/config/shared/infrastructure.shared.env:9` = `8008` | `fastQA/app/core/config.py:287`<br>`fastQA/scripts/start_gunicorn.sh:41` default=${APP_PORT:-8008<br>`fastQA/scripts/start_gunicorn.sh:44` default=${FASTAPI_PORT:-$APP_PORT<br>`fastQA/scripts/start_gunicorn.sh:47` default=${BACKEND_PORT:-$FASTAPI_PORT<br>`fastQA/scripts/status_gunicorn.sh:14` default=${APP_PORT:-8008<br>`fastQA/scripts/status_gunicorn.sh:15` default=${FASTAPI_PORT:-$APP_PORT<br>`fastQA/scripts/stop_gunicorn.sh:11` default=${APP_PORT:-8008<br>`fastQA/scripts/stop_gunicorn.sh:12` default=${FASTAPI_PORT:-$APP_PORT<br>+2 more | ${APP_PORT:-8008<br>${FASTAPI_PORT:-$APP_PORT<br>${BACKEND_PORT:-$FASTAPI_PORT<br>8008 |
| `FASTQA_SERVICE_ASSET_ROOT` | `resource/config/shared/resource-roots.env.example:27` = `/home/cqy/worktrees/highThinking/resource/assets` | `fastQA/scripts/start_gunicorn.sh:29` default=$ASSET_DIR_DEFAULT<br>`scripts/_service_common.sh:132` default="$RESOURCE_DIR/assets" | $ASSET_DIR_DEFAULT<br>"$RESOURCE_DIR/assets" |
| `FASTQA_SERVICE_CONFIG_ROOT` | `resource/config/shared/resource-roots.env.example:24` = `/home/cqy/worktrees/highThinking/resource/config/services/fastQA` | `fastQA/scripts/start_gunicorn.sh:26` default=$CONFIG_DIR_DEFAULT<br>`fastQA/scripts/start_gunicorn.sh:32`<br>`scripts/_service_common.sh:129` default="$RESOURCE_DIR/config/services/fastQA" | $CONFIG_DIR_DEFAULT<br>"$RESOURCE_DIR/config/services/fastQA" |
| `FASTQA_SERVICE_RUNTIME_ROOT` | `resource/config/shared/resource-roots.env.example:26` = `/home/cqy/worktrees/highThinking/resource/runtime/dev/fastQA` | `fastQA/scripts/start_gunicorn.sh:28` default=$RUNTIME_DIR_DEFAULT<br>`fastQA/scripts/start_gunicorn.sh:50`<br>`fastQA/scripts/start_gunicorn.sh:55`<br>`fastQA/scripts/status_gunicorn.sh:12` default=$RUNTIME_DIR_DEFAULT<br>`fastQA/scripts/status_gunicorn.sh:16`<br>`fastQA/scripts/stop_gunicorn.sh:10` default=$RUNTIME_DIR_DEFAULT<br>`fastQA/scripts/stop_gunicorn.sh:13`<br>`scripts/_service_common.sh:131` default="$RESOURCE_DIR/runtime/dev/fastQA"<br>+2 more | $RUNTIME_DIR_DEFAULT<br>"$RESOURCE_DIR/runtime/dev/fastQA" |
| `FASTQA_SERVICE_STATE_ROOT` | `resource/config/shared/resource-roots.env.example:25` = `/home/cqy/worktrees/highThinking/resource/state/dev/fastQA` | `fastQA/scripts/start_gunicorn.sh:27` default=$STATE_DIR_DEFAULT<br>`scripts/_service_common.sh:130` default="$RESOURCE_DIR/state/dev/fastQA" | $STATE_DIR_DEFAULT<br>"$RESOURCE_DIR/state/dev/fastQA" |
| `FASTQA_STAGE2_CHAT_WARM_INTERVAL_SECONDS` | `resource/config/services/fastQA/config.shared.env:50` = `7200` | `fastQA/app/core/config.py:392` |  |
| `FASTQA_STAGE2_RERANK_WARMUP_ENABLED` | `resource/config/services/fastQA/config.shared.env:51` = `false` | `fastQA/app/core/config.py:390` default=True | True |
| `FASTQA_STAGE2_RERANK_WARM_INTERVAL_SECONDS` | `resource/config/services/fastQA/config.shared.env:52` = `7200` | `fastQA/app/core/config.py:395` |  |
| `FASTQA_STAGE2_WARM_ACTIVE_END_HOUR` | `resource/config/services/fastQA/config.shared.env:54` = `18` | `fastQA/app/core/config.py:426` |  |
| `FASTQA_STAGE2_WARM_ACTIVE_START_HOUR` | `resource/config/services/fastQA/config.shared.env:53` = `8` | `fastQA/app/core/config.py:423` |  |
| `FASTQA_TRUST_GATEWAY_ROUTE` | `resource/config/services/fastQA/config.env.example:5` = `1` | 未命中直接字符串引用 | 无直接代码默认值 |
| `FAST_BACKEND_BASE_URL` | `resource/config/shared/infrastructure.shared.env:18` = `http://127.0.0.1:8008` | `gateway/app/core/config.py:116` default="http://127.0.0.1:8008"<br>`gateway/scripts/run_gunicorn_foreground.sh:23` default=http://127.0.0.1:8008<br>`gateway/scripts/start_gunicorn.sh:31` default=http://127.0.0.1:8008 | "http://127.0.0.1:8008"<br>http://127.0.0.1:8008 |
| `GATEWAY_ADMISSION_CONTROL_TOKEN` | `resource/config/services/gateway/config.secret.env.example:2` = `` | `gateway/app/core/config.py:160` default="" | "" |
| `GATEWAY_ADMISSION_DISPATCHER_ENABLED` | `resource/config/services/gateway/config.secret.env:4` = `<redacted:set>`<br>`resource/config/services/gateway/config.shared.env:4` = `1` | `gateway/app/core/config.py:159` default=admission_enabled<br>`gateway/scripts/run_admission_worker_foreground.sh:19` default=1<br>`gateway/scripts/start_admission_worker.sh:25` default=1 | admission_enabled<br>1<br>最终策略：admission dispatcher 已确认必开，开关写死启用；dispatcher/worker 运行参数、队列和容量数值继续保留配置。 |
| `GATEWAY_ADMISSION_ENABLED` | `resource/config/services/gateway/config.secret.env:3` = `<redacted:set>`<br>`resource/config/services/gateway/config.shared.env:3` = `1` | `gateway/app/core/config.py:123` default=False<br>`gateway/scripts/run_admission_worker_foreground.sh:18` default=1<br>`gateway/scripts/start_admission_worker.sh:24` default=1 | False<br>1<br>最终策略：gateway admission 已确认必开，开关写死启用；全局/单用户/各后端并发上限继续保留配置。 |
| `GATEWAY_ADMISSION_WORKER_ENABLED` | `resource/config/services/gateway/config.secret.env:5` = `<redacted:set>`<br>`resource/config/services/gateway/config.shared.env:5` = `1` | `scripts/_service_common.sh:247` default=0<br>`scripts/status_all.sh:23` | 0<br>最终策略：gateway admission worker 是 admission 的顶层启动门禁，已确认必开，开关写死启用；删除配置前必须把启动脚本默认行为改成启用。 |
| `GATEWAY_GUNICORN_WORKERS` | `resource/config/services/gateway/config.shared.env:1` = `8` | `gateway/scripts/run_gunicorn_foreground.sh:18` default=4<br>`gateway/scripts/run_gunicorn_foreground.sh:36`<br>`gateway/scripts/start_gunicorn.sh:26` default=4<br>`gateway/scripts/start_gunicorn.sh:65` | 4 |
| `GATEWAY_HOST` | `resource/config/shared/infrastructure.shared.env:4` = `0.0.0.0` | `gateway/app/core/config.py:133` default="0.0.0.0" | "0.0.0.0" |
| `GATEWAY_PORT` | `resource/config/shared/infrastructure.shared.env:5` = `8101` | `gateway/app/core/config.py:134` default="8101"<br>`gateway/scripts/run_gunicorn_foreground.sh:17` default=8101<br>`gateway/scripts/run_gunicorn_foreground.sh:35`<br>`gateway/scripts/start_gunicorn.sh:25` default=8101<br>`gateway/scripts/start_gunicorn.sh:64`<br>`gateway/scripts/start_gunicorn.sh:78`<br>`gateway/scripts/status_gunicorn.sh:7` default=8101<br>`gateway/scripts/stop_gunicorn.sh:6` default=8101<br>+2 more | "8101"<br>8101 |
| `GATEWAY_REFRESH_SURVIVABLE_QA_TASKS_ENABLED` | `resource/config/services/gateway/config.secret.env:2` = `<redacted:set>`<br>`resource/config/services/gateway/config.shared.env:2` = `1` | `gateway/app/core/config.py:178` default=False | False |
| `GATEWAY_SERVICE_ASSET_ROOT` | `resource/config/shared/resource-roots.env.example:9` = `/home/cqy/worktrees/highThinking/resource/assets` | 未命中直接字符串引用 | 无直接代码默认值 |
| `GATEWAY_SERVICE_CONFIG_ROOT` | `resource/config/shared/resource-roots.env.example:6` = `/home/cqy/worktrees/highThinking/resource/config/services/gateway` | 未命中直接字符串引用 | 无直接代码默认值 |
| `GATEWAY_SERVICE_RUNTIME_ROOT` | `resource/config/shared/resource-roots.env.example:8` = `/home/cqy/worktrees/highThinking/resource/runtime/dev/gateway` | 未命中直接字符串引用 | 无直接代码默认值 |
| `GATEWAY_SERVICE_STATE_ROOT` | `resource/config/shared/resource-roots.env.example:7` = `/home/cqy/worktrees/highThinking/resource/state/dev/gateway` | 未命中直接字符串引用 | 无直接代码默认值 |
| `GUNICORN_KEEPALIVE` | `resource/config/services/highThinkingQA/config.shared.env:65` = `15` | `highThinkingQA/config.py:367` default=15<br>`highThinkingQA/config.py:451`<br>`highThinkingQA/server_fastapi/gunicorn.conf.py:12` | 15 |
| `GUNICORN_MAX_REQUESTS` | `resource/config/services/highThinkingQA/config.shared.env:66` = `1000` | `highThinkingQA/config.py:368` default=1000<br>`highThinkingQA/config.py:452`<br>`highThinkingQA/server_fastapi/gunicorn.conf.py:13` | 1000 |
| `GUNICORN_MAX_REQUESTS_JITTER` | `resource/config/services/highThinkingQA/config.shared.env:67` = `100` | `highThinkingQA/config.py:369` default=100<br>`highThinkingQA/config.py:453`<br>`highThinkingQA/server_fastapi/gunicorn.conf.py:14` | 100 |
| `GUNICORN_THREADS` | `resource/config/services/highThinkingQA/config.env.example:20` = `8`<br>`resource/config/services/highThinkingQA/config.shared.env:63` = `8` | `highThinkingQA/config.py:365` default=8<br>`highThinkingQA/config.py:449`<br>`highThinkingQA/server_fastapi/gunicorn.conf.py:10` | 8 |
| `GUNICORN_TIMEOUT` | `resource/config/services/highThinkingQA/config.env.example:21` = `1800`<br>`resource/config/services/highThinkingQA/config.shared.env:64` = `1800` | `highThinkingQA/config.py:366` default=1800<br>`highThinkingQA/config.py:450`<br>`highThinkingQA/server_fastapi/gunicorn.conf.py:11` | 1800 |
| `GUNICORN_WORKERS` | `resource/config/services/highThinkingQA/config.env.example:19` = `2`<br>`resource/config/services/highThinkingQA/config.shared.env:62` = `8` | `highThinkingQA/config.py:364` default=4<br>`highThinkingQA/config.py:448`<br>`highThinkingQA/server_fastapi/gunicorn.conf.py:9` | 4 |
| `GUNICORN_WORKER_CLASS` | `resource/config/services/highThinkingQA/config.env.example:18` = `uvicorn.workers.UvicornWorker`<br>`resource/config/services/highThinkingQA/config.shared.env:61` = `uvicorn.workers.UvicornWorker` | `highThinkingQA/config.py:363` default="uvicorn.workers.UvicornWorker"<br>`highThinkingQA/config.py:447`<br>`highThinkingQA/server_fastapi/gunicorn.conf.py:8` | "uvicorn.workers.UvicornWorker" |
| `HIGHTHINKINGQA_HOST` | `resource/config/shared/infrastructure.shared.env:11` = `0.0.0.0` | `highThinkingQA/config.py:327` default="0.0.0.0" | "0.0.0.0" |
| `HIGHTHINKINGQA_PORT` | `resource/config/shared/infrastructure.shared.env:12` = `8009` | `highThinkingQA/config.py:319` default="8008"<br>`highThinkingQA/scripts/start_fastapi_gunicorn.sh:39` default=${APP_PORT:-8009<br>`highThinkingQA/scripts/status_fastapi_gunicorn.sh:27` default=${APP_PORT:-8009<br>`highThinkingQA/scripts/stop_fastapi_gunicorn.sh:13` default=${APP_PORT:-8009<br>`scripts/_service_common.sh:35`<br>`scripts/_service_common.sh:76` default=8009 | "8008"<br>${APP_PORT:-8009<br>8009 |
| `HIGHTHINKINGQA_SERVICE_ASSET_ROOT` | `resource/config/shared/resource-roots.env.example:21` = `/home/cqy/worktrees/highThinking/resource/assets` | `highThinkingQA/scripts/start_fastapi_gunicorn.sh:27` default=$SERVICE_ASSET_ROOT_DEFAULT<br>`highThinkingQA/scripts/status_fastapi_gunicorn.sh:25` default=$SERVICE_ASSET_ROOT_DEFAULT<br>`scripts/_service_common.sh:147` default="$RESOURCE_DIR/assets"<br>`scripts/_service_common.sh:158` default="$RESOURCE_DIR/assets" | $SERVICE_ASSET_ROOT_DEFAULT<br>"$RESOURCE_DIR/assets" |
| `HIGHTHINKINGQA_SERVICE_CONFIG_ROOT` | `resource/config/shared/resource-roots.env.example:18` = `/home/cqy/worktrees/highThinking/resource/config/services/highThinkingQA` | `highThinkingQA/scripts/start_fastapi_gunicorn.sh:24` default=$SERVICE_CONFIG_ROOT_DEFAULT<br>`highThinkingQA/scripts/start_fastapi_gunicorn.sh:33`<br>`highThinkingQA/scripts/status_fastapi_gunicorn.sh:22` default=$SERVICE_CONFIG_ROOT_DEFAULT<br>`scripts/_service_common.sh:144` default="$RESOURCE_DIR/config/services/highThinkingQA"<br>`scripts/_service_common.sh:155` default="$RESOURCE_DIR/config/services/highThinkingQA" | $SERVICE_CONFIG_ROOT_DEFAULT<br>"$RESOURCE_DIR/config/services/highThinkingQA" |
| `HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT` | `resource/config/shared/resource-roots.env.example:20` = `/home/cqy/worktrees/highThinking/resource/runtime/dev/highThinkingQA` | `highThinkingQA/scripts/start_fastapi_gunicorn.sh:26` default=$SERVICE_RUNTIME_ROOT_DEFAULT<br>`highThinkingQA/scripts/start_fastapi_gunicorn.sh:42`<br>`highThinkingQA/scripts/start_fastapi_gunicorn.sh:49`<br>`highThinkingQA/scripts/status_fastapi_gunicorn.sh:24` default=$SERVICE_RUNTIME_ROOT_DEFAULT<br>`highThinkingQA/scripts/status_fastapi_gunicorn.sh:28`<br>`highThinkingQA/scripts/stop_fastapi_gunicorn.sh:12` default=$SERVICE_RUNTIME_ROOT_DEFAULT<br>`highThinkingQA/scripts/stop_fastapi_gunicorn.sh:14`<br>`scripts/_service_common.sh:146` default="$RESOURCE_DIR/runtime/dev/highThinkingQA"<br>+2 more | $SERVICE_RUNTIME_ROOT_DEFAULT<br>"$RESOURCE_DIR/runtime/dev/highThinkingQA" |
| `HIGHTHINKINGQA_SERVICE_STATE_ROOT` | `resource/config/shared/resource-roots.env.example:19` = `/home/cqy/worktrees/highThinking/resource/state/dev/highThinkingQA` | `highThinkingQA/scripts/start_fastapi_gunicorn.sh:25` default=$SERVICE_STATE_ROOT_DEFAULT<br>`highThinkingQA/scripts/status_fastapi_gunicorn.sh:23` default=$SERVICE_STATE_ROOT_DEFAULT<br>`scripts/_service_common.sh:145` default="$RESOURCE_DIR/state/dev/highThinkingQA"<br>`scripts/_service_common.sh:156` default="$RESOURCE_DIR/state/dev/highThinkingQA" | $SERVICE_STATE_ROOT_DEFAULT<br>"$RESOURCE_DIR/state/dev/highThinkingQA" |
| `HT_QA_CACHE_EPOCH` | `resource/config/services/highThinkingQA/config.shared.env:88` = `0` | `highThinkingQA/server/services/stage_cache.py:93` default="0" | "0" |
| `HT_QA_CACHE_LOCK_ENABLED` | `resource/config/services/highThinkingQA/config.shared.env:92` = `1` | `highThinkingQA/server/services/stage_cache.py:125` default=True | True |
| `HT_QA_CACHE_LOCK_TTL_SECONDS` | `resource/config/services/highThinkingQA/config.shared.env:94` = `30` | `highThinkingQA/server/services/stage_cache.py:133` default=30 | 30 |
| `HT_QA_CACHE_WAIT_MS` | `resource/config/services/highThinkingQA/config.shared.env:93` = `400` | `highThinkingQA/server/services/stage_cache.py:129` default=400 | 400 |
| `HT_QA_DECOMPOSE_CACHE_TTL_SECONDS` | `resource/config/services/highThinkingQA/config.shared.env:90` = `43200` | `highThinkingQA/server/services/stage_cache.py:117` default=43200 | 43200 |
| `HT_QA_DIRECT_CACHE_TTL_SECONDS` | `resource/config/services/highThinkingQA/config.shared.env:89` = `43200` | `highThinkingQA/server/services/stage_cache.py:113` default=43200 | 43200 |
| `HT_QA_RETRIEVE_CACHE_TTL_SECONDS` | `resource/config/services/highThinkingQA/config.shared.env:91` = `43200` | `highThinkingQA/server/services/stage_cache.py:121` default=43200 | 43200 |
| `INTERACTIVE_EXECUTION_FAST_OR_PATENT_MAX_CONCURRENT` | `resource/config/services/gateway/config.shared.env:7` = `50` | `gateway/app/core/config.py:163` default=20 | 20 |
| `INTERACTIVE_EXECUTION_MAX_CONCURRENT` | `resource/config/services/gateway/config.shared.env:6` = `50` | `gateway/app/core/config.py:162` default=20 | 20 |
| `INTERACTIVE_EXECUTION_THINKING_MAX_CONCURRENT` | `resource/config/services/gateway/config.shared.env:8` = `20` | `gateway/app/core/config.py:164` default=5 | 5 |
| `JSON_DIR` | `resource/config/services/fastQA/config.shared.env:37` = `/home/cqy/worktrees/highThinking/resource/fastqa/json` | `fastQA/app/core/config.py:441` |  |
| `JSON_NORMALIZED_DIR` | `resource/config/services/fastQA/config.shared.env:38` = `/home/cqy/worktrees/highThinking/resource/fastqa/json_normalized` | `fastQA/app/core/config.py:442` |  |
| `JSON_SUMMARY_DIR` | `resource/config/services/fastQA/config.shared.env:41` = `/home/cqy/worktrees/highThinking/resource/fastqa/json_summary` | `fastQA/app/core/config.py:445` |  |
| `JWT_EXPIRE_SECONDS` | `resource/config/services/highThinkingQA/config.shared.env:81` = `86400`<br>`resource/config/services/public-service/config.shared.env:28` = `86400` | `highThinkingQA/server/services/auth_service.py:55` default="86400"<br>`patent/config.py:293` default=86400<br>`public-service/backend/app/modules/auth/service.py:211` default="86400" | "86400"<br>86400 |
| `JWT_SECRET` | `resource/config/services/highThinkingQA/config.env.example:38` = `change_me_for_production`<br>`resource/config/services/highThinkingQA/config.secret.env:26` = `<redacted:set>`<br>`resource/config/services/highThinkingQA/config.secret.env.example:23` = ``<br>`resource/config/services/public-service/config.secret.env.example:1` = `` | `highThinkingQA/server/services/auth_service.py:46` default=""<br>`patent/config.py:292` default=""<br>`patent/server_fastapi/auth/deps.py:46`<br>`public-service/backend/app/modules/auth/service.py:207` default=""<br>`public-service/backend/app/modules/auth/service.py:209` | "" |
| `LLM_API_KEY` | `resource/config/services/fastQA/config.secret.env.example:4` = ``<br>`resource/config/services/highThinkingQA/config.secret.env:7` = `<redacted:set>`<br>`resource/config/services/highThinkingQA/config.secret.env.example:9` = ``<br>`resource/config/shared/infrastructure.secret.env.example:7` = ``<br>`resource/config/shared/model-endpoints.secret.env.example:1` = `` | `fastQA/app/modules/generation_pipeline/query_expander.py:33`<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:78` default="OPENAI_API_KEY"<br>`fastQA/app/modules/qa_pdf/llm_factory.py:53`<br>`fastQA/app/modules/qa_pdf/llm_factory.py:150`<br>`highThinkingQA/agent_core/llm_client.py:29`<br>`highThinkingQA/agent_core/llm_client.py:41`<br>`highThinkingQA/config.py:245` default=""<br>`highThinkingQA/config.py:381`<br>+8 more | "OPENAI_API_KEY"<br>"" |
| `LLM_BASE_URL` | `resource/config/shared/model-endpoints.shared.env:4` = `https://dashscope.aliyuncs.com/compatible-mode/v1` | `fastQA/app/modules/generation_pipeline/query_expander.py:36`<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:80`<br>`fastQA/app/modules/qa_pdf/llm_factory.py:55`<br>`highThinkingQA/agent_core/llm_client.py:32`<br>`highThinkingQA/agent_core/llm_client.py:44`<br>`highThinkingQA/config.py:252`<br>`highThinkingQA/config.py:379`<br>`highThinkingQA/server/services/documents_service.py:42`<br>+7 more | "" |
| `LLM_CONNECT_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:7` = `15` | `fastQA/app/integrations/llm/shared_http_pool.py:96` |  |
| `LLM_ENABLE_THINKING` | `resource/config/services/highThinkingQA/config.env.example:7` = `1`<br>`resource/config/services/highThinkingQA/config.shared.env:10` = `1`<br>`resource/config/shared/model-endpoints.shared.env:6` = `0` | `highThinkingQA/agent_core/graph.py:413`<br>`highThinkingQA/agent_core/llm_client.py:67`<br>`highThinkingQA/config.py:259` default=True<br>`highThinkingQA/config.py:382` | True |
| `LLM_KEEPALIVE_EXPIRY_SECONDS` | `resource/config/shared/model-endpoints.shared.env:12` = `120` | `fastQA/app/integrations/llm/shared_http_pool.py:139` |  |
| `LLM_MAX_CONNECTIONS` | `resource/config/shared/model-endpoints.shared.env:13` = `160` | `fastQA/app/integrations/llm/shared_http_pool.py:146` |  |
| `LLM_MAX_KEEPALIVE_CONNECTIONS` | `resource/config/shared/model-endpoints.shared.env:14` = `64` | `fastQA/app/integrations/llm/shared_http_pool.py:153` |  |
| `LLM_MODEL` | `resource/config/services/highThinkingQA/config.env.example:6` = `qwen3-max`<br>`resource/config/services/highThinkingQA/config.shared.env:9` = `qwen3-max`<br>`resource/config/shared/model-endpoints.shared.env:5` = `deepseek-v3.1` | `fastQA/app/modules/generation_pipeline/query_expander.py:41` default="qwen-plus"<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:85` default="OPENAI_MODEL"<br>`fastQA/app/modules/qa_pdf/llm_factory.py:62` default=os.getenv("OPENAI_MODEL", os.getenv("DASHSCOPE_MODEL", "deepseek-v3.1"<br>`highThinkingQA/agent_core/llm_client.py:70`<br>`highThinkingQA/agent_core/llm_client.py:118`<br>`highThinkingQA/agent_core/llm_client.py:167`<br>`highThinkingQA/config.py:257` default="qwen3-max"<br>`highThinkingQA/config.py:260` default="qwen3-max"<br>+11 more | "qwen-plus"<br>"OPENAI_MODEL"<br>os.getenv("OPENAI_MODEL", os.getenv("DASHSCOPE_MODEL", "deepseek-v3.1"<br>"qwen3-max"<br>"" |
| `LLM_POOL_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:11` = `30` | `fastQA/app/integrations/llm/shared_http_pool.py:130` |  |
| `LLM_PROVIDER` | `resource/config/shared/model-endpoints.shared.env:3` = `openai-compatible` | 未命中直接字符串引用 | 无直接代码默认值 |
| `LLM_READ_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:8` = `180` | `fastQA/app/integrations/llm/shared_http_pool.py:105` |  |
| `LLM_STREAM_READ_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:9` = `600` | `fastQA/app/integrations/llm/shared_http_pool.py:114` |  |
| `LLM_WRITE_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:10` = `180` | `fastQA/app/integrations/llm/shared_http_pool.py:121` |  |
| `LOCAL_STORAGE_ROOT` | `resource/config/services/public-service/config.shared.env:24` = `storage` | `public-service/backend/app/core/config.py:213` |  |
| `LOGIN_FAILURE_LOCK_MINUTES` | `resource/config/services/highThinkingQA/config.shared.env:84` = `5`<br>`resource/config/services/public-service/config.shared.env:31` = `5` | `highThinkingQA/server/services/auth_service.py:89` default="5"<br>`public-service/backend/app/modules/auth/service.py:250` default="5" | "5" |
| `LOGIN_FAILURE_LOCK_THRESHOLD` | `resource/config/services/highThinkingQA/config.shared.env:83` = `5`<br>`resource/config/services/public-service/config.shared.env:30` = `5` | `highThinkingQA/server/services/auth_service.py:88` default="5"<br>`public-service/backend/app/modules/auth/service.py:249` default="5" | "5" |
| `LOG_LEVEL` | `resource/config/services/fastQA/config.shared.env:5` = `INFO` | `fastQA/app/main.py:40` default="INFO"<br>`patent/server_fastapi/app.py:727` default="INFO" | "INFO" |
| `MATERIAL_AGENT_PROMPTS_DIR` | `resource/config/services/fastQA/config.shared.env:44` = `/home/cqy/worktrees/highThinking/resource/fastqa/prompts` | `fastQA/app/core/config.py:448` |  |
| `MAX_CHECK_LOOPS` | `resource/config/services/highThinkingQA/config.shared.env:31` = `2` | `highThinkingQA/agent_core/graph.py:393`<br>`highThinkingQA/agent_core/graph.py:423`<br>`highThinkingQA/config.py:309` default=2<br>`highThinkingQA/config.py:418` | 2 |
| `MAX_CHUNK_TOKENS` | `resource/config/services/highThinkingQA/config.shared.env:23` = `4000` | `highThinkingQA/config.py:286` default=4000<br>`highThinkingQA/config.py:395`<br>`highThinkingQA/ingest/chunker.py:258`<br>`highThinkingQA/ingest/chunker.py:308`<br>`highThinkingQA/ingest/chunker.py:317`<br>`highThinkingQA/ingest/chunker.py:329`<br>`highThinkingQA/ingest/chunker.py:345`<br>`highThinkingQA/ingest/chunker.py:347` | 4000 |
| `MAX_PDF_PAGES` | `resource/config/services/highThinkingQA/config.shared.env:85` = `50`<br>`resource/config/services/public-service/config.shared.env:69` = `50` | `fastQA/app/modules/documents/service.py:99` default="50"<br>`highThinkingQA/server/services/documents_service.py:28` default="50"<br>`public-service/backend/app/modules/documents/service.py:118` default="50" | "50" |
| `MINIO_ACCESS_KEY` | `resource/config/services/fastQA/config.secret.env.example:9` = ``<br>`resource/config/services/highThinkingQA/config.env.example:32` = `change_me`<br>`resource/config/services/highThinkingQA/config.secret.env:19` = `<redacted:set>`<br>`resource/config/services/highThinkingQA/config.secret.env.example:18` = ``<br>`resource/config/services/public-service/config.secret.env.example:4` = ``<br>`resource/config/shared/infrastructure.secret.env.example:18` = `` | `fastQA/app/core/config.py:321` default=""<br>`fastQA/app/modules/storage/paper_storage.py:108` default=""<br>`fastQA/app/modules/storage/upload_materializer.py:52` default=""<br>`highThinkingQA/server/storage/minio_backend.py:27` default=""<br>`highThinkingQA/server/storage/minio_backend.py:34`<br>`highThinkingQA/server/storage/paper_storage.py:68` default=""<br>`public-service/backend/app/core/config.py:243` default=""<br>`public-service/backend/app/integrations/storage/minio.py:27`<br>+5 more | "" |
| `MINIO_BUCKET` | `resource/config/services/highThinkingQA/config.env.example:34` = `agentcode`<br>`resource/config/services/highThinkingQA/config.secret.env:21` = `<redacted:set>`<br>`resource/config/shared/infrastructure.shared.env:40` = `agentcode` | `fastQA/app/core/config.py:323` default="agentcode"<br>`fastQA/app/modules/storage/paper_storage.py:110` default=""<br>`highThinkingQA/server/storage/minio_backend.py:29` default=""<br>`highThinkingQA/server/storage/paper_storage.py:70` default=""<br>`public-service/backend/app/core/config.py:245` default="agentcode"<br>`public-service/backend/app/modules/documents/translation_cache_impl.py:60` default=""<br>`scripts/patent_originals_backfill.py:34` default="agentcode"<br>`scripts/patent_originals_parity_check.py:33` default="agentcode" | "agentcode"<br>"" |
| `MINIO_DOWNLOAD_EXPIRES` | `resource/config/shared/infrastructure.shared.env:43` = `3600` | `highThinkingQA/server/storage/file_delivery_service.py:63` default="3600"<br>`public-service/backend/app/modules/conversation/service.py:3416` default="3600" | "3600" |
| `MINIO_ENDPOINT` | `resource/config/services/highThinkingQA/config.env.example:31` = `127.0.0.1:9000`<br>`resource/config/services/highThinkingQA/config.secret.env:18` = `<redacted:set>`<br>`resource/config/shared/infrastructure.secret.env.example:17` = `` | `fastQA/app/core/config.py:320` default=""<br>`fastQA/app/modules/storage/paper_storage.py:107` default=""<br>`fastQA/app/modules/storage/upload_materializer.py:51` default=""<br>`highThinkingQA/server/storage/minio_backend.py:26` default=""<br>`highThinkingQA/server/storage/minio_backend.py:34`<br>`highThinkingQA/server/storage/paper_storage.py:67` default=""<br>`public-service/backend/app/core/config.py:242` default=""<br>`public-service/backend/app/integrations/storage/minio.py:27`<br>+5 more | "" |
| `MINIO_REGION` | `resource/config/services/highThinkingQA/config.secret.env:23` = `<redacted:set>`<br>`resource/config/services/highThinkingQA/config.secret.env.example:20` = ``<br>`resource/config/shared/infrastructure.secret.env.example:20` = `` | `fastQA/app/core/config.py:325` default=""<br>`highThinkingQA/server/storage/minio_backend.py:31` default=""<br>`public-service/backend/app/core/config.py:247` default="" | "" |
| `MINIO_SECRET_KEY` | `resource/config/services/fastQA/config.secret.env.example:10` = ``<br>`resource/config/services/highThinkingQA/config.env.example:33` = `change_me`<br>`resource/config/services/highThinkingQA/config.secret.env:20` = `<redacted:set>`<br>`resource/config/services/highThinkingQA/config.secret.env.example:19` = ``<br>`resource/config/services/public-service/config.secret.env.example:5` = ``<br>`resource/config/shared/infrastructure.secret.env.example:19` = `` | `fastQA/app/core/config.py:322` default=""<br>`fastQA/app/modules/storage/paper_storage.py:109` default=""<br>`fastQA/app/modules/storage/upload_materializer.py:53` default=""<br>`highThinkingQA/server/storage/minio_backend.py:28` default=""<br>`highThinkingQA/server/storage/minio_backend.py:34`<br>`highThinkingQA/server/storage/paper_storage.py:69` default=""<br>`public-service/backend/app/core/config.py:244` default=""<br>`public-service/backend/app/integrations/storage/minio.py:27`<br>+5 more | "" |
| `MINIO_SECURE` | `resource/config/services/highThinkingQA/config.env.example:35` = `0`<br>`resource/config/services/highThinkingQA/config.secret.env:22` = `<redacted:set>`<br>`resource/config/shared/infrastructure.shared.env:41` = `0` | `fastQA/app/core/config.py:324` default=False<br>`fastQA/app/modules/storage/paper_storage.py:111` default="0"<br>`fastQA/app/modules/storage/upload_materializer.py:54` default="0"<br>`highThinkingQA/server/storage/minio_backend.py:30` default="0"<br>`highThinkingQA/server/storage/paper_storage.py:71` default="0"<br>`public-service/backend/app/core/config.py:246` default=False<br>`public-service/backend/app/modules/documents/translation_cache_impl.py:61` default="0"<br>`scripts/patent_originals_backfill.py:35` default="0"<br>+1 more | False<br>"0" |
| `MINIO_USE_PROXY` | `resource/config/shared/infrastructure.shared.env:42` = `1` | `highThinkingQA/server/storage/file_delivery_service.py:61`<br>`public-service/backend/app/modules/conversation/service.py:3414` default="1" | "1"<br>最终策略：MinIO 代理下载已确认必开，开关写死启用；MinIO endpoint、bucket、凭据、secure、region、下载过期时间继续保留配置。 |
| `MYSQL_CONNECT_RETRIES` | `resource/config/shared/infrastructure.shared.env:35` = `2` | `highThinkingQA/server/database/connection.py:81` |  |
| `MYSQL_CONNECT_RETRY_DELAY_SECONDS` | `resource/config/shared/infrastructure.shared.env:36` = `0.15` | `highThinkingQA/server/database/connection.py:82` |  |
| `MYSQL_CONNECT_TIMEOUT_SECONDS` | `resource/config/shared/infrastructure.shared.env:32` = `5` | `highThinkingQA/server/database/connection.py:45` |  |
| `MYSQL_DATABASE` | `resource/config/services/highThinkingQA/config.env.example:28` = `agentcode`<br>`resource/config/services/highThinkingQA/config.secret.env:15` = `<redacted:set>`<br>`resource/config/shared/infrastructure.shared.env:31` = `agentcode` | `fastQA/app/core/config.py:319` default="agent_reconstruct"<br>`highThinkingQA/server/database/connection.py:43` default=""<br>`highThinkingQA/server/database/connection.py:50`<br>`public-service/backend/app/core/config.py:231` default="agent_reconstruct" | "agent_reconstruct"<br>"" |
| `MYSQL_HOST` | `resource/config/services/highThinkingQA/config.env.example:24` = `127.0.0.1`<br>`resource/config/services/highThinkingQA/config.secret.env:11` = `<redacted:set>`<br>`resource/config/shared/infrastructure.shared.env:29` = `127.0.0.1` | `fastQA/app/core/config.py:315` default="127.0.0.1"<br>`highThinkingQA/server/database/connection.py:39` default=""<br>`public-service/backend/app/core/config.py:227` default="127.0.0.1" | "127.0.0.1"<br>"" |
| `MYSQL_PASSWORD` | `resource/config/services/fastQA/config.secret.env.example:8` = ``<br>`resource/config/services/highThinkingQA/config.env.example:27` = `change_me`<br>`resource/config/services/highThinkingQA/config.secret.env:14` = `<redacted:set>`<br>`resource/config/services/highThinkingQA/config.secret.env.example:15` = ``<br>`resource/config/services/public-service/config.secret.env.example:3` = ``<br>`resource/config/shared/infrastructure.secret.env.example:15` = `` | `fastQA/app/core/config.py:318` default=""<br>`highThinkingQA/server/database/connection.py:42` default=""<br>`public-service/backend/app/core/config.py:230` default="" | "" |
| `MYSQL_PORT` | `resource/config/services/highThinkingQA/config.env.example:25` = `3306`<br>`resource/config/services/highThinkingQA/config.secret.env:12` = `<redacted:set>`<br>`resource/config/shared/infrastructure.shared.env:30` = `3306` | `fastQA/app/core/config.py:316` default=3306<br>`highThinkingQA/server/database/connection.py:40`<br>`public-service/backend/app/core/config.py:228` default=3306 | 3306 |
| `MYSQL_QUERY_RETRIES` | `resource/config/shared/infrastructure.shared.env:37` = `2` | `highThinkingQA/server/database/connection.py:99`<br>`highThinkingQA/server/database/connection.py:128` |  |
| `MYSQL_QUERY_RETRY_DELAY_SECONDS` | `resource/config/shared/infrastructure.shared.env:38` = `0.05` | `highThinkingQA/server/database/connection.py:100`<br>`highThinkingQA/server/database/connection.py:129` |  |
| `MYSQL_READ_TIMEOUT_SECONDS` | `resource/config/shared/infrastructure.shared.env:33` = `30` | 未命中直接字符串引用 | 无直接代码默认值 |
| `MYSQL_USER` | `resource/config/services/fastQA/config.secret.env.example:7` = ``<br>`resource/config/services/highThinkingQA/config.env.example:26` = `root`<br>`resource/config/services/highThinkingQA/config.secret.env:13` = `<redacted:set>`<br>`resource/config/services/highThinkingQA/config.secret.env.example:14` = ``<br>`resource/config/services/public-service/config.secret.env.example:2` = ``<br>`resource/config/shared/infrastructure.secret.env.example:14` = `` | `fastQA/app/core/config.py:317` default="root"<br>`highThinkingQA/server/database/connection.py:41` default=""<br>`highThinkingQA/server/database/connection.py:48`<br>`public-service/backend/app/core/config.py:229` default="root" | "root"<br>"" |
| `MYSQL_WRITE_TIMEOUT_SECONDS` | `resource/config/shared/infrastructure.shared.env:34` = `30` | 未命中直接字符串引用 | 无直接代码默认值 |
| `NEO4J_PASSWORD` | `resource/config/services/fastQA/config.secret.env:9` = `<redacted:set>`<br>`resource/config/shared/graph.secret.env.example:6` = `` | `fastQA/app/core/config.py:344` default=""<br>`public-service/backend/app/core/config.py:252` default=""<br>`public-service/backend/app/modules/retrieval/service.py:45` default="password" | ""<br>"password" |
| `NEO4J_URL` | `resource/config/services/fastQA/config.secret.env:7` = `<redacted:set>`<br>`resource/config/shared/graph.shared.env:12` = `bolt://127.0.0.1:7688` | `fastQA/app/core/config.py:340` default=""<br>`fastQA/app/core/runtime.py:743`<br>`public-service/backend/app/core/config.py:248` default=""<br>`public-service/backend/app/core/runtime.py:283` default=""<br>`public-service/backend/app/modules/retrieval/service.py:43` default="" | "" |
| `NEO4J_USERNAME` | `resource/config/services/fastQA/config.secret.env:8` = `<redacted:set>`<br>`resource/config/shared/graph.shared.env:13` = `neo4j` | `fastQA/app/core/config.py:342` default="neo4j"<br>`public-service/backend/app/core/config.py:250` default="neo4j"<br>`public-service/backend/app/modules/retrieval/service.py:44` default="neo4j" | "neo4j" |
| `NUM_SUB_QUESTIONS` | `resource/config/services/highThinkingQA/config.shared.env:29` = `5` | `highThinkingQA/agent_core/decomposer.py:38`<br>`highThinkingQA/agent_core/graph.py:421`<br>`highThinkingQA/config.py:307` default=5<br>`highThinkingQA/config.py:416` | 5 |
| `OCR_API_KEY` | `resource/config/services/fastQA/config.secret.env.example:6` = ``<br>`resource/config/services/highThinkingQA/config.secret.env.example:11` = ``<br>`resource/config/shared/model-endpoints.secret.env.example:8` = `` | `highThinkingQA/config.py:247` default=""<br>`highThinkingQA/config.py:394`<br>`highThinkingQA/ingest/pdf_parser.py:96` | "" |
| `OCR_BASE_URL` | `resource/config/services/highThinkingQA/config.shared.env:19` = `https://dashscope.aliyuncs.com/compatible-mode/v1`<br>`resource/config/shared/model-endpoints.shared.env:58` = `http://127.0.0.1:8001/v1` | `highThinkingQA/config.py:278`<br>`highThinkingQA/config.py:392`<br>`highThinkingQA/ingest/pdf_parser.py:97` |  |
| `OCR_CONCURRENCY` | `resource/config/services/highThinkingQA/config.shared.env:34` = `40` | `highThinkingQA/config.py:292` default=40<br>`highThinkingQA/config.py:401`<br>`highThinkingQA/ingest/pipeline.py:289` | 40 |
| `OCR_MAX_CONCURRENT_REQUESTS` | `resource/config/services/highThinkingQA/config.shared.env:35` = `40` | `highThinkingQA/config.py:293` default=40<br>`highThinkingQA/config.py:402`<br>`highThinkingQA/ingest/pdf_parser.py:12`<br>`highThinkingQA/ingest/pdf_parser.py:42`<br>`highThinkingQA/ingest/pdf_parser.py:44` | 40 |
| `OCR_MAX_RETRIES` | `resource/config/services/highThinkingQA/config.shared.env:37` = `5` | `highThinkingQA/config.py:295` default=5<br>`highThinkingQA/config.py:404`<br>`highThinkingQA/ingest/pdf_parser.py:157` | 5 |
| `OCR_MODEL` | `resource/config/services/highThinkingQA/config.shared.env:20` = `qwen-vl-ocr-2025-11-20`<br>`resource/config/shared/model-endpoints.shared.env:59` = `qwen-vl-ocr` | `highThinkingQA/config.py:284` default="qwen-vl-ocr-2025-11-20"<br>`highThinkingQA/config.py:393`<br>`highThinkingQA/ingest/pdf_parser.py:120`<br>`highThinkingQA/ingest/pdf_parser.py:259` | "qwen-vl-ocr-2025-11-20" |
| `OCR_PAGES_PER_BATCH` | `resource/config/services/highThinkingQA/config.shared.env:36` = `3` | `highThinkingQA/config.py:294` default=3<br>`highThinkingQA/config.py:403`<br>`highThinkingQA/ingest/pdf_parser.py:7`<br>`highThinkingQA/ingest/pdf_parser.py:86`<br>`highThinkingQA/ingest/pdf_parser.py:208`<br>`highThinkingQA/ingest/pdf_parser.py:218`<br>`highThinkingQA/ingest/pipeline.py:11` | 3 |
| `OCR_RETRY_BASE` | `resource/config/services/highThinkingQA/config.shared.env:38` = `3` | `highThinkingQA/config.py:296` default=3<br>`highThinkingQA/config.py:405`<br>`highThinkingQA/ingest/pdf_parser.py:158` | 3 |
| `OCR_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:60` = `120` | 未命中直接字符串引用 | 无直接代码默认值 |
| `OPENAI_API_KEY` | `resource/config/services/fastQA/config.secret.env:3` = `<redacted:set>`<br>`resource/config/services/highThinkingQA/config.secret.env:8` = `<redacted:set>`<br>`resource/config/shared/infrastructure.secret.env.example:6` = ``<br>`resource/config/shared/model-endpoints.secret.env.example:2` = `` | `fastQA/app/core/runtime.py:466`<br>`fastQA/app/modules/generation_pipeline/query_expander.py:33`<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:78`<br>`fastQA/app/modules/qa_pdf/llm_factory.py:53`<br>`fastQA/app/modules/qa_pdf/llm_factory.py:150`<br>`fastQA/app/services/file_route_service.py:62`<br>`highThinkingQA/config.py:244` default=""<br>`highThinkingQA/server/services/documents_service.py:35` default=""<br>+7 more | "" |
| `OPENAI_BASE_URL` | `resource/config/shared/model-endpoints.shared.env:18` = `https://dashscope.aliyuncs.com/compatible-mode/v1` | `fastQA/app/core/runtime.py:468`<br>`fastQA/app/modules/generation_pipeline/query_expander.py:37`<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:81`<br>`fastQA/app/modules/qa_pdf/llm_factory.py:56`<br>`fastQA/app/services/file_route_service.py:64`<br>`highThinkingQA/config.py:253`<br>`highThinkingQA/config.py:268`<br>`highThinkingQA/config.py:279`<br>+8 more | "" |
| `OPENAI_CONNECT_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:23` = `15` | `fastQA/app/integrations/llm/shared_http_pool.py:97` |  |
| `OPENAI_MODEL` | `resource/config/shared/model-endpoints.shared.env:19` = `deepseek-v3.1` | `fastQA/app/modules/generation_pipeline/query_expander.py:41` default="qwen-plus"<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:85`<br>`fastQA/app/modules/qa_pdf/llm_factory.py:62` default=os.getenv("DASHSCOPE_MODEL", "deepseek-v3.1"<br>`highThinkingQA/server/services/documents_service.py:49` default=""<br>`patent/server/patent/answering.py:955`<br>`patent/server/patent/hybrid_synthesis.py:277`<br>`patent/server/patent/pdf_service.py:885`<br>`patent/server/patent/runtime.py:260` default=""<br>+3 more | "qwen-plus"<br>os.getenv("DASHSCOPE_MODEL", "deepseek-v3.1"<br>"" |
| `OPENAI_POOL_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:27` = `30` | `fastQA/app/integrations/llm/shared_http_pool.py:131` |  |
| `OPENAI_READ_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:24` = `180` | `fastQA/app/integrations/llm/shared_http_pool.py:106` |  |
| `OPENAI_STREAM_READ_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:25` = `600` | 未命中直接字符串引用 | 无直接代码默认值 |
| `OPENAI_WRITE_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:26` = `180` | `fastQA/app/integrations/llm/shared_http_pool.py:122` |  |
| `OUTBOX_MAX_ATTEMPTS` | `resource/config/services/public-service/config.shared.env:59` = `20` | `highThinkingQA/server/services/conversation/chat_json_outbox_worker.py:48` default=20<br>`public-service/backend/app/modules/conversation/outbox_worker.py:49` default=20 | 20 |
| `OUTBOX_PROCESSING_TIMEOUT_SECONDS` | `resource/config/services/public-service/config.shared.env:62` = `120` | `highThinkingQA/server/services/conversation/chat_json_outbox_worker.py:52`<br>`public-service/backend/app/modules/conversation/outbox_worker.py:52` default=120 | 120 |
| `OUTBOX_RETRY_BASE_SECONDS` | `resource/config/services/public-service/config.shared.env:60` = `2` | `highThinkingQA/server/services/conversation/chat_json_outbox_worker.py:49` default=2<br>`public-service/backend/app/modules/conversation/outbox_worker.py:50` default=2 | 2 |
| `OUTBOX_RETRY_MAX_SECONDS` | `resource/config/services/public-service/config.shared.env:61` = `300` | `highThinkingQA/server/services/conversation/chat_json_outbox_worker.py:50` default=300<br>`public-service/backend/app/modules/conversation/outbox_worker.py:51` default=300 | 300 |
| `OUTBOX_WORKER_BATCH_SIZE` | `resource/config/services/public-service/config.shared.env:57` = `100` | `highThinkingQA/server/services/conversation/chat_json_outbox_worker.py:46` default=100<br>`public-service/backend/app/modules/conversation/outbox_worker.py:47` default=100 | 100 |
| `OUTBOX_WORKER_POLL_INTERVAL_MS` | `resource/config/services/public-service/config.shared.env:58` = `1000` | `highThinkingQA/server/services/conversation/chat_json_outbox_worker.py:47` default=1000<br>`public-service/backend/app/modules/conversation/outbox_worker.py:48` default=1000 | 1000 |
| `PAPERS_DIR` | `resource/config/services/fastQA/config.shared.env:39` = `/home/cqy/worktrees/highThinking/resource/fastqa/papers`<br>`resource/config/services/highThinkingQA/config.shared.env:49` = `../../../highThinkingQA/papers`<br>`resource/config/services/public-service/config.shared.env:15` = `papers` | `fastQA/app/core/config.py:443`<br>`highThinkingQA/config.py:313`<br>`highThinkingQA/config.py:421`<br>`highThinkingQA/ingest/pipeline.py:226`<br>`highThinkingQA/server/services/ask_service.py:150`<br>`highThinkingQA/server/services/documents_service.py:27`<br>`public-service/backend/app/core/config.py:199` |  |
| `PASSWORD_EXPIRE_DAYS` | `resource/config/services/highThinkingQA/config.shared.env:82` = `180`<br>`resource/config/services/public-service/config.shared.env:29` = `180` | `highThinkingQA/server/services/auth_service.py:87` default="180"<br>`public-service/backend/app/modules/auth/service.py:248` default="180" | "180" |
| `PATENT_ASK_EXECUTOR_MAX_WORKERS` | `resource/config/services/patent/config.shared.env:6` = `4` | `patent/config.py:276` default=4 | 4 |
| `PATENT_ASK_STREAM_MAX_CONCURRENT` | `resource/config/services/patent/config.shared.env:5` = `8` | `patent/config.py:275` default=8 | 8 |
| `PATENT_BACKEND_BASE_URL` | `resource/config/shared/infrastructure.shared.env:20` = `http://127.0.0.1:8010` | `gateway/app/core/config.py:119` default="http://127.0.0.1:8010"<br>`gateway/scripts/run_gunicorn_foreground.sh:25` default=http://127.0.0.1:8010<br>`gateway/scripts/start_gunicorn.sh:33` default=http://127.0.0.1:8010 | "http://127.0.0.1:8010"<br>http://127.0.0.1:8010 |
| `PATENT_DURABLE_AUTHORITY_ENABLED` | `resource/config/services/patent/config.shared.env:31` = `true` | `patent/config.py:289` default=False<br>`patent/scripts/start.sh:26` default=true<br>`patent/scripts/start_gunicorn.sh:31` default=true | False<br>true |
| `PATENT_DURABLE_MODE_ENABLED` | `resource/config/services/patent/config.shared.env:30` = `true` | `patent/config.py:258` default=True<br>`patent/scripts/start.sh:25` default=true<br>`patent/scripts/start_gunicorn.sh:30` default=true | True<br>true |
| `PATENT_EMBEDDING_API_MODEL` | `resource/config/shared/model-endpoints.shared.env:41` = `bge-local` | `patent/server/patent/runtime.py:328` |  |
| `PATENT_EMBEDDING_API_TIMEOUT_SECONDS` | `resource/config/services/patent/config.shared.env:33` = `20`<br>`resource/config/shared/model-endpoints.shared.env:42` = `20` | `patent/server/patent/runtime.py:326` default="20" | "20" |
| `PATENT_EMBEDDING_API_URL` | `resource/config/shared/model-endpoints.shared.env:40` = `http://127.0.0.1:8001/v1/embeddings` | `patent/server/patent/runtime.py:327` |  |
| `PATENT_EMBEDDING_BASE_URL` | `resource/config/shared/model-endpoints.shared.env:37` = `http://127.0.0.1:8001/v1/embeddings` | 未命中直接字符串引用 | 无直接代码默认值 |
| `PATENT_EMBEDDING_MODEL` | `resource/config/shared/model-endpoints.shared.env:38` = `bge-local` | 未命中直接字符串引用 | 无直接代码默认值 |
| `PATENT_EMBEDDING_MODEL_TYPE` | `resource/config/shared/model-endpoints.shared.env:39` = `remote` | `patent/server/patent/runtime.py:325` |  |
| `PATENT_ENV` | `resource/config/services/patent/config.shared.env:1` = `dev` | `patent/config.py:254` default="dev"<br>`public-service/backend/app/modules/conversation/service.py:424` default="dev" | "dev" |
| `PATENT_GUNICORN_THREADS` | `resource/config/services/patent/config.shared.env:3` = `8` | `patent/config.py:267` default=8 | 8 |
| `PATENT_GUNICORN_TIMEOUT` | `resource/config/services/patent/config.shared.env:4` = `120` | `patent/config.py:268` default=120 | 120 |
| `PATENT_GUNICORN_WORKERS` | `resource/config/services/patent/config.shared.env:2` = `8` | `patent/config.py:266` default=4 | 4 |
| `PATENT_HOST` | `resource/config/shared/infrastructure.shared.env:13` = `0.0.0.0` | `patent/config.py:262` default="0.0.0.0" | "0.0.0.0" |
| `PATENT_LLM_HTTP_SHARED_POOL_ENABLED` | `resource/config/services/patent/config.shared.env:36` = `true` | `patent/config.py:297` default=False<br>`patent/server/patent/upstream_http.py:58` default=default=False | False<br>default=False<br>最终策略：patent LLM 共享 HTTP 池已确认必开，开关写死启用；HTTP timeout、连接池大小、keepalive/pool 参数继续保留配置。 |
| `PATENT_NEO4J_DATABASE` | `resource/config/shared/graph.shared.env:6` = `neo4j` | `patent/config.py:332` default="neo4j" | "neo4j" |
| `PATENT_NEO4J_PASSWORD` | `resource/config/shared/graph.secret.env.example:2` = `` | `patent/config.py:331` default="" | "" |
| `PATENT_NEO4J_URL` | `resource/config/shared/graph.shared.env:4` = `bolt://127.0.0.1:8687` | `patent/config.py:329` default="bolt://127.0.0.1:8687" | "bolt://127.0.0.1:8687" |
| `PATENT_NEO4J_USERNAME` | `resource/config/shared/graph.shared.env:5` = `neo4j` | `patent/config.py:330` default="neo4j" | "neo4j" |
| `PATENT_OPENAI_BASE_URL` | `resource/config/shared/model-endpoints.shared.env:20` = `https://dashscope.aliyuncs.com/compatible-mode/v1` | `patent/server/patent/answering.py:946`<br>`patent/server/patent/hybrid_synthesis.py:269`<br>`patent/server/patent/pdf_service.py:877`<br>`patent/server/patent/runtime.py:269`<br>`patent/server/patent/tabular_service.py:533` |  |
| `PATENT_OPENAI_MODEL` | `resource/config/shared/model-endpoints.shared.env:21` = `deepseek-v3.1` | `patent/server/patent/answering.py:953`<br>`patent/server/patent/hybrid_synthesis.py:275`<br>`patent/server/patent/pdf_service.py:883`<br>`patent/server/patent/runtime.py:275`<br>`patent/server/patent/tabular_service.py:539` |  |
| `PATENT_OPENAI_TIMEOUT_SECONDS` | `resource/config/services/patent/config.shared.env:35` = `30` | `patent/server/patent/answering.py:959` default="30"<br>`patent/server/patent/hybrid_synthesis.py:286` default=30.0<br>`patent/server/patent/pdf_service.py:894` default=30.0<br>`patent/server/patent/runtime.py:282`<br>`patent/server/patent/tabular_service.py:550` default=30.0 | "30"<br>30.0 |
| `PATENT_PLANNING_HOT_POOL_ENABLED` | `resource/config/services/patent/config.shared.env:37` = `true` | `patent/config.py:308` default=False<br>`patent/server/patent/planning_hot_pool.py:61` default=False | False<br>最终策略：patent planning hot pool 已确认必开，开关写死启用；`PATENT_PLANNING_HOT_POOL_LANE_COUNT`、`PATENT_PLANNING_HOT_POOL_LANE_DEGRADED_AFTER_SECONDS` 继续保留配置；warm interval、warm timeout、warm jitter、warm active window 属于预热参数，不再保留配置。 |
| `PATENT_PLANNING_HOT_POOL_WARMUP_ENABLED` | `resource/config/services/patent/config.shared.env:38` = `true` | `patent/config.py:310` default=False<br>`patent/server/patent/planning_hot_pool.py:69` default=False | False |
| `PATENT_PLANNING_UPSTREAM_GATE_ENABLED` | `resource/config/services/patent/config.shared.env:39` = `true` | `patent/config.py:322` default=False<br>`patent/server/patent/upstream_gate.py:79` default=False | False<br>最终策略：patent planning upstream gate 已确认必开，开关写死启用；gate limit 和相关容量参数继续保留配置。 |
| `PATENT_PORT` | `resource/config/shared/infrastructure.shared.env:14` = `8010` | `patent/config.py:263` default=8787<br>`patent/scripts/start.sh:24` default=8010<br>`patent/scripts/start_gunicorn.sh:29` default=8010<br>`patent/scripts/start_gunicorn.sh:95`<br>`patent/scripts/status_gunicorn.sh:16` default=8010<br>`patent/scripts/status_gunicorn.sh:34`<br>`patent/scripts/status_gunicorn.sh:35`<br>`patent/scripts/status_gunicorn.sh:45`<br>+6 more | 8787<br>8010 |
| `PATENT_REDIS_ENABLED` | `resource/config/services/patent/config.shared.env:28` = `true` | `patent/config.py:280` default=False<br>`patent/scripts/start.sh:29` default=true<br>`patent/scripts/start_gunicorn.sh:34` default=true | False<br>true<br>最终策略：patent Redis 已确认必开，开关写死启用；patent Redis 连接和 namespace 参数继续保留配置。 |
| `PATENT_REDIS_KEY_PREFIX` | `resource/config/services/patent/config.shared.env:29` = `patent` | `patent/config.py:255` default="patent"<br>`public-service/backend/app/modules/conversation/service.py:425` default="patent" | "patent" |
| `PATENT_STAGE2_CONVERGENCE_ENABLED` | `resource/config/services/patent/config.shared.env:7` = `true` | `patent/server/patent/stage2_controls.py:64` default=False | False |
| `PATENT_STAGE2_C_GLOBAL_CHUNK_RECALL_ENABLED` | `resource/config/services/patent/config.shared.env:23` = `true` | `patent/server/patent/stage2_controls.py:75` default=False | False |
| `PATENT_STAGE2_C_PATENT_SCORING_ENABLED` | `resource/config/services/patent/config.shared.env:22` = `true` | `patent/server/patent/stage2_controls.py:74` default=False | False |
| `PATENT_STAGE2_C_TABLE_METRIC_BOOST_ENABLED` | `resource/config/services/patent/config.shared.env:24` = `true` | `patent/server/patent/stage2_controls.py:76` default=False | False |
| `PATENT_STAGE2_ENTITY_LOCK_ENABLED` | `resource/config/services/patent/config.shared.env:9` = `true` | `patent/server/patent/stage2_controls.py:66` default=True | True |
| `PATENT_STAGE2_FORCE_KEYWORD_INJECTION` | `resource/config/services/patent/config.shared.env:8` = `true` | `patent/server/patent/stage2_controls.py:65` default=True | True |
| `PATENT_STAGE2_MAX_GLOBAL_PATENTS` | `resource/config/services/patent/config.shared.env:20` = `20` | `patent/server/patent/stage2_controls.py:72` default=20 | 20 |
| `PATENT_STAGE2_MAX_RESULTS_PER_CLAIM` | `resource/config/services/patent/config.shared.env:19` = `5` | `patent/server/patent/stage2_controls.py:71` default=5 | 5 |
| `PATENT_STAGE2_MIN_RESULTS_PER_CLAIM` | `resource/config/services/patent/config.shared.env:18` = `2` | `patent/server/patent/stage2_controls.py:70` default=2 | 2 |
| `PATENT_STAGE2_RERANK_API_KEY` | `resource/config/shared/model-endpoints.secret.env.example:7` = `` | `patent/server/patent/rerank_service.py:189` default="" | "" |
| `PATENT_STAGE2_RERANK_BASE_URL` | `resource/config/services/patent/config.shared.env:14` = `http://localhost:8084`<br>`resource/config/shared/model-endpoints.shared.env:53` = `http://localhost:8084` | `patent/server/patent/rerank_service.py:194` default=default_base_url<br>`patent/server/patent/stage2_controls.py:79` default="" | default_base_url<br>"" |
| `PATENT_STAGE2_RERANK_CANDIDATES` | `resource/config/services/patent/config.shared.env:11` = `80` | `patent/server/patent/stage2_controls.py:68` default=80 | 80 |
| `PATENT_STAGE2_RERANK_ENABLED` | `resource/config/services/patent/config.shared.env:10` = `true` | `patent/server/patent/stage2_controls.py:67` default=True | True |
| `PATENT_STAGE2_RERANK_ENDPOINT_FAMILY` | `resource/config/services/patent/config.shared.env:17` = ``<br>`resource/config/shared/model-endpoints.shared.env:56` = `` | `patent/server/patent/stage2_controls.py:81` default="" | "" |
| `PATENT_STAGE2_RERANK_MODEL` | `resource/config/services/patent/config.shared.env:15` = `qwen3-vl-rerank`<br>`resource/config/shared/model-endpoints.shared.env:54` = `qwen3-vl-rerank` | `patent/server/patent/rerank_service.py:195` default="qwen3-vl-rerank"<br>`patent/server/patent/stage2_controls.py:78` default="" | "qwen3-vl-rerank"<br>"" |
| `PATENT_STAGE2_RERANK_PROVIDER` | `resource/config/services/patent/config.shared.env:13` = `local`<br>`resource/config/shared/model-endpoints.shared.env:52` = `local` | `patent/server/patent/rerank_service.py:186` default="none"<br>`patent/server/patent/stage2_controls.py:77` default="none" | "none" |
| `PATENT_STAGE2_RERANK_TIMEOUT_SECONDS` | `resource/config/services/patent/config.shared.env:16` = `20`<br>`resource/config/shared/model-endpoints.shared.env:55` = `20` | `patent/server/patent/rerank_service.py:196`<br>`patent/server/patent/stage2_controls.py:80` default=20.0 | 20.0 |
| `PATENT_STAGE2_RERANK_TOP_PATENTS` | `resource/config/services/patent/config.shared.env:12` = `20` | `patent/server/patent/stage2_controls.py:69` default=20 | 20 |
| `PATENT_STAGE2_VALIDATION_ENABLED` | `resource/config/services/patent/config.shared.env:21` = `true` | `patent/server/patent/stage2_controls.py:73` default=True | True |
| `PATENT_STAGE4_MIN_CITATIONS` | `resource/config/services/patent/config.shared.env:26` = `10` | `patent/server/patent/orchestrators/generation.py:156` default=""<br>`patent/server/patent/stages/synthesis.py:454` default=10 | ""<br>10 |
| `PATENT_STAGE4_REFERENCE_TOPK` | `resource/config/services/patent/config.shared.env:25` = `20` | `patent/server/patent/orchestrators/generation.py:155` default=""<br>`patent/server/patent/stages/synthesis.py:453` default=20 | ""<br>20 |
| `PDFQA_SIDECAR_BASE_URL_INTERNAL` | `resource/config/services/fastQA/config.shared.env:99` = `http://127.0.0.1:8012` | `fastQA/app/modules/qa_pdf/service.py:114` |  |
| `PDFQA_SIDECAR_SELF_PORT` | `resource/config/services/fastQA/config.shared.env:100` = `8008` | `fastQA/app/modules/qa_pdf/service.py:47` |  |
| `PDF_CHUNKS_DIR` | `resource/config/services/fastQA/config.shared.env:40` = `/home/cqy/worktrees/highThinking/resource/fastqa/pdf_chunks` | `fastQA/app/core/config.py:444` |  |
| `PDF_QA_MAX_PDF_CHARS` | `resource/config/services/fastQA/config.shared.env:95` = `50000` | `fastQA/app/services/file_route_service.py:104` default="50000"<br>`fastQA/app/services/file_routes.py:181` default=default=12000 | "50000"<br>default=12000 |
| `PDF_QA_MAX_RETRIES` | `resource/config/services/fastQA/config.shared.env:104` = `0` | `fastQA/app/modules/qa_pdf/llm_factory.py:67` default=3 | 3 |
| `PDF_QA_MAX_TOKENS` | `resource/config/services/fastQA/config.shared.env:105` = `1800` | `fastQA/app/modules/qa_pdf/llm_factory.py:66` default=2500<br>`fastQA/app/services/file_route_service.py:79` default="2500"<br>`patent/server/patent/pdf_service.py:900` default=2500 | 2500<br>"2500" |
| `PDF_QA_MODEL` | `resource/config/services/fastQA/config.shared.env:102` = `deepseek-v3.1` | `fastQA/app/modules/qa_pdf/llm_factory.py:61` |  |
| `PDF_QA_TEMPERATURE` | `resource/config/services/fastQA/config.shared.env:106` = `0.5` | `fastQA/app/modules/qa_pdf/llm_factory.py:64` default=0.5 | 0.5 |
| `PDF_QA_TIMEOUT_SECONDS` | `resource/config/services/fastQA/config.shared.env:103` = `45` | 未命中直接字符串引用 | 无直接代码默认值 |
| `PDF_QA_TOP_P` | `resource/config/services/fastQA/config.shared.env:107` = `0.95` | `fastQA/app/modules/qa_pdf/llm_factory.py:65` default=0.95 | 0.95 |
| `PDF_QA_USE_DEDICATED_LLM` | `resource/config/services/fastQA/config.shared.env:101` = `true` | 未命中直接字符串引用 | 无直接代码默认值 |
| `PDF_QA_WARMUP_ENABLED` | `resource/config/services/fastQA/config.shared.env:108` = `false` | 未命中直接字符串引用 | 无直接代码默认值 |
| `PROMPTS_DIR` | `resource/config/services/highThinkingQA/config.shared.env:50` = `prompts` | `highThinkingQA/agent_core/llm_client.py:230`<br>`highThinkingQA/config.py:314`<br>`highThinkingQA/config.py:422` |  |
| `PUBLIC_BACKEND_BASE_URL` | `resource/config/shared/infrastructure.shared.env:17` = `http://127.0.0.1:8102` | `gateway/app/core/config.py:117` default="http://127.0.0.1:8102"<br>`gateway/scripts/run_gunicorn_foreground.sh:22` default=http://127.0.0.1:8102<br>`gateway/scripts/start_gunicorn.sh:30` default=http://127.0.0.1:8102 | "http://127.0.0.1:8102"<br>http://127.0.0.1:8102 |
| `PUBLIC_SERVICE_API_PREFIX` | `resource/config/services/public-service/config.shared.env:7` = `/api` | `public-service/backend/app/core/config.py:223` default="/api" | "/api" |
| `PUBLIC_SERVICE_APP_NAME` | `resource/config/services/public-service/config.shared.env:5` = `agentCode Public Service` | `public-service/backend/app/core/config.py:218` default="agentCode Public Service" | "agentCode Public Service" |
| `PUBLIC_SERVICE_CORS_ORIGINS` | `resource/config/services/public-service/config.shared.env:10` = `http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:5174,http://localhost:5174` | `public-service/backend/app/core/config.py:193` default=os.getenv("BACKEND_CORS_ORIGINS", "*" | os.getenv("BACKEND_CORS_ORIGINS", "*" |
| `PUBLIC_SERVICE_DEBUG` | `resource/config/services/public-service/config.shared.env:6` = `0` | `public-service/backend/app/core/config.py:220` default=False | False |
| `PUBLIC_SERVICE_DOCS_URL` | `resource/config/services/public-service/config.shared.env:8` = `/docs` | `public-service/backend/app/core/config.py:224` default="/docs" | "/docs" |
| `PUBLIC_SERVICE_ENABLE_LEGACY_CONVERSATION_FALLBACK` | `resource/config/services/public-service/config.shared.env:25` = `0` | `public-service/backend/app/core/config.py:270` default=False | False |
| `PUBLIC_SERVICE_GUNICORN_WORKERS` | `resource/config/services/public-service/config.shared.env:11` = `8` | `public-service/scripts/start_gunicorn.sh:27` default=4<br>`public-service/scripts/start_gunicorn.sh:58` | 4 |
| `PUBLIC_SERVICE_HOST` | `resource/config/shared/infrastructure.shared.env:6` = `0.0.0.0` | `public-service/backend/app/core/config.py:221` default="0.0.0.0" | "0.0.0.0" |
| `PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN` | `resource/config/services/fastQA/config.secret.env:1` = `<redacted:set>`<br>`resource/config/services/gateway/config.secret.env:1` = `<redacted:set>`<br>`resource/config/services/gateway/config.secret.env.example:1` = ``<br>`resource/config/services/highThinkingQA/config.secret.env:28` = `<redacted:set>`<br>`resource/config/shared/infrastructure.secret.env.example:3` = `` | `fastQA/app/routers/qa.py:215` default=""<br>`fastQA/app/services/chat_persistence.py:22` default=""<br>`fastQA/app/services/conversation_authority_client.py:13`<br>`gateway/app/services/conversation_persistence.py:603` default=""<br>`gateway/app/services/quota_proxy.py:121` default=""<br>`highThinkingQA/server/services/chat_persistence.py:25` default=""<br>`highThinkingQA/server/services/conversation_authority_client.py:13`<br>`highThinkingQA/server_fastapi/routers/ask.py:185` default=""<br>+3 more | "" |
| `PUBLIC_SERVICE_LOGS_DIR` | `resource/config/services/public-service/config.shared.env:23` = `logs` | `public-service/backend/app/core/config.py:211` |  |
| `PUBLIC_SERVICE_NEO4J_DATABASE` | `resource/config/shared/graph.shared.env:9` = `neo4j` | `public-service/backend/app/core/config.py:254` default="neo4j" | "neo4j" |
| `PUBLIC_SERVICE_NEO4J_PASSWORD` | `resource/config/shared/graph.secret.env.example:3` = `` | `public-service/backend/app/core/config.py:252` default="" | "" |
| `PUBLIC_SERVICE_NEO4J_URL` | `resource/config/shared/graph.shared.env:7` = `bolt://127.0.0.1:7688` | `public-service/backend/app/core/config.py:248` default="" | "" |
| `PUBLIC_SERVICE_NEO4J_USERNAME` | `resource/config/shared/graph.shared.env:8` = `neo4j` | `public-service/backend/app/core/config.py:250` default="neo4j" | "neo4j" |
| `PUBLIC_SERVICE_OPENAPI_URL` | `resource/config/services/public-service/config.shared.env:9` = `/openapi.json` | `public-service/backend/app/core/config.py:225` default="/openapi.json" | "/openapi.json" |
| `PUBLIC_SERVICE_PORT` | `resource/config/shared/infrastructure.shared.env:7` = `8102` | `public-service/backend/app/core/config.py:222` default=8102<br>`public-service/scripts/start_gunicorn.sh:25` default=8102<br>`public-service/scripts/start_gunicorn.sh:57`<br>`public-service/scripts/start_gunicorn.sh:71`<br>`public-service/scripts/status_gunicorn.sh:7` default=8102<br>`public-service/scripts/stop_gunicorn.sh:6` default=8102<br>`scripts/_service_common.sh:35`<br>`scripts/_service_common.sh:74` default=8102 | 8102 |
| `PUBLIC_SERVICE_SERVICE_ASSET_ROOT` | `resource/config/shared/resource-roots.env.example:15` = `/home/cqy/worktrees/highThinking/resource/assets` | 未命中直接字符串引用 | 无直接代码默认值 |
| `PUBLIC_SERVICE_SERVICE_CONFIG_ROOT` | `resource/config/shared/resource-roots.env.example:12` = `/home/cqy/worktrees/highThinking/resource/config/services/public-service` | 未命中直接字符串引用 | 无直接代码默认值 |
| `PUBLIC_SERVICE_SERVICE_RUNTIME_ROOT` | `resource/config/shared/resource-roots.env.example:14` = `/home/cqy/worktrees/highThinking/resource/runtime/dev/public-service` | 未命中直接字符串引用 | 无直接代码默认值 |
| `PUBLIC_SERVICE_SERVICE_STATE_ROOT` | `resource/config/shared/resource-roots.env.example:13` = `/home/cqy/worktrees/highThinking/resource/state/dev/public-service` | 未命中直接字符串引用 | 无直接代码默认值 |
| `QA_QUERY_PIPELINE_MODE` | `resource/config/services/fastQA/config.shared.env:47` = `new` | `fastQA/app/modules/qa_kb/service.py:65` default="new"<br>`fastQA/app/modules/qa_kb/service.py:78` | "new" |
| `QA_RETRIEVAL_RERANK_API_KEY` | `resource/config/services/fastQA/config.shared.env:49` = ``<br>`resource/config/shared/model-endpoints.secret.env.example:6` = `` | `fastQA/app/core/runtime.py:576` default=""<br>`fastQA/app/modules/microscopic_expert.py:157` default="" | "" |
| `QA_RETRIEVAL_RERANK_BASE_URL` | `resource/config/shared/model-endpoints.shared.env:49` = `http://localhost:8084` | `fastQA/app/core/runtime.py:588` default=rerank_default_base_url<br>`fastQA/app/modules/microscopic_expert.py:165` default=default_base_url | rerank_default_base_url<br>default_base_url |
| `QA_RETRIEVAL_RERANK_CANDIDATES` | `resource/config/services/fastQA/config.shared.env:48` = `50` | `fastQA/app/modules/generation_pipeline/stage2_retrieval.py:77`<br>`fastQA/app/modules/qa_cache/stage2_cache.py:87` default="50" | "50" |
| `QA_RETRIEVAL_RERANK_MODEL` | `resource/config/shared/model-endpoints.shared.env:50` = `qwen3-vl-rerank` | `fastQA/app/core/runtime.py:591` default="qwen3-vl-rerank"<br>`fastQA/app/modules/microscopic_expert.py:167` default="qwen3-vl-rerank"<br>`fastQA/app/modules/qa_cache/stage2_cache.py:89` default="qwen3-vl-rerank" | "qwen3-vl-rerank" |
| `QA_RETRIEVAL_RERANK_PROVIDER` | `resource/config/shared/model-endpoints.shared.env:48` = `local` | `fastQA/app/core/runtime.py:573` default="dashscope"<br>`fastQA/app/modules/microscopic_expert.py:155` default="dashscope"<br>`fastQA/app/modules/qa_cache/stage2_cache.py:88` default="dashscope" | "dashscope" |
| `QA_RETRIEVAL_RERANK_TIMEOUT` | `resource/config/shared/model-endpoints.shared.env:51` = `20` | `fastQA/app/modules/microscopic_expert.py:169` default="20" | "20" |
| `QA_SOURCE_DOI_MAX_PER_COMPARISON_OBJECT` | `resource/config/services/fastQA/config.shared.env:68` = `5` | `fastQA/app/modules/qa_kb/orchestrators/generation.py:160` |  |
| `QA_SOURCE_DOI_MAX_TOTAL` | `resource/config/services/fastQA/config.shared.env:66` = `15` | `fastQA/app/modules/qa_kb/orchestrators/generation.py:158` |  |
| `QA_SOURCE_DOI_MAX_TOTAL_NON_COMPARISON` | `resource/config/services/fastQA/config.shared.env:67` = `20` | `fastQA/app/modules/qa_kb/orchestrators/generation.py:159` |  |
| `QA_STAGE1_CACHE_TTL_SECONDS` | `resource/config/services/fastQA/config.shared.env:63` = `43200` | `fastQA/app/modules/qa_cache/stage1_cache.py:17` default="43200"<br>`public-service/backend/app/modules/system/service.py:42` default="3600" | "43200"<br>"3600" |
| `QA_STAGE25_CACHE_TTL_SECONDS` | `resource/config/services/fastQA/config.shared.env:65` = `43200` | `fastQA/app/modules/qa_cache/stage25_cache.py:21` default="43200" | "43200" |
| `QA_STAGE25_MD_CHUNKS_PER_DOI` | `resource/config/services/fastQA/config.shared.env:71` = `5` | `fastQA/app/modules/generation_pipeline/md_expansion.py:32`<br>`fastQA/app/modules/qa_cache/stage25_cache.py:55` default="5" | "5" |
| `QA_STAGE25_MD_GLOBAL_MAX_NEW_DOIS` | `resource/config/services/fastQA/config.shared.env:74` = `5` | `fastQA/app/modules/generation_pipeline/md_expansion.py:38`<br>`fastQA/app/modules/qa_cache/stage25_cache.py:58` default="5" | "5" |
| `QA_STAGE25_MD_GLOBAL_MIN_SCORE` | `resource/config/services/fastQA/config.shared.env:75` = `0` | `fastQA/app/modules/generation_pipeline/md_expansion.py:39` default="0"<br>`fastQA/app/modules/qa_cache/stage25_cache.py:59` default="0" | "0" |
| `QA_STAGE25_MD_GLOBAL_SUPPLEMENT_ENABLED` | `resource/config/services/fastQA/config.shared.env:72` = `true` | `fastQA/app/modules/generation_pipeline/md_expansion.py:36`<br>`fastQA/app/modules/qa_cache/stage25_cache.py:56` default="1" | "1" |
| `QA_STAGE25_MD_GLOBAL_TOPK` | `resource/config/services/fastQA/config.shared.env:73` = `20` | `fastQA/app/modules/generation_pipeline/md_expansion.py:37`<br>`fastQA/app/modules/qa_cache/stage25_cache.py:57` default="20" | "20" |
| `QA_STAGE25_MD_MAX_DOIS` | `resource/config/services/fastQA/config.shared.env:70` = `20` | `fastQA/app/modules/generation_pipeline/md_expansion.py:30`<br>`fastQA/app/modules/qa_cache/stage25_cache.py:54` default="20" | "20" |
| `QA_STAGE2_CACHE_TTL_SECONDS` | `resource/config/services/fastQA/config.shared.env:64` = `43200` | `fastQA/app/modules/qa_cache/stage2_cache.py:25` default="43200"<br>`public-service/backend/app/modules/system/service.py:43` default="1800" | "43200"<br>"1800" |
| `QA_STAGE2_DYNAMIC_WORKERS_ENABLED` | `resource/config/services/fastQA/config.shared.env:56` = `false` | `fastQA/app/modules/generation_pipeline/stage2_retrieval.py:90` |  |
| `QA_STAGE2_DYNAMIC_WORKERS_MIN` | `resource/config/services/fastQA/config.shared.env:58` = `3` | `fastQA/app/modules/generation_pipeline/stage2_retrieval.py:102` |  |
| `QA_STAGE2_DYNAMIC_WORKERS_STEP` | `resource/config/services/fastQA/config.shared.env:59` = `1` | `fastQA/app/modules/generation_pipeline/stage2_retrieval.py:103` |  |
| `QA_STAGE2_DYNAMIC_WORKERS_TRIGGER_ACTIVE` | `resource/config/services/fastQA/config.shared.env:57` = `4` | `fastQA/app/modules/generation_pipeline/stage2_retrieval.py:101` |  |
| `QA_STAGE2_PARALLEL_WORKERS` | `resource/config/services/fastQA/config.shared.env:55` = `5` | `fastQA/app/modules/generation_pipeline/stage2_retrieval.py:727` |  |
| `QA_STAGE2_QUERY_EXPANSION_ENABLED` | `resource/config/services/fastQA/config.shared.env:61` = `false` | `fastQA/app/modules/generation_pipeline/stage2_retrieval.py:732`<br>`fastQA/app/modules/qa_cache/stage2_cache.py:90` default="0" | "0" |
| `QA_STAGE2_RETRIEVAL_VERSION` | `resource/config/services/fastQA/config.shared.env:62` = `1` | `fastQA/app/modules/qa_cache/stage2_cache.py:21` default="1" | "1" |
| `QA_STAGE35_EVIDENCE_RERANK_ENABLED` | `resource/config/services/fastQA/config.shared.env:76` = `true` | `fastQA/app/modules/generation_pipeline/evidence_rerank.py:144`<br>`fastQA/app/modules/qa_kb/orchestrators/generation.py:546` |  |
| `QA_STAGE35_EVIDENCE_TOPK_PER_COMPARISON_OBJECT` | `resource/config/services/fastQA/config.shared.env:79` = `8` | `fastQA/app/modules/generation_pipeline/evidence_rerank.py:169` |  |
| `QA_STAGE35_EVIDENCE_TOPK_PER_DOI` | `resource/config/services/fastQA/config.shared.env:78` = `3` | `fastQA/app/modules/generation_pipeline/evidence_rerank.py:164` |  |
| `QA_STAGE35_EVIDENCE_TOPK_TOTAL` | `resource/config/services/fastQA/config.shared.env:77` = `30` | `fastQA/app/modules/generation_pipeline/evidence_rerank.py:159` |  |
| `QA_STAGE3_CACHE_TTL_SECONDS` | `resource/config/services/fastQA/config.shared.env:69` = `43200` | `fastQA/app/modules/qa_cache/stage3_cache.py:22` default="43200" | "43200" |
| `QA_STAGE3_SKIP_PDF_MIN_MD_CHUNKS` | `resource/config/services/fastQA/config.shared.env:92` = `3` | `fastQA/app/modules/generation_pipeline/md_expansion.py:384` |  |
| `QA_STAGE3_SKIP_PDF_MIN_MD_HIT_DOIS` | `resource/config/services/fastQA/config.shared.env:91` = `1` | `fastQA/app/modules/generation_pipeline/md_expansion.py:379` |  |
| `QA_STAGE3_SKIP_PDF_WHEN_MD_HIT` | `resource/config/services/fastQA/config.shared.env:90` = `false` | `fastQA/app/modules/generation_pipeline/md_expansion.py:377` |  |
| `QA_STAGE4_CITATION_VERIFY_AFTER_SYNTHESIS` | `resource/config/services/fastQA/config.shared.env:83` = `true` | `fastQA/app/modules/generation_pipeline/synthesis_streaming.py:790` |  |
| `QA_STAGE4_ELEMENT_GUARD` | `resource/config/services/fastQA/config.shared.env:82` = `true` | `fastQA/app/modules/generation_pipeline/synthesis_postprocess.py:51`<br>`fastQA/app/modules/generation_pipeline/synthesis_streaming.py:788` |  |
| `QA_STAGE4_EMPTY_FACTS_FALLBACK_MODE` | `resource/config/services/fastQA/config.shared.env:86` = `restricted_synthesis` | `fastQA/app/modules/generation_pipeline/synthesis_streaming.py:856` default="restricted_synthesis" | "restricted_synthesis" |
| `QA_STAGE4_EVIDENCE_CHUNKS_PER_DOI` | `resource/config/services/fastQA/config.shared.env:88` = `3` | `fastQA/app/modules/generation_pipeline/reference_alignment.py:180` |  |
| `QA_STAGE4_EVIDENCE_CHUNK_MAX_CHARS` | `resource/config/services/fastQA/config.shared.env:89` = `800` | `fastQA/app/modules/generation_pipeline/reference_alignment.py:186` |  |
| `QA_STAGE4_MIN_CITATIONS` | `resource/config/services/fastQA/config.shared.env:81` = `3` | `fastQA/app/modules/generation_pipeline/synthesis_postprocess.py:45`<br>`fastQA/app/modules/generation_pipeline/synthesis_streaming.py:785` |  |
| `QA_STAGE4_REFERENCE_TOPK` | `resource/config/services/fastQA/config.shared.env:80` = `20` | `fastQA/app/modules/generation_pipeline/synthesis_postprocess.py:43`<br>`fastQA/app/modules/generation_pipeline/synthesis_streaming.py:784` |  |
| `QA_STAGE4_REQUIRE_FACTS_FOR_DOI_SYNTHESIS` | `resource/config/services/fastQA/config.shared.env:85` = `true` | `fastQA/app/modules/generation_pipeline/synthesis_streaming.py:855` |  |
| `QA_STAGE4_STRUCTURE_ONLY_MODE` | `resource/config/services/fastQA/config.shared.env:87` = `false` | `fastQA/app/modules/generation_pipeline/synthesis_streaming.py:798` |  |
| `QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED` | `resource/config/services/fastQA/config.shared.env:84` = `true` | `fastQA/app/modules/generation_pipeline/synthesis_streaming.py:794` |  |
| `QA_STREAM_CLEAN_FLUSH_CHARS` | `resource/config/services/fastQA/config.shared.env:60` = `384` | 未命中直接字符串引用 | 无直接代码默认值 |
| `QUERY_EXPANSION_MODEL` | `resource/config/services/fastQA/config.shared.env:23` = `qwen3-8b` | `fastQA/app/modules/generation_pipeline/query_expander.py:41` default="qwen-plus"<br>`fastQA/app/modules/qa_cache/stage2_cache.py:91` default="qwen3-8b" | "qwen-plus"<br>"qwen3-8b" |
| `QUOTA_ACTIVE_LIST_CACHE_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:39` = `300` | `public-service/backend/app/modules/quota/cache.py:25` default="300" | "300" |
| `QUOTA_ALL_LIST_CACHE_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:40` = `300` | `public-service/backend/app/modules/quota/cache.py:33` default="300" | "300" |
| `QUOTA_CACHE_EPOCH` | `resource/config/services/public-service/config.shared.env:37` = `0` | `public-service/backend/app/modules/quota/cache.py:13` default="0" | "0" |
| `QUOTA_CONFIG_CACHE_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:38` = `600` | `public-service/backend/app/modules/quota/cache.py:17` default="600" | "600" |
| `QUOTA_LOCK_RETRY_INTERVAL_MS` | `resource/config/services/public-service/config.shared.env:49` = `100` | `public-service/backend/app/modules/quota/service.py:321` default="100" | "100" |
| `QUOTA_LOCK_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:47` = `30` | `public-service/backend/app/modules/quota/service.py:307` default="30" | "30" |
| `QUOTA_LOCK_WAIT_SECONDS` | `resource/config/services/public-service/config.shared.env:48` = `10` | `public-service/backend/app/modules/quota/service.py:314` default="10" | "10" |
| `QUOTA_OVERRIDE_CACHE_TTL_SECONDS` | `resource/config/services/public-service/config.shared.env:41` = `600` | `public-service/backend/app/modules/quota/cache.py:41` default="600" | "600" |
| `REDIS_DB` | `resource/config/services/gateway/config.secret.env:9` = `<redacted:set>`<br>`resource/config/shared/infrastructure.shared.env:25` = `0` | `fastQA/app/core/config.py:332` default=0<br>`gateway/app/core/config.py:151` default=0<br>`highThinkingQA/server/services/redis_client.py:137` default=0<br>`patent/scripts/start.sh:35` default=0<br>`patent/scripts/start.sh:42`<br>`patent/scripts/start_gunicorn.sh:40` default=0<br>`patent/scripts/start_gunicorn.sh:47`<br>`public-service/backend/app/core/config.py:238` default=0 | 0 |
| `REDIS_ENABLED` | `resource/config/services/gateway/config.secret.env:6` = `<redacted:set>`<br>`resource/config/shared/infrastructure.shared.env:22` = `1` | `fastQA/app/core/config.py:326` default=False<br>`gateway/app/core/config.py:121` default=False<br>`highThinkingQA/server/services/redis_client.py:143` default=False<br>`public-service/backend/app/core/config.py:232` default=False | False<br>最终策略：Redis 已确认必开，开关写死启用；Redis URL/host/port/db/password/key prefix/socket timeout 继续保留配置。 |
| `REDIS_HOST` | `resource/config/services/gateway/config.secret.env:7` = `<redacted:set>`<br>`resource/config/shared/infrastructure.shared.env:23` = `127.0.0.1` | `fastQA/app/core/config.py:328` default="127.0.0.1"<br>`gateway/app/core/config.py:147` default="127.0.0.1"<br>`highThinkingQA/server/services/redis_client.py:134` default="127.0.0.1"<br>`patent/scripts/start.sh:33` default=127.0.0.1<br>`patent/scripts/start.sh:42`<br>`patent/scripts/start_gunicorn.sh:38` default=127.0.0.1<br>`patent/scripts/start_gunicorn.sh:47`<br>`public-service/backend/app/core/config.py:234` default="127.0.0.1" | "127.0.0.1"<br>127.0.0.1 |
| `REDIS_KEY_PREFIX` | `resource/config/services/fastQA/config.shared.env:111` = `fastqa`<br>`resource/config/services/gateway/config.secret.env:11` = `<redacted:set>`<br>`resource/config/services/gateway/config.shared.env:9` = `gateway`<br>`resource/config/services/highThinkingQA/config.shared.env:87` = `highthinkingqa`<br>`resource/config/services/public-service/config.shared.env:34` = `public_service` | `fastQA/app/core/config.py:333` default="fastqa"<br>`gateway/app/core/config.py:152` default="gateway"<br>`highThinkingQA/server/services/redis_client.py:145` default="highthinkingqa"<br>`public-service/backend/app/core/config.py:239` default="agentcode" | "fastqa"<br>"gateway"<br>"highthinkingqa"<br>"agentcode" |
| `REDIS_PASSWORD` | `resource/config/services/gateway/config.secret.env:10` = `<redacted:set>`<br>`resource/config/services/gateway/config.secret.env.example:3` = ``<br>`resource/config/services/public-service/config.secret.env.example:6` = ``<br>`resource/config/shared/infrastructure.secret.env.example:10` = `` | `fastQA/app/core/config.py:331` default="123456"<br>`gateway/app/core/config.py:150` default=""<br>`highThinkingQA/server/services/redis_client.py:136` default="123456"<br>`patent/scripts/start.sh:37` default=123456<br>`patent/scripts/start.sh:39`<br>`patent/scripts/start.sh:40`<br>`patent/scripts/start_gunicorn.sh:42` default=123456<br>`patent/scripts/start_gunicorn.sh:44`<br>+2 more | "123456"<br>""<br>123456 |
| `REDIS_PORT` | `resource/config/services/gateway/config.secret.env:8` = `<redacted:set>`<br>`resource/config/shared/infrastructure.shared.env:24` = `6379` | `fastQA/app/core/config.py:329` default=6379<br>`gateway/app/core/config.py:148` default=6379<br>`highThinkingQA/server/services/redis_client.py:135` default=6379<br>`patent/scripts/start.sh:34` default=6379<br>`patent/scripts/start.sh:42`<br>`patent/scripts/start_gunicorn.sh:39` default=6379<br>`patent/scripts/start_gunicorn.sh:47`<br>`public-service/backend/app/core/config.py:235` default=6379 | 6379 |
| `REDIS_SOCKET_CONNECT_TIMEOUT_SEC` | `resource/config/shared/infrastructure.shared.env:26` = `2` | `fastQA/app/core/config.py:334` default=2<br>`gateway/app/core/config.py:153` default=2<br>`highThinkingQA/server/services/redis_client.py:146` default=2<br>`public-service/backend/app/core/config.py:240` default=2 | 2 |
| `REDIS_SOCKET_TIMEOUT_SEC` | `resource/config/shared/infrastructure.shared.env:27` = `2` | `fastQA/app/core/config.py:335` default=2<br>`gateway/app/core/config.py:154` default=2<br>`highThinkingQA/server/services/redis_client.py:147` default=2<br>`public-service/backend/app/core/config.py:241` default=2 | 2 |
| `REDIS_URL` | `resource/config/shared/infrastructure.secret.env.example:12` = `` | `fastQA/app/core/config.py:327` default=""<br>`gateway/app/core/config.py:146` default=""<br>`highThinkingQA/server/services/redis_client.py:131` default=""<br>`public-service/backend/app/core/config.py:233` default="" | "" |
| `REDIS_USERNAME` | `resource/config/shared/infrastructure.secret.env.example:11` = `` | `fastQA/app/core/config.py:330` default=""<br>`gateway/app/core/config.py:149` default=""<br>`patent/scripts/start.sh:36`<br>`patent/scripts/start.sh:39`<br>`patent/scripts/start.sh:40`<br>`patent/scripts/start_gunicorn.sh:41`<br>`patent/scripts/start_gunicorn.sh:44`<br>`patent/scripts/start_gunicorn.sh:45`<br>+1 more | "" |
| `REFERENCE_PREVIEW_MAX_WORKERS` | `resource/config/services/public-service/config.shared.env:70` = `4` | `public-service/backend/app/modules/documents/reference_preview.py:33` default=str(DEFAULT_PREVIEW_MAX_WORKERS | str(DEFAULT_PREVIEW_MAX_WORKERS |
| `RERANK_API_KEY` | `resource/config/shared/model-endpoints.secret.env.example:5` = `` | 未命中直接字符串引用 | 无直接代码默认值 |
| `RERANK_BASE_URL` | `resource/config/shared/model-endpoints.shared.env:45` = `http://localhost:8084` | 未命中直接字符串引用 | 无直接代码默认值 |
| `RERANK_MODEL` | `resource/config/shared/model-endpoints.shared.env:46` = `qwen3-vl-rerank` | 未命中直接字符串引用 | 无直接代码默认值 |
| `RERANK_PROVIDER` | `resource/config/shared/model-endpoints.shared.env:44` = `local` | 未命中直接字符串引用 | 无直接代码默认值 |
| `RERANK_TIMEOUT_SECONDS` | `resource/config/shared/model-endpoints.shared.env:47` = `20` | 未命中直接字符串引用 | 无直接代码默认值 |
| `RESOURCE_ROOT` | `resource/config/shared/resource-roots.env.example:3` = `/home/cqy/worktrees/highThinking/resource` | `fastQA/app/core/config.py:25`<br>`fastQA/app/core/env_loader.py:59`<br>`fastQA/app/routers/health.py:6`<br>`fastQA/app/routers/health.py:98`<br>`gateway/app/core/env_loader.py:57`<br>`highThinkingQA/config.py:27`<br>`highThinkingQA/env_loader.py:60`<br>`patent/config.py:68` default=""<br>+1 more | "" |
| `RETRIEVAL_PIPELINE_BATCH_SIZE` | `resource/config/services/highThinkingQA/config.shared.env:28` = `2` | `highThinkingQA/agent_core/graph.py:424`<br>`highThinkingQA/config.py:306` default=2<br>`highThinkingQA/config.py:415` | 2 |
| `RETRIEVAL_TOP_K` | `resource/config/services/highThinkingQA/config.shared.env:27` = `3` | `highThinkingQA/agent_core/graph.py:422`<br>`highThinkingQA/config.py:305` default=3<br>`highThinkingQA/config.py:414`<br>`highThinkingQA/ingest/vector_store.py:148`<br>`highThinkingQA/ingest/vector_store.py:180`<br>`highThinkingQA/retriever/vector_retriever.py:95`<br>`highThinkingQA/retriever/vector_retriever.py:232`<br>`highThinkingQA/server/services/stage_cache.py:201`<br>+1 more | 3 |
| `SEMANTIC_CHUNK_MAX_TOKENS` | `resource/config/services/highThinkingQA/config.shared.env:25` = `4000` | `highThinkingQA/config.py:288` default=4000<br>`highThinkingQA/config.py:397`<br>`highThinkingQA/ingest/chunker.py:127` | 4000 |
| `SEMANTIC_CHUNK_MIN_TOKENS` | `resource/config/services/highThinkingQA/config.shared.env:24` = `2000` | `highThinkingQA/config.py:287` default=2000<br>`highThinkingQA/config.py:396`<br>`highThinkingQA/ingest/chunker.py:125` | 2000 |
| `SSE_HEARTBEAT_SEC` | `resource/config/services/fastQA/config.shared.env:11` = `15` | `fastQA/app/core/config.py:366` |  |
| `SSE_HEARTBEAT_SECONDS` | `resource/config/services/highThinkingQA/config.shared.env:73` = `15` | `fastQA/app/core/config.py:367` default=15<br>`highThinkingQA/config.py:334` default=15<br>`highThinkingQA/config.py:432`<br>`highThinkingQA/server_fastapi/app.py:76`<br>`highThinkingQA/server_fastapi/routers/ask.py:535` | 15 |
| `SUB_ANSWER_MODEL` | `resource/config/services/highThinkingQA/config.env.example:10` = `qwen3-max`<br>`resource/config/services/highThinkingQA/config.shared.env:13` = `qwen3-max` | `highThinkingQA/agent_core/sub_answerer.py:22`<br>`highThinkingQA/config.py:262` default=os.getenv("LLM_MODEL", "qwen3-max"<br>`highThinkingQA/config.py:385` | os.getenv("LLM_MODEL", "qwen3-max" |
| `THINKING_BACKEND_BASE_URL` | `resource/config/shared/infrastructure.shared.env:19` = `http://127.0.0.1:8009` | `gateway/app/core/config.py:118` default="http://127.0.0.1:8009"<br>`gateway/scripts/run_gunicorn_foreground.sh:24` default=http://127.0.0.1:8009<br>`gateway/scripts/start_gunicorn.sh:32` default=http://127.0.0.1:8009 | "http://127.0.0.1:8009"<br>http://127.0.0.1:8009 |
| `TIKTOKEN_ENCODING` | `resource/config/services/highThinkingQA/config.shared.env:26` = `cl100k_base` | `highThinkingQA/config.py:289` default="cl100k_base"<br>`highThinkingQA/config.py:398`<br>`highThinkingQA/ingest/chunker.py:24`<br>`highThinkingQA/ingest/embedder.py:33` | "cl100k_base" |
| `TOPIC_INDEX_PATH` | `resource/config/services/fastQA/config.shared.env:34` = `/home/cqy/worktrees/highThinking/resource/fastqa/vector_db_topic_index.json` | `fastQA/app/core/config.py:440`<br>`fastQA/app/modules/generation_pipeline/context_loading.py:15` default="" | "" |
| `TRANSLATION_CACHE_DIR` | `resource/config/services/fastQA/config.shared.env:42` = `/home/cqy/worktrees/highThinking/resource/fastqa/translation_cache`<br>`resource/config/services/public-service/config.shared.env:19` = `translation_cache` | `fastQA/app/core/config.py:446`<br>`public-service/backend/app/core/config.py:207` |  |
| `TRANSLATION_CACHE_MAX_ENTRIES` | `resource/config/services/public-service/config.shared.env:21` = `10000` | `public-service/backend/app/modules/documents/translation_cache_impl.py:40` default="10000" | "10000" |
| `TRANSLATION_CACHE_OBJECT_NAME` | `resource/config/services/public-service/config.shared.env:20` = `translation_cache/translations.json` | `public-service/backend/app/modules/documents/translation_cache_impl.py:48` default="translation_cache/translations.json" | "translation_cache/translations.json" |
| `TRANSLATION_CACHE_REMOTE_SYNC_INTERVAL_SECONDS` | `resource/config/services/public-service/config.shared.env:22` = `5` | `public-service/backend/app/modules/documents/translation_cache_impl.py:43` default="5" | "5" |
| `UPLOAD_DIR` | `resource/config/services/highThinkingQA/config.shared.env:53` = `uploads`<br>`resource/config/services/public-service/config.shared.env:14` = `uploads` | `highThinkingQA/config.py:330`<br>`highThinkingQA/config.py:428`<br>`highThinkingQA/server_fastapi/app.py:72`<br>`highThinkingQA/server_fastapi/routers/upload.py:45` default=""<br>`public-service/backend/app/core/config.py:198` | "" |
| `UPLOAD_FILE_PROCESSING_ENABLED` | `resource/config/services/public-service/config.shared.env:52` = `1` | `public-service/backend/app/modules/conversation/upload_processing_worker.py:60` default="1" | "1"<br>最终策略：上传文件后台处理已确认必开，开关写死启用；worker 数、PDF 页数、poll interval、recovery scan limit 继续保留配置。 |
| `UPLOAD_PROCESSING_MAX_PDF_PAGES` | `resource/config/services/public-service/config.shared.env:54` = `20` | `public-service/backend/app/modules/conversation/upload_processing_worker.py:70` |  |
| `UPLOAD_PROCESSING_POLL_INTERVAL_MS` | `resource/config/services/public-service/config.shared.env:55` = `1000` | 未命中直接字符串引用 | 无直接代码默认值 |
| `UPLOAD_PROCESSING_RECOVERY_SCAN_LIMIT` | `resource/config/services/public-service/config.shared.env:56` = `500` | `public-service/backend/app/modules/conversation/service.py:667` default="500" | "500" |
| `UPLOAD_PROCESSING_WORKER_MAX_WORKERS` | `resource/config/services/public-service/config.shared.env:53` = `2` | `public-service/backend/app/modules/conversation/upload_processing_worker.py:64` |  |
| `UPLOAD_QA_FIRST_TOKEN_TIMEOUT_SEC` | `resource/config/services/fastQA/config.shared.env:96` = `60` | `fastQA/app/modules/qa_pdf/service.py:425` default="25"<br>`fastQA/app/modules/qa_pdf/streaming.py:32` default="25" | "25" |
| `UPLOAD_QA_SIDECAR_MODE` | `resource/config/services/fastQA/config.shared.env:98` = `file_only` | `fastQA/app/modules/qa_pdf/service.py:87` |  |
| `UPLOAD_QA_USE_SIDECAR` | `resource/config/services/fastQA/config.shared.env:97` = `1` | `fastQA/app/modules/qa_pdf/service.py:84` | 最终策略：上传 PDF QA sidecar 已确认必开，开关写死启用；`UPLOAD_QA_SIDECAR_MODE`、`PDFQA_SIDECAR_BASE_URL_INTERNAL`、`PDFQA_SIDECAR_SELF_PORT`、`UPLOAD_QA_FIRST_TOKEN_TIMEOUT_SEC` 继续保留配置。 |
| `VECTOR_COLLECTION_NAME` | `resource/config/services/public-service/config.shared.env:18` = `lfp_papers` | `fastQA/app/modules/microscopic_expert.py:131` default="lfp_papers"<br>`public-service/backend/app/modules/retrieval/service.py:40` default="lfp_papers" | "lfp_papers" |
| `VECTOR_DB_COMMUNITY_PATH` | `resource/config/services/fastQA/config.shared.env:31` = `/home/cqy/worktrees/highThinking/resource/fastqa/community_vector_database` | `fastQA/app/core/config.py:438` |  |
| `VECTOR_DB_MD_COLLECTION` | `resource/config/services/fastQA/config.shared.env:33` = `md_papers` | `fastQA/app/modules/generation_pipeline/md_expansion.py:29` default="md_papers" | "md_papers" |
| `VECTOR_DB_MD_PATH` | `resource/config/services/fastQA/config.shared.env:32` = `/home/cqy/worktrees/highThinking/resource/fastqa/vector_database_md` | `fastQA/app/core/config.py:439`<br>`fastQA/app/modules/generation_pipeline/md_expansion.py:28` default="vector_database_md" | "vector_database_md" |
| `VECTOR_DB_PATH` | `resource/config/services/fastQA/config.shared.env:28` = `/home/cqy/worktrees/highThinking/resource/fastqa/vector_database`<br>`resource/config/services/public-service/config.shared.env:17` = `vector_database` | `fastQA/app/core/config.py:435`<br>`fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:94` default=default="vector_database"<br>`fastQA/app/modules/microscopic_expert.py:114` default="vector_database"<br>`public-service/backend/app/core/config.py:205`<br>`public-service/backend/app/modules/retrieval/service.py:36` default="vector_database" | default="vector_database"<br>"vector_database" |
| `VECTOR_DB_PDF_PATH` | `resource/config/services/fastQA/config.shared.env:30` = `/home/cqy/worktrees/highThinking/resource/fastqa/vector_database_pdf` | `fastQA/app/core/config.py:437` |  |
| `VECTOR_DB_SUMMARY_PATH` | `resource/config/services/fastQA/config.shared.env:29` = `/home/cqy/worktrees/highThinking/resource/fastqa/vector_database` | `fastQA/app/core/config.py:436` |  |

## 多位置引用明细

只展开直接引用超过 8 处的变量，便于后续改代码时追踪所有 call site。

### `APP_ENV`
- `fastQA/app/core/config.py:284` default="development": `app_env = str(os.getenv("APP_ENV", "development") or "development").strip()`
- `highThinkingQA/config.py:326` default="dev": `app_env=str(os.getenv("APP_ENV", "dev") or "dev").strip(),`
- `highThinkingQA/config.py:347` default="dev": `app_env = str(os.getenv("APP_ENV", "dev") or "dev").strip()`
- `highThinkingQA/config.py:424`: `APP_ENV = HTTP_SETTINGS.app_env`
- `highThinkingQA/server_fastapi/app.py:69`: `"APP_ENV": settings.app_env,`
- `public-service/backend/app/core/config.py:195` default="development": `app_env = str(os.getenv("APP_ENV", "development") or "development").strip()`
- `public-service/backend/app/modules/conversation/internal_api.py:74` default="": `if str(os.getenv("APP_ENV", "") or "").strip().lower() == "test":`
- `public-service/backend/app/modules/conversation/upload_processing_worker.py:432` default="development": `return str(os.getenv("APP_ENV", "development") or "development").strip().lower() == "test"`
- `public-service/backend/app/modules/quota/deps.py:67` default="development": `return str(os.getenv("APP_ENV", "development") or "development").strip().lower() == "test"`

### `APP_PORT`
- `fastQA/scripts/start_gunicorn.sh:33` default=8008: `export APP_PORT="${APP_PORT:-8008}"`
- `fastQA/scripts/start_gunicorn.sh:34`: `export FASTAPI_PORT="${FASTAPI_PORT:-$APP_PORT}"`
- `fastQA/scripts/start_gunicorn.sh:40`: `if [[ -z "${ENV_FILE_LOADER_PROCESS_KEYS[APP_PORT]+x}" ]]; then`
- `fastQA/scripts/start_gunicorn.sh:41` default=8008: `export APP_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${APP_PORT:-8008}}}"`
- `fastQA/scripts/start_gunicorn.sh:44`: `export FASTAPI_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT}}}"`
- `fastQA/scripts/status_gunicorn.sh:14` default=8008: `export APP_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${APP_PORT:-8008}}}"`
- `fastQA/scripts/status_gunicorn.sh:15`: `export FASTAPI_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT}}}"`
- `fastQA/scripts/stop_gunicorn.sh:11` default=8008: `export APP_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${APP_PORT:-8008}}}"`
- `fastQA/scripts/stop_gunicorn.sh:12`: `export FASTAPI_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT}}}"`
- `highThinkingQA/config.py:319` default="8008": `raw_port = str(os.getenv("HIGHTHINKINGQA_PORT") or os.getenv("APP_PORT") or "8008").strip()`
- `highThinkingQA/config.py:426`: `APP_PORT = HTTP_SETTINGS.app_port`
- `highThinkingQA/scripts/start_fastapi_gunicorn.sh:29` default=8009: `export APP_PORT="${APP_PORT:-8009}"`
- `highThinkingQA/scripts/start_fastapi_gunicorn.sh:38`: `if [[ -z "${ENV_FILE_LOADER_PROCESS_KEYS[APP_PORT]+x}" ]]; then`
- `highThinkingQA/scripts/start_fastapi_gunicorn.sh:39` default=8009: `export APP_PORT="${HIGHTHINKINGQA_PORT:-${APP_PORT:-8009}}"`
- `highThinkingQA/scripts/start_fastapi_gunicorn.sh:81`: `echo "gunicorn started: pid=$PID port=${APP_PORT}"`
- `highThinkingQA/scripts/status_fastapi_gunicorn.sh:27` default=8009: `PORT="${HIGHTHINKINGQA_PORT:-${APP_PORT:-8009}}"`
- `highThinkingQA/scripts/stop_fastapi_gunicorn.sh:13` default=8009: `PORT="${HIGHTHINKINGQA_PORT:-${APP_PORT:-8009}}"`
- `highThinkingQA/server_fastapi/app.py:71`: `"APP_PORT": settings.app_port,`

### `DASHSCOPE_API_KEY`
- `fastQA/app/core/runtime.py:466`: `raise ValueError("OPENAI_API_KEY/DASHSCOPE_API_KEY is required")`
- `fastQA/app/core/runtime.py:583` default="": `or str(os.getenv("DASHSCOPE_API_KEY", "") or "").strip()`
- `fastQA/app/modules/generation_pipeline/query_expander.py:33`: `self.api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")`
- `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:78`: `resolved_api_key = api_key or _env_first("LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY")`
- `fastQA/app/modules/microscopic_expert.py:162` default="": `api_key = raw_api_key or str(os.getenv("DASHSCOPE_API_KEY", "") or "").strip()`
- `fastQA/app/modules/qa_pdf/llm_factory.py:53`: `dashscope_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")`
- `fastQA/app/modules/qa_pdf/llm_factory.py:150`: `raise ValueError("请设置LLM_API_KEY、OPENAI_API_KEY或DASHSCOPE_API_KEY环境变量")`
- `fastQA/app/services/file_route_service.py:62`: `raise RuntimeError("OPENAI_API_KEY/DASHSCOPE_API_KEY is required for file QA")`
- `highThinkingQA/agent_core/llm_client.py:29`: `api_key = _require_api_key(api_key=config.LLM_API_KEY, env_name="DASHSCOPE_API_KEY")`
- `highThinkingQA/agent_core/llm_client.py:41`: `api_key = _require_api_key(api_key=config.LLM_API_KEY, env_name="DASHSCOPE_API_KEY")`
- `highThinkingQA/config.py:243` default="": `dashscope_api_key = str(os.getenv("DASHSCOPE_API_KEY", "") or "").strip()`
- `highThinkingQA/config.py:378`: `DASHSCOPE_API_KEY = SETTINGS.dashscope_api_key`
- `highThinkingQA/ingest/embedder.py:48`: `api_key = _require_api_key(api_key=config.EMBEDDING_API_KEY, env_name="DASHSCOPE_API_KEY")`
- `highThinkingQA/server/services/documents_service.py:36` default="": `or str(os.getenv("DASHSCOPE_API_KEY", "")).strip()`
- `patent/server/patent/answering.py:942`: `or os.getenv("DASHSCOPE_API_KEY")`
- `patent/server/patent/hybrid_synthesis.py:266`: `"DASHSCOPE_API_KEY",`
- `patent/server/patent/pdf_service.py:874`: `"DASHSCOPE_API_KEY",`
- `patent/server/patent/rerank_service.py:193` default="": `api_key = raw_api_key or str(os.getenv("DASHSCOPE_API_KEY", "") or "").strip()`
- `patent/server/patent/runtime.py:258` default="": `shared_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""`
- `patent/server/patent/tabular_service.py:530`: `"DASHSCOPE_API_KEY",`
- `public-service/backend/app/modules/documents/service.py:119`: `self._openai_api_key = _first_env("LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY")`
- `public-service/backend/app/modules/documents/translator.py:32`: `self.api_key = api_key or _first_env("LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY")`

### `DASHSCOPE_BASE_URL`
- `fastQA/app/core/runtime.py:468`: `raise ValueError("OPENAI_BASE_URL/DASHSCOPE_BASE_URL is required")`
- `fastQA/app/modules/generation_pipeline/query_expander.py:38`: `or os.getenv("DASHSCOPE_BASE_URL")`
- `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:82`: `"DASHSCOPE_BASE_URL",`
- `fastQA/app/modules/qa_pdf/llm_factory.py:57`: `or os.getenv("DASHSCOPE_BASE_URL")`
- `fastQA/app/services/file_route_service.py:64`: `raise RuntimeError("OPENAI_BASE_URL/DASHSCOPE_BASE_URL is required for file QA")`
- `highThinkingQA/config.py:254`: `or os.getenv("DASHSCOPE_BASE_URL")`
- `highThinkingQA/config.py:269` default="https://dashscope.aliyuncs.com/compatible-mode/v1": `or os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),`
- `highThinkingQA/config.py:280` default="https://dashscope.aliyuncs.com/compatible-mode/v1": `or os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),`
- `patent/server/patent/answering.py:949`: `or os.getenv("DASHSCOPE_BASE_URL")`
- `patent/server/patent/hybrid_synthesis.py:272`: `"DASHSCOPE_BASE_URL",`
- `patent/server/patent/pdf_service.py:880`: `"DASHSCOPE_BASE_URL",`
- `patent/server/patent/runtime.py:259` default="": `shared_base_url = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL") or ""`
- `patent/server/patent/tabular_service.py:536`: `"DASHSCOPE_BASE_URL",`
- `public-service/backend/app/modules/documents/service.py:123`: `"DASHSCOPE_BASE_URL",`
- `public-service/backend/app/modules/documents/translator.py:36`: `"DASHSCOPE_BASE_URL",`

### `DASHSCOPE_MODEL`
- `fastQA/app/modules/generation_pipeline/query_expander.py:41` default="qwen-plus": `self.model = model or os.getenv("QUERY_EXPANSION_MODEL") or os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("DASHSCOPE_MODEL") or "qwen-plus"`
- `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:85`: `resolved_model = model or _env_first("LLM_MODEL", "OPENAI_MODEL", "DASHSCOPE_MODEL", default="qwen-plus")`
- `fastQA/app/modules/qa_cache/stage1_cache.py:97` default="unknown": `raw = str(getattr(runtime, "model", "") or os.getenv("DASHSCOPE_MODEL", "unknown")).strip()`
- `fastQA/app/modules/qa_cache/stage25_cache.py:37` default="unknown": `raw = str(getattr(runtime, "model", "") or os.getenv("DASHSCOPE_MODEL", "unknown")).strip()`
- `fastQA/app/modules/qa_cache/stage2_cache.py:78` default="unknown": `raw = str(getattr(runtime, "model", "") or os.getenv("DASHSCOPE_MODEL", "unknown")).strip()`
- `fastQA/app/modules/qa_pdf/llm_factory.py:62` default="deepseek-v3.1": `os.getenv("LLM_MODEL", os.getenv("OPENAI_MODEL", os.getenv("DASHSCOPE_MODEL", "deepseek-v3.1"))),`
- `patent/server/patent/answering.py:956`: `or os.getenv("DASHSCOPE_MODEL")`
- `patent/server/patent/hybrid_synthesis.py:278`: `"DASHSCOPE_MODEL",`
- `patent/server/patent/pdf_service.py:886`: `"DASHSCOPE_MODEL",`
- `patent/server/patent/runtime.py:260` default="": `shared_model = os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("DASHSCOPE_MODEL") or ""`
- `patent/server/patent/tabular_service.py:542`: `"DASHSCOPE_MODEL",`
- `public-service/backend/app/modules/documents/service.py:126`: `self._openai_model = _first_env("LLM_MODEL", "OPENAI_MODEL", "DASHSCOPE_MODEL", default="deepseek-v3.1")`
- `public-service/backend/app/modules/documents/translator.py:39`: `self.model = model or _first_env("LLM_MODEL", "OPENAI_MODEL", "DASHSCOPE_MODEL", default="deepseek-v3.1")`

### `FASTQA_FASTAPI_PORT`
- `fastQA/app/core/config.py:286`: `os.getenv("FASTQA_FASTAPI_PORT")`
- `fastQA/scripts/start_gunicorn.sh:41` default=${FASTQA_PORT:-${APP_PORT:-8008: `export APP_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${APP_PORT:-8008}}}"`
- `fastQA/scripts/start_gunicorn.sh:44` default=${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT: `export FASTAPI_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT}}}"`
- `fastQA/scripts/start_gunicorn.sh:47` default=${FASTQA_PORT:-${BACKEND_PORT:-$FASTAPI_PORT: `export BACKEND_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${BACKEND_PORT:-$FASTAPI_PORT}}}"`
- `fastQA/scripts/status_gunicorn.sh:14` default=${FASTQA_PORT:-${APP_PORT:-8008: `export APP_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${APP_PORT:-8008}}}"`
- `fastQA/scripts/status_gunicorn.sh:15` default=${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT: `export FASTAPI_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT}}}"`
- `fastQA/scripts/stop_gunicorn.sh:11` default=${FASTQA_PORT:-${APP_PORT:-8008: `export APP_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${APP_PORT:-8008}}}"`
- `fastQA/scripts/stop_gunicorn.sh:12` default=${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT: `export FASTAPI_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT}}}"`
- `scripts/_service_common.sh:35`: `GATEWAY_PORT\|PUBLIC_SERVICE_PORT\|FASTQA_PORT\|FASTQA_FASTAPI_PORT\|HIGHTHINKINGQA_PORT\|PATENT_PORT) ;;`
- `scripts/_service_common.sh:75` default=${FASTQA_PORT:-8008: `fastQA) echo "${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-8008}}" ;;`

### `FASTQA_PORT`
- `fastQA/app/core/config.py:287`: `or os.getenv("FASTQA_PORT")`
- `fastQA/scripts/start_gunicorn.sh:41` default=${APP_PORT:-8008: `export APP_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${APP_PORT:-8008}}}"`
- `fastQA/scripts/start_gunicorn.sh:44` default=${FASTAPI_PORT:-$APP_PORT: `export FASTAPI_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT}}}"`
- `fastQA/scripts/start_gunicorn.sh:47` default=${BACKEND_PORT:-$FASTAPI_PORT: `export BACKEND_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${BACKEND_PORT:-$FASTAPI_PORT}}}"`
- `fastQA/scripts/status_gunicorn.sh:14` default=${APP_PORT:-8008: `export APP_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${APP_PORT:-8008}}}"`
- `fastQA/scripts/status_gunicorn.sh:15` default=${FASTAPI_PORT:-$APP_PORT: `export FASTAPI_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT}}}"`
- `fastQA/scripts/stop_gunicorn.sh:11` default=${APP_PORT:-8008: `export APP_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${APP_PORT:-8008}}}"`
- `fastQA/scripts/stop_gunicorn.sh:12` default=${FASTAPI_PORT:-$APP_PORT: `export FASTAPI_PORT="${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-${FASTAPI_PORT:-$APP_PORT}}}"`
- `scripts/_service_common.sh:35`: `GATEWAY_PORT\|PUBLIC_SERVICE_PORT\|FASTQA_PORT\|FASTQA_FASTAPI_PORT\|HIGHTHINKINGQA_PORT\|PATENT_PORT) ;;`
- `scripts/_service_common.sh:75` default=8008: `fastQA) echo "${FASTQA_FASTAPI_PORT:-${FASTQA_PORT:-8008}}" ;;`

### `FASTQA_SERVICE_RUNTIME_ROOT`
- `fastQA/scripts/start_gunicorn.sh:28` default=$RUNTIME_DIR_DEFAULT: `export FASTQA_SERVICE_RUNTIME_ROOT="${FASTQA_SERVICE_RUNTIME_ROOT:-$RUNTIME_DIR_DEFAULT}"`
- `fastQA/scripts/start_gunicorn.sh:50`: `PID_FILE="$FASTQA_SERVICE_RUNTIME_ROOT/fastqa-gunicorn.pid"`
- `fastQA/scripts/start_gunicorn.sh:55`: `mkdir -p "$FASTQA_SERVICE_RUNTIME_ROOT" "$FASTQA_SERVICE_LOG_ROOT"`
- `fastQA/scripts/status_gunicorn.sh:12` default=$RUNTIME_DIR_DEFAULT: `export FASTQA_SERVICE_RUNTIME_ROOT="${FASTQA_SERVICE_RUNTIME_ROOT:-$RUNTIME_DIR_DEFAULT}"`
- `fastQA/scripts/status_gunicorn.sh:16`: `PID_FILE="$FASTQA_SERVICE_RUNTIME_ROOT/fastqa-gunicorn.pid"`
- `fastQA/scripts/stop_gunicorn.sh:10` default=$RUNTIME_DIR_DEFAULT: `export FASTQA_SERVICE_RUNTIME_ROOT="${FASTQA_SERVICE_RUNTIME_ROOT:-$RUNTIME_DIR_DEFAULT}"`
- `fastQA/scripts/stop_gunicorn.sh:13`: `PID_FILE="$FASTQA_SERVICE_RUNTIME_ROOT/fastqa-gunicorn.pid"`
- `scripts/_service_common.sh:131` default="$RESOURCE_DIR/runtime/dev/fastQA": `FASTQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/fastQA" \\`
- `scripts/_service_common.sh:136` default="$RESOURCE_DIR/runtime/dev/fastQA": `FASTQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/fastQA" \\`
- `scripts/_service_common.sh:140` default="$RESOURCE_DIR/runtime/dev/fastQA": `FASTQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/fastQA" \\`

### `GATEWAY_PORT`
- `gateway/app/core/config.py:134` default="8101": `port=int(str(os.getenv("GATEWAY_PORT", "8101") or "8101")),`
- `gateway/scripts/run_gunicorn_foreground.sh:17` default=8101: `export GATEWAY_PORT="${GATEWAY_PORT:-8101}"`
- `gateway/scripts/run_gunicorn_foreground.sh:35`: `--bind "0.0.0.0:${GATEWAY_PORT}" \\`
- `gateway/scripts/start_gunicorn.sh:25` default=8101: `export GATEWAY_PORT="${GATEWAY_PORT:-8101}"`
- `gateway/scripts/start_gunicorn.sh:64`: `--bind "0.0.0.0:${GATEWAY_PORT}" \\`
- `gateway/scripts/start_gunicorn.sh:78`: `echo "gateway gunicorn started: pid=$PID port=${GATEWAY_PORT}"`
- `gateway/scripts/status_gunicorn.sh:7` default=8101: `PORT="${GATEWAY_PORT:-8101}"`
- `gateway/scripts/stop_gunicorn.sh:6` default=8101: `PORT="${GATEWAY_PORT:-8101}"`
- `scripts/_service_common.sh:35`: `GATEWAY_PORT\|PUBLIC_SERVICE_PORT\|FASTQA_PORT\|FASTQA_FASTAPI_PORT\|HIGHTHINKINGQA_PORT\|PATENT_PORT) ;;`
- `scripts/_service_common.sh:73` default=8101: `gateway) echo "${GATEWAY_PORT:-8101}" ;;`

### `HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT`
- `highThinkingQA/scripts/start_fastapi_gunicorn.sh:26` default=$SERVICE_RUNTIME_ROOT_DEFAULT: `export HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT="${HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT:-$SERVICE_RUNTIME_ROOT_DEFAULT}"`
- `highThinkingQA/scripts/start_fastapi_gunicorn.sh:42`: `PID_FILE="$HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT/gunicorn.pid"`
- `highThinkingQA/scripts/start_fastapi_gunicorn.sh:49`: `mkdir -p "$HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT" "$LOG_DIR"`
- `highThinkingQA/scripts/status_fastapi_gunicorn.sh:24` default=$SERVICE_RUNTIME_ROOT_DEFAULT: `export HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT="${HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT:-$SERVICE_RUNTIME_ROOT_DEFAULT}"`
- `highThinkingQA/scripts/status_fastapi_gunicorn.sh:28`: `PID_FILE="$HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT/gunicorn.pid"`
- `highThinkingQA/scripts/stop_fastapi_gunicorn.sh:12` default=$SERVICE_RUNTIME_ROOT_DEFAULT: `export HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT="${HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT:-$SERVICE_RUNTIME_ROOT_DEFAULT}"`
- `highThinkingQA/scripts/stop_fastapi_gunicorn.sh:14`: `PID_FILE="$HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT/gunicorn.pid"`
- `scripts/_service_common.sh:146` default="$RESOURCE_DIR/runtime/dev/highThinkingQA": `HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/highThinkingQA" \\`
- `scripts/_service_common.sh:151` default="$RESOURCE_DIR/runtime/dev/highThinkingQA": `HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/highThinkingQA" \\`
- `scripts/_service_common.sh:157` default="$RESOURCE_DIR/runtime/dev/highThinkingQA": `HIGHTHINKINGQA_SERVICE_RUNTIME_ROOT="$RESOURCE_DIR/runtime/dev/highThinkingQA" \\`

### `LLM_API_KEY`
- `fastQA/app/modules/generation_pipeline/query_expander.py:33`: `self.api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")`
- `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:78` default="OPENAI_API_KEY": `resolved_api_key = api_key or _env_first("LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY")`
- `fastQA/app/modules/qa_pdf/llm_factory.py:53`: `dashscope_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")`
- `fastQA/app/modules/qa_pdf/llm_factory.py:150`: `raise ValueError("请设置LLM_API_KEY、OPENAI_API_KEY或DASHSCOPE_API_KEY环境变量")`
- `highThinkingQA/agent_core/llm_client.py:29`: `api_key = _require_api_key(api_key=config.LLM_API_KEY, env_name="DASHSCOPE_API_KEY")`
- `highThinkingQA/agent_core/llm_client.py:41`: `api_key = _require_api_key(api_key=config.LLM_API_KEY, env_name="DASHSCOPE_API_KEY")`
- `highThinkingQA/config.py:245` default="": `llm_api_key = str(os.getenv("LLM_API_KEY") or openai_api_key or dashscope_api_key or "").strip()`
- `highThinkingQA/config.py:381`: `LLM_API_KEY = SETTINGS.llm_api_key`
- `highThinkingQA/server/services/documents_service.py:37`: `or str(getattr(config, "LLM_API_KEY", "")).strip()`
- `patent/server/patent/answering.py:940`: `or os.getenv("LLM_API_KEY")`
- `patent/server/patent/hybrid_synthesis.py:264`: `"LLM_API_KEY",`
- `patent/server/patent/pdf_service.py:872`: `"LLM_API_KEY",`
- `patent/server/patent/runtime.py:258` default="": `shared_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""`
- `patent/server/patent/tabular_service.py:528`: `"LLM_API_KEY",`
- `public-service/backend/app/modules/documents/service.py:119`: `self._openai_api_key = _first_env("LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY")`
- `public-service/backend/app/modules/documents/translator.py:32`: `self.api_key = api_key or _first_env("LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY")`

### `LLM_BASE_URL`
- `fastQA/app/modules/generation_pipeline/query_expander.py:36`: `or os.getenv("LLM_BASE_URL")`
- `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:80`: `"LLM_BASE_URL",`
- `fastQA/app/modules/qa_pdf/llm_factory.py:55`: `os.getenv("LLM_BASE_URL")`
- `highThinkingQA/agent_core/llm_client.py:32`: `"base_url": config.LLM_BASE_URL,`
- `highThinkingQA/agent_core/llm_client.py:44`: `"base_url": config.LLM_BASE_URL,`
- `highThinkingQA/config.py:252`: `os.getenv("LLM_BASE_URL")`
- `highThinkingQA/config.py:379`: `LLM_BASE_URL = SETTINGS.llm_base_url`
- `highThinkingQA/server/services/documents_service.py:42`: `value = str(os.getenv("OPENAI_BASE_URL", "")).strip() or str(getattr(config, "LLM_BASE_URL", "")).strip()`
- `patent/server/patent/answering.py:947`: `or os.getenv("LLM_BASE_URL")`
- `patent/server/patent/hybrid_synthesis.py:270`: `"LLM_BASE_URL",`
- `patent/server/patent/pdf_service.py:878`: `"LLM_BASE_URL",`
- `patent/server/patent/runtime.py:259` default="": `shared_base_url = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL") or ""`
- `patent/server/patent/tabular_service.py:534`: `"LLM_BASE_URL",`
- `public-service/backend/app/modules/documents/service.py:121`: `"LLM_BASE_URL",`
- `public-service/backend/app/modules/documents/translator.py:34`: `"LLM_BASE_URL",`

### `LLM_MODEL`
- `fastQA/app/modules/generation_pipeline/query_expander.py:41` default="qwen-plus": `self.model = model or os.getenv("QUERY_EXPANSION_MODEL") or os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("DASHSCOPE_MODEL") or "qwen-plus"`
- `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:85` default="OPENAI_MODEL": `resolved_model = model or _env_first("LLM_MODEL", "OPENAI_MODEL", "DASHSCOPE_MODEL", default="qwen-plus")`
- `fastQA/app/modules/qa_pdf/llm_factory.py:62` default=os.getenv("OPENAI_MODEL", os.getenv("DASHSCOPE_MODEL", "deepseek-v3.1": `os.getenv("LLM_MODEL", os.getenv("OPENAI_MODEL", os.getenv("DASHSCOPE_MODEL", "deepseek-v3.1"))),`
- `highThinkingQA/agent_core/llm_client.py:70`: `"model": model or config.LLM_MODEL,`
- `highThinkingQA/agent_core/llm_client.py:118`: `model: 指定模型名称，None 则使用全局配置 LLM_MODEL`
- `highThinkingQA/agent_core/llm_client.py:167`: `model: 指定模型名称，None 则使用全局配置 LLM_MODEL`
- `highThinkingQA/config.py:257` default="qwen3-max": `llm_model=str(os.getenv("LLM_MODEL", "qwen3-max") or "qwen3-max").strip(),`
- `highThinkingQA/config.py:260` default="qwen3-max": `decompose_model=str(os.getenv("DECOMPOSE_MODEL", os.getenv("LLM_MODEL", "qwen3-max")) or os.getenv("LLM_MODEL", "qwen3-max")).strip(),`
- `highThinkingQA/config.py:261` default="qwen3-max": `direct_answer_model=str(os.getenv("DIRECT_ANSWER_MODEL", os.getenv("LLM_MODEL", "qwen3-max")) or os.getenv("LLM_MODEL", "qwen3-max")).strip(),`
- `highThinkingQA/config.py:262` default="qwen3-max": `sub_answer_model=str(os.getenv("SUB_ANSWER_MODEL", os.getenv("LLM_MODEL", "qwen3-max")) or os.getenv("LLM_MODEL", "qwen3-max")).strip(),`
- `highThinkingQA/config.py:380`: `LLM_MODEL = SETTINGS.llm_model`
- `highThinkingQA/server/services/documents_service.py:50`: `or str(getattr(config, "LLM_MODEL", "")).strip()`
- `patent/server/patent/answering.py:954`: `or os.getenv("LLM_MODEL")`
- `patent/server/patent/hybrid_synthesis.py:276`: `"LLM_MODEL",`
- `patent/server/patent/pdf_service.py:884`: `"LLM_MODEL",`
- `patent/server/patent/runtime.py:260` default="": `shared_model = os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("DASHSCOPE_MODEL") or ""`
- `patent/server/patent/tabular_service.py:540`: `"LLM_MODEL",`
- `public-service/backend/app/modules/documents/service.py:126`: `self._openai_model = _first_env("LLM_MODEL", "OPENAI_MODEL", "DASHSCOPE_MODEL", default="deepseek-v3.1")`
- `public-service/backend/app/modules/documents/translator.py:39`: `self.model = model or _first_env("LLM_MODEL", "OPENAI_MODEL", "DASHSCOPE_MODEL", default="deepseek-v3.1")`

### `MINIO_ACCESS_KEY`
- `fastQA/app/core/config.py:321` default="": `minio_access_key=str(os.getenv("MINIO_ACCESS_KEY", "") or "").strip(),`
- `fastQA/app/modules/storage/paper_storage.py:108` default="": `access_key = os.getenv("MINIO_ACCESS_KEY", "").strip()`
- `fastQA/app/modules/storage/upload_materializer.py:52` default="": `access_key = os.getenv("MINIO_ACCESS_KEY", "").strip()`
- `highThinkingQA/server/storage/minio_backend.py:27` default="": `access_key = str(os.getenv("MINIO_ACCESS_KEY", "")).strip()`
- `highThinkingQA/server/storage/minio_backend.py:34`: `raise RuntimeError("MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY are required")`
- `highThinkingQA/server/storage/paper_storage.py:68` default="": `access_key = os.getenv("MINIO_ACCESS_KEY", "").strip()`
- `public-service/backend/app/core/config.py:243` default="": `minio_access_key=(str(os.getenv("MINIO_ACCESS_KEY", "") or "").strip() or None),`
- `public-service/backend/app/integrations/storage/minio.py:27`: `raise RuntimeError("MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY are required")`
- `public-service/backend/app/modules/documents/translation_cache_impl.py:58` default="": `access_key = os.getenv("MINIO_ACCESS_KEY", "").strip()`
- `scripts/patent_originals_backfill.py:32` default="": `access_key = str(os.getenv("MINIO_ACCESS_KEY") or "").strip()`
- `scripts/patent_originals_backfill.py:37`: `raise RuntimeError("MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY are required")`
- `scripts/patent_originals_parity_check.py:31` default="": `access_key = str(os.getenv("MINIO_ACCESS_KEY") or "").strip()`
- `scripts/patent_originals_parity_check.py:36`: `raise RuntimeError("MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY are required")`

### `MINIO_ENDPOINT`
- `fastQA/app/core/config.py:320` default="": `minio_endpoint=str(os.getenv("MINIO_ENDPOINT", "") or "").strip(),`
- `fastQA/app/modules/storage/paper_storage.py:107` default="": `endpoint = os.getenv("MINIO_ENDPOINT", "").strip()`
- `fastQA/app/modules/storage/upload_materializer.py:51` default="": `endpoint = os.getenv("MINIO_ENDPOINT", "").strip()`
- `highThinkingQA/server/storage/minio_backend.py:26` default="": `endpoint = str(os.getenv("MINIO_ENDPOINT", "")).strip()`
- `highThinkingQA/server/storage/minio_backend.py:34`: `raise RuntimeError("MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY are required")`
- `highThinkingQA/server/storage/paper_storage.py:67` default="": `endpoint = os.getenv("MINIO_ENDPOINT", "").strip()`
- `public-service/backend/app/core/config.py:242` default="": `minio_endpoint=(str(os.getenv("MINIO_ENDPOINT", "") or "").strip() or None),`
- `public-service/backend/app/integrations/storage/minio.py:27`: `raise RuntimeError("MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY are required")`
- `public-service/backend/app/modules/documents/translation_cache_impl.py:57` default="": `endpoint = os.getenv("MINIO_ENDPOINT", "").strip()`
- `scripts/patent_originals_backfill.py:31` default="": `endpoint = str(os.getenv("MINIO_ENDPOINT") or "").strip()`
- `scripts/patent_originals_backfill.py:37`: `raise RuntimeError("MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY are required")`
- `scripts/patent_originals_parity_check.py:30` default="": `endpoint = str(os.getenv("MINIO_ENDPOINT") or "").strip()`
- `scripts/patent_originals_parity_check.py:36`: `raise RuntimeError("MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY are required")`

### `MINIO_SECRET_KEY`
- `fastQA/app/core/config.py:322` default="": `minio_secret_key=str(os.getenv("MINIO_SECRET_KEY", "") or "").strip(),`
- `fastQA/app/modules/storage/paper_storage.py:109` default="": `secret_key = os.getenv("MINIO_SECRET_KEY", "").strip()`
- `fastQA/app/modules/storage/upload_materializer.py:53` default="": `secret_key = os.getenv("MINIO_SECRET_KEY", "").strip()`
- `highThinkingQA/server/storage/minio_backend.py:28` default="": `secret_key = str(os.getenv("MINIO_SECRET_KEY", "")).strip()`
- `highThinkingQA/server/storage/minio_backend.py:34`: `raise RuntimeError("MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY are required")`
- `highThinkingQA/server/storage/paper_storage.py:69` default="": `secret_key = os.getenv("MINIO_SECRET_KEY", "").strip()`
- `public-service/backend/app/core/config.py:244` default="": `minio_secret_key=(str(os.getenv("MINIO_SECRET_KEY", "") or "").strip() or None),`
- `public-service/backend/app/integrations/storage/minio.py:27`: `raise RuntimeError("MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY are required")`
- `public-service/backend/app/modules/documents/translation_cache_impl.py:59` default="": `secret_key = os.getenv("MINIO_SECRET_KEY", "").strip()`
- `scripts/patent_originals_backfill.py:33` default="": `secret_key = str(os.getenv("MINIO_SECRET_KEY") or "").strip()`
- `scripts/patent_originals_backfill.py:37`: `raise RuntimeError("MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY are required")`
- `scripts/patent_originals_parity_check.py:32` default="": `secret_key = str(os.getenv("MINIO_SECRET_KEY") or "").strip()`
- `scripts/patent_originals_parity_check.py:36`: `raise RuntimeError("MINIO_ENDPOINT/MINIO_ACCESS_KEY/MINIO_SECRET_KEY are required")`

### `MINIO_SECURE`
- `fastQA/app/core/config.py:324` default=False: `minio_secure=_get_bool("MINIO_SECURE", False),`
- `fastQA/app/modules/storage/paper_storage.py:111` default="0": `secure = os.getenv("MINIO_SECURE", "0").strip() == "1"`
- `fastQA/app/modules/storage/upload_materializer.py:54` default="0": `secure = os.getenv("MINIO_SECURE", "0").strip() == "1"`
- `highThinkingQA/server/storage/minio_backend.py:30` default="0": `secure = str(os.getenv("MINIO_SECURE", "0")).strip() == "1"`
- `highThinkingQA/server/storage/paper_storage.py:71` default="0": `secure = os.getenv("MINIO_SECURE", "0").strip() == "1"`
- `public-service/backend/app/core/config.py:246` default=False: `minio_secure=_get_bool("MINIO_SECURE", False),`
- `public-service/backend/app/modules/documents/translation_cache_impl.py:61` default="0": `secure = os.getenv("MINIO_SECURE", "0").strip() == "1"`
- `scripts/patent_originals_backfill.py:35` default="0": `secure = str(os.getenv("MINIO_SECURE") or "0").strip().lower() in {"1", "true", "yes"}`
- `scripts/patent_originals_parity_check.py:34` default="0": `secure = str(os.getenv("MINIO_SECURE") or "0").strip().lower() in {"1", "true", "yes"}`

### `OPENAI_API_KEY`
- `fastQA/app/core/runtime.py:466`: `raise ValueError("OPENAI_API_KEY/DASHSCOPE_API_KEY is required")`
- `fastQA/app/modules/generation_pipeline/query_expander.py:33`: `self.api_key = api_key or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")`
- `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:78`: `resolved_api_key = api_key or _env_first("LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY")`
- `fastQA/app/modules/qa_pdf/llm_factory.py:53`: `dashscope_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")`
- `fastQA/app/modules/qa_pdf/llm_factory.py:150`: `raise ValueError("请设置LLM_API_KEY、OPENAI_API_KEY或DASHSCOPE_API_KEY环境变量")`
- `fastQA/app/services/file_route_service.py:62`: `raise RuntimeError("OPENAI_API_KEY/DASHSCOPE_API_KEY is required for file QA")`
- `highThinkingQA/config.py:244` default="": `openai_api_key = str(os.getenv("OPENAI_API_KEY", "") or "").strip()`
- `highThinkingQA/server/services/documents_service.py:35` default="": `str(os.getenv("OPENAI_API_KEY", "")).strip()`
- `patent/server/patent/answering.py:941`: `or os.getenv("OPENAI_API_KEY")`
- `patent/server/patent/hybrid_synthesis.py:265`: `"OPENAI_API_KEY",`
- `patent/server/patent/pdf_service.py:873`: `"OPENAI_API_KEY",`
- `patent/server/patent/runtime.py:258` default="": `shared_api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or ""`
- `patent/server/patent/tabular_service.py:529`: `"OPENAI_API_KEY",`
- `public-service/backend/app/modules/documents/service.py:119`: `self._openai_api_key = _first_env("LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY")`
- `public-service/backend/app/modules/documents/translator.py:32`: `self.api_key = api_key or _first_env("LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY")`

### `OPENAI_BASE_URL`
- `fastQA/app/core/runtime.py:468`: `raise ValueError("OPENAI_BASE_URL/DASHSCOPE_BASE_URL is required")`
- `fastQA/app/modules/generation_pipeline/query_expander.py:37`: `or os.getenv("OPENAI_BASE_URL")`
- `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:81`: `"OPENAI_BASE_URL",`
- `fastQA/app/modules/qa_pdf/llm_factory.py:56`: `or os.getenv("OPENAI_BASE_URL")`
- `fastQA/app/services/file_route_service.py:64`: `raise RuntimeError("OPENAI_BASE_URL/DASHSCOPE_BASE_URL is required for file QA")`
- `highThinkingQA/config.py:253`: `or os.getenv("OPENAI_BASE_URL")`
- `highThinkingQA/config.py:268`: `os.getenv("OPENAI_BASE_URL")`
- `highThinkingQA/config.py:279`: `os.getenv("OPENAI_BASE_URL")`
- `highThinkingQA/server/services/documents_service.py:42` default="": `value = str(os.getenv("OPENAI_BASE_URL", "")).strip() or str(getattr(config, "LLM_BASE_URL", "")).strip()`
- `patent/server/patent/answering.py:948`: `or os.getenv("OPENAI_BASE_URL")`
- `patent/server/patent/hybrid_synthesis.py:271`: `"OPENAI_BASE_URL",`
- `patent/server/patent/pdf_service.py:879`: `"OPENAI_BASE_URL",`
- `patent/server/patent/runtime.py:259` default="": `shared_base_url = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL") or ""`
- `patent/server/patent/tabular_service.py:535`: `"OPENAI_BASE_URL",`
- `public-service/backend/app/modules/documents/service.py:122`: `"OPENAI_BASE_URL",`
- `public-service/backend/app/modules/documents/translator.py:35`: `"OPENAI_BASE_URL",`

### `OPENAI_MODEL`
- `fastQA/app/modules/generation_pipeline/query_expander.py:41` default="qwen-plus": `self.model = model or os.getenv("QUERY_EXPANSION_MODEL") or os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("DASHSCOPE_MODEL") or "qwen-plus"`
- `fastQA/app/modules/generation_pipeline/runtime_bootstrap.py:85`: `resolved_model = model or _env_first("LLM_MODEL", "OPENAI_MODEL", "DASHSCOPE_MODEL", default="qwen-plus")`
- `fastQA/app/modules/qa_pdf/llm_factory.py:62` default=os.getenv("DASHSCOPE_MODEL", "deepseek-v3.1": `os.getenv("LLM_MODEL", os.getenv("OPENAI_MODEL", os.getenv("DASHSCOPE_MODEL", "deepseek-v3.1"))),`
- `highThinkingQA/server/services/documents_service.py:49` default="": `or str(os.getenv("OPENAI_MODEL", "")).strip()`
- `patent/server/patent/answering.py:955`: `or os.getenv("OPENAI_MODEL")`
- `patent/server/patent/hybrid_synthesis.py:277`: `"OPENAI_MODEL",`
- `patent/server/patent/pdf_service.py:885`: `"OPENAI_MODEL",`
- `patent/server/patent/runtime.py:260` default="": `shared_model = os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("DASHSCOPE_MODEL") or ""`
- `patent/server/patent/tabular_service.py:541`: `"OPENAI_MODEL",`
- `public-service/backend/app/modules/documents/service.py:126`: `self._openai_model = _first_env("LLM_MODEL", "OPENAI_MODEL", "DASHSCOPE_MODEL", default="deepseek-v3.1")`
- `public-service/backend/app/modules/documents/translator.py:39`: `self.model = model or _first_env("LLM_MODEL", "OPENAI_MODEL", "DASHSCOPE_MODEL", default="deepseek-v3.1")`

### `PATENT_PORT`
- `patent/config.py:263` default=8787: `port=_read_int("PATENT_PORT", 8787),`
- `patent/scripts/start.sh:24` default=8010: `export PATENT_PORT="${PATENT_PORT:-8010}"`
- `patent/scripts/start_gunicorn.sh:29` default=8010: `export PATENT_PORT="${PATENT_PORT:-8010}"`
- `patent/scripts/start_gunicorn.sh:95`: `echo "patent gunicorn started: pid=$PID port=${PATENT_PORT}"`
- `patent/scripts/status_gunicorn.sh:16` default=8010: `export PATENT_PORT="${PATENT_PORT:-8010}"`
- `patent/scripts/status_gunicorn.sh:34`: `echo "patent gunicorn running: pid=$PID port=$PATENT_PORT"`
- `patent/scripts/status_gunicorn.sh:35`: `ss -ltnp "( sport = :$PATENT_PORT )" 2>/dev/null \|\| true`
- `patent/scripts/status_gunicorn.sh:45`: `ss -ltnp "( sport = :$PATENT_PORT )" 2>/dev/null \|\| true`
- `patent/scripts/stop_gunicorn.sh:13` default=8010: `export PATENT_PORT="${PATENT_PORT:-8010}"`
- `patent/scripts/stop_gunicorn.sh:38`: `fuser -k "${PATENT_PORT}/tcp" 2>/dev/null \|\| true`
- `patent/scripts/stop_gunicorn.sh:42`: `if ss -ltn "( sport = :$PATENT_PORT )" 2>/dev/null \| rg -q ":${PATENT_PORT}\\\\b"; then`
- `patent/scripts/stop_gunicorn.sh:43`: `echo "patent gunicorn stop incomplete: port ${PATENT_PORT} still in use"`
- `scripts/_service_common.sh:35`: `GATEWAY_PORT\|PUBLIC_SERVICE_PORT\|FASTQA_PORT\|FASTQA_FASTAPI_PORT\|HIGHTHINKINGQA_PORT\|PATENT_PORT) ;;`
- `scripts/_service_common.sh:77` default=8010: `patent) echo "${PATENT_PORT:-8010}" ;;`

### `PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN`
- `fastQA/app/routers/qa.py:215` default="": `expected_token = str(os.getenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "") or "").strip()`
- `fastQA/app/services/chat_persistence.py:22` default="": `service_token=str(os.getenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "") or "").strip(),`
- `fastQA/app/services/conversation_authority_client.py:13`: `_TOKEN_ENV = "PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN"`
- `gateway/app/services/conversation_persistence.py:603` default="": `token = str(os.getenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "") or "").strip()`
- `gateway/app/services/quota_proxy.py:121` default="": `token = str(os.getenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "") or "").strip()`
- `highThinkingQA/server/services/chat_persistence.py:25` default="": `service_token=str(os.getenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "") or "").strip(),`
- `highThinkingQA/server/services/conversation_authority_client.py:13`: `_TOKEN_ENV = "PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN"`
- `highThinkingQA/server_fastapi/routers/ask.py:185` default="": `expected_token = str(os.getenv("PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN", "") or "").strip()`
- `patent/scripts/start.sh:28`: `export PATENT_AUTHORITY_INTERNAL_TOKEN="${PATENT_AUTHORITY_INTERNAL_TOKEN:-${PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN:-}}"`
- `patent/scripts/start_gunicorn.sh:33`: `export PATENT_AUTHORITY_INTERNAL_TOKEN="${PATENT_AUTHORITY_INTERNAL_TOKEN:-${PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN:-}}"`
- `public-service/backend/app/modules/conversation/internal_api.py:35`: `_INTERNAL_TOKEN_ENV = "PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN"`

### `REDIS_PASSWORD`
- `fastQA/app/core/config.py:331` default="123456": `redis_password=str(os.getenv("REDIS_PASSWORD", "123456") or "123456"),`
- `gateway/app/core/config.py:150` default="": `password=str(os.getenv("REDIS_PASSWORD", "") or "").strip(),`
- `highThinkingQA/server/services/redis_client.py:136` default="123456": `password = str(os.getenv("REDIS_PASSWORD", "123456") or "123456")`
- `patent/scripts/start.sh:37` default=123456: `REDIS_PASSWORD="${REDIS_PASSWORD:-123456}"`
- `patent/scripts/start.sh:39`: `if [[ -n "${REDIS_USERNAME:-}" \|\| -n "${REDIS_PASSWORD:-}" ]]; then`
- `patent/scripts/start.sh:40`: `REDIS_AUTH="${REDIS_USERNAME:-}:${REDIS_PASSWORD:-}@"`
- `patent/scripts/start_gunicorn.sh:42` default=123456: `REDIS_PASSWORD="${REDIS_PASSWORD:-123456}"`
- `patent/scripts/start_gunicorn.sh:44`: `if [[ -n "${REDIS_USERNAME:-}" \|\| -n "${REDIS_PASSWORD:-}" ]]; then`
- `patent/scripts/start_gunicorn.sh:45`: `REDIS_AUTH="${REDIS_USERNAME:-}:${REDIS_PASSWORD:-}@"`
- `public-service/backend/app/core/config.py:237` default="123456": `redis_password=str(os.getenv("REDIS_PASSWORD", "123456") or "123456"),`

### `REDIS_USERNAME`
- `fastQA/app/core/config.py:330` default="": `redis_username=(str(os.getenv("REDIS_USERNAME", "") or "").strip() or None),`
- `gateway/app/core/config.py:149` default="": `username=str(os.getenv("REDIS_USERNAME", "") or "").strip(),`
- `patent/scripts/start.sh:36`: `REDIS_USERNAME="${REDIS_USERNAME:-}"`
- `patent/scripts/start.sh:39`: `if [[ -n "${REDIS_USERNAME:-}" \|\| -n "${REDIS_PASSWORD:-}" ]]; then`
- `patent/scripts/start.sh:40`: `REDIS_AUTH="${REDIS_USERNAME:-}:${REDIS_PASSWORD:-}@"`
- `patent/scripts/start_gunicorn.sh:41`: `REDIS_USERNAME="${REDIS_USERNAME:-}"`
- `patent/scripts/start_gunicorn.sh:44`: `if [[ -n "${REDIS_USERNAME:-}" \|\| -n "${REDIS_PASSWORD:-}" ]]; then`
- `patent/scripts/start_gunicorn.sh:45`: `REDIS_AUTH="${REDIS_USERNAME:-}:${REDIS_PASSWORD:-}@"`
- `public-service/backend/app/core/config.py:236` default="": `redis_username=(str(os.getenv("REDIS_USERNAME", "") or "").strip() or None),`

### `RESOURCE_ROOT`
- `fastQA/app/core/config.py:25`: `RESOURCE_ROOT = resolve_resource_root()`
- `fastQA/app/core/env_loader.py:59`: `raw = _read_env("RESOURCE_ROOT")`
- `fastQA/app/routers/health.py:6`: `from app.core.config import RESOURCE_ROOT, SERVICE_RUNTIME_ROOT, SERVICE_STATE_ROOT`
- `fastQA/app/routers/health.py:98`: `"resource_root": str(RESOURCE_ROOT) if RESOURCE_ROOT is not None else None,`
- `gateway/app/core/env_loader.py:57`: `raw = _read_env("RESOURCE_ROOT")`
- `highThinkingQA/config.py:27`: `RESOURCE_ROOT = resolve_resource_root()`
- `highThinkingQA/env_loader.py:60`: `raw = _read_env("RESOURCE_ROOT")`
- `patent/config.py:68` default="": `raw = str(os.getenv("RESOURCE_ROOT", "") or "").strip()`
- `public-service/backend/app/core/env_loader.py:42` default="": `raw = str(os.getenv("RESOURCE_ROOT", "") or "").strip()`

### `RETRIEVAL_TOP_K`
- `highThinkingQA/agent_core/graph.py:422`: `resolved_retrieval_top_k = int(retrieval_top_k) if retrieval_top_k is not None else int(config.RETRIEVAL_TOP_K)`
- `highThinkingQA/config.py:305` default=3: `retrieval_top_k=_get_int("RETRIEVAL_TOP_K", 3, minimum=1),`
- `highThinkingQA/config.py:414`: `RETRIEVAL_TOP_K = SETTINGS.retrieval_top_k`
- `highThinkingQA/ingest/vector_store.py:148`: `top_k = config.RETRIEVAL_TOP_K`
- `highThinkingQA/ingest/vector_store.py:180`: `top_k = config.RETRIEVAL_TOP_K`
- `highThinkingQA/retriever/vector_retriever.py:95`: `top_k = config.RETRIEVAL_TOP_K`
- `highThinkingQA/retriever/vector_retriever.py:232`: `top_k = config.RETRIEVAL_TOP_K`
- `highThinkingQA/server/services/stage_cache.py:201`: `int(top_k or config.RETRIEVAL_TOP_K),`
- `highThinkingQA/server/services/stage_cache.py:212`: `int(top_k or config.RETRIEVAL_TOP_K),`
