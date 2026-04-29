# 配置开关退役与层级归属清单

> 日期：2026-04-29
> 范围：gateway、public-service、fastQA、highThinkingQA、patent，以及 `resource/config/shared`
> 目标：精简迁移期 feature flag，统一公共连接信息，明确各服务自身配置边界。
> 本文档最初只列清单和建议。实施细节见
> `docs/config/2026-04-29-config-layer-migration-guide.md`。

## 1. 总体原则

配置文件最终只应保留两类东西：

1. 环境差异：地址、端口、账号、密钥、数据目录、worker 数、并发数。
2. 运行调参：TTL、topK、timeout、max rows、最少引用数、chunk 大小、队列长度。

已经稳定成为主路径的功能，不应继续保留“默认关闭/可关闭”的历史开关。否则会造成：

- 配置文件过厚；
- 不同环境能力不一致；
- 用户以为能力已上线，但隐藏开关关闭；
- secret 文件混入非 secret 行为配置；
- 代码长期保留旧路径和 fallback。

## 2. 目标配置层级

建议目标结构：

```text
resource/config/shared/
  infrastructure.shared.env
  infrastructure.secret.env
  model-endpoints.shared.env
  model-endpoints.secret.env
  graph.shared.env
  graph.secret.env

resource/config/services/gateway/
  config.env
  config.shared.env
  config.secret.env

resource/config/services/public-service/
  config.env
  config.shared.env
  config.secret.env

resource/config/services/fastQA/
  config.env
  config.shared.env
  config.secret.env

resource/config/services/highThinkingQA/
  config.env
  config.shared.env
  config.secret.env

resource/config/services/patent/
  config.env
  config.shared.env
  config.secret.env
```

`config.env` 只作为本机临时 override，默认尽量为空。

## 3. 公共配置归属

### 3.1 `infrastructure.shared.env`

放所有非密基础设施连接信息，以及各服务监听端口。

建议放入：

| 配置类别 | 建议变量 | 说明 |
| --- | --- | --- |
| MySQL | `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_DATABASE`, `MYSQL_CONNECT_TIMEOUT_SECONDS`, `MYSQL_READ_TIMEOUT_SECONDS`, `MYSQL_WRITE_TIMEOUT_SECONDS`, `MYSQL_CONNECT_RETRIES`, `MYSQL_CONNECT_RETRY_DELAY_SECONDS`, `MYSQL_QUERY_RETRIES`, `MYSQL_QUERY_RETRY_DELAY_SECONDS` | MySQL 非密连接和超时。 |
| Redis | `REDIS_ENABLED`, `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_SOCKET_CONNECT_TIMEOUT_SEC`, `REDIS_SOCKET_TIMEOUT_SEC` | Redis 非密连接和超时。 |
| MinIO | `MINIO_ENDPOINT`, `MINIO_BUCKET`, `MINIO_SECURE`, `MINIO_REGION`, `MINIO_USE_PROXY`, `MINIO_DOWNLOAD_EXPIRES` | MinIO 非密连接和下载策略。若 endpoint 被视为敏感，可移到 secret。 |
| 服务端口 | `GATEWAY_PORT`, `PUBLIC_SERVICE_PORT`, `FASTQA_PORT`, `FASTQA_FASTAPI_PORT`, `HIGHTHINKINGQA_PORT`, `PATENT_PORT` | 所有后端服务端口统一可见。 |
| 服务后端地址 | `PUBLIC_BACKEND_BASE_URL`, `FAST_BACKEND_BASE_URL`, `THINKING_BACKEND_BASE_URL`, `PATENT_BACKEND_BASE_URL` | gateway 代理目标。可由端口生成时后续再简化。 |
| 服务根路径 | `RESOURCE_ROOT`, `SERVICE_CONFIG_ROOT`, `SERVICE_STATE_ROOT`, `SERVICE_RUNTIME_ROOT`, `SERVICE_ASSET_ROOT` | 如需要统一部署根路径，可放这里或单独 root 文件。 |

说明：

- 用户提出“各个服务的端口也都放进 MySQL 等连接的那个配置文件”，这里对应的就是 `resource/config/shared/infrastructure.shared.env`。
- 端口属于基础设施部署拓扑，不属于某个服务的业务行为配置。
- 服务自己的 worker、业务并发、stage 参数仍留在 service config。

### 3.2 `infrastructure.secret.env`

只放基础设施密钥：

| 配置类别 | 建议变量 | 说明 |
| --- | --- | --- |
| MySQL | `MYSQL_USER`, `MYSQL_PASSWORD` | 账号和密码。 |
| Redis | `REDIS_USERNAME`, `REDIS_PASSWORD`, `REDIS_URL` | 如果使用完整 URL 且含密码，应放 secret。 |
| MinIO | `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` | MinIO 密钥。 |
| 内部调用 | `PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN`, `GATEWAY_ADMISSION_CONTROL_TOKEN` | 服务间 token。 |
| Auth | `JWT_SECRET` | 可公共复用时放 shared secret；如服务不同，放 service secret。 |

### 3.3 `model-endpoints.shared.env`

后期所有 LLM 统一，因此模型端点应公共化。

建议放入：

| 配置类别 | 建议变量 | 说明 |
| --- | --- | --- |
| LLM | `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_TIMEOUT_SECONDS`, `LLM_STREAM_TIMEOUT_SECONDS`, `LLM_CONNECT_TIMEOUT_SECONDS`, `LLM_READ_TIMEOUT_SECONDS`, `LLM_WRITE_TIMEOUT_SECONDS`, `LLM_POOL_TIMEOUT_SECONDS`, `LLM_MAX_CONNECTIONS`, `LLM_MAX_KEEPALIVE_CONNECTIONS`, `LLM_KEEPALIVE_EXPIRY_SECONDS` | 统一 LLM endpoint、模型和 HTTP 池参数。 |
| OpenAI 兼容别名 | `OPENAI_BASE_URL`, `OPENAI_MODEL`, `DASHSCOPE_BASE_URL`, `DASHSCOPE_MODEL` | 兼容旧代码，迁移期保留，后续收敛到 `LLM_*`。 |
| Embedding | `EMBEDDING_MODEL_TYPE`, `EMBEDDING_BASE_URL`, `EMBEDDING_API_URL`, `EMBEDDING_MODEL`, `EMBEDDING_API_MODEL`, `EMBEDDING_DIMENSIONS`, `EMBEDDING_API_TIMEOUT_SECONDS` | 统一 embedding endpoint 和模型。 |
| Rerank | `QA_RETRIEVAL_RERANK_PROVIDER`, `QA_RETRIEVAL_RERANK_BASE_URL`, `QA_RETRIEVAL_RERANK_MODEL`, `QA_RETRIEVAL_RERANK_TIMEOUT` | rerank provider 和模型。 |
| OCR | `OCR_BASE_URL`, `OCR_MODEL` | highThinkingQA 使用 OCR 时的公共模型端点。 |

### 3.4 `model-endpoints.secret.env`

建议放入：

| 配置类别 | 建议变量 |
| --- | --- |
| LLM | `LLM_API_KEY`, `OPENAI_API_KEY`, `DASHSCOPE_API_KEY` |
| Embedding | `EMBEDDING_API_KEY` |
| Rerank | `RERANK_API_KEY`, `QA_RETRIEVAL_RERANK_API_KEY` |
| OCR | `OCR_API_KEY` |

### 3.5 `graph.shared.env` / `graph.secret.env`

图谱建议独立出来，因为 fastQA 和 patent 可能使用不同 Neo4j 实例或不同 database。

建议 shared：

| 配置 | 说明 |
| --- | --- |
| `FASTQA_NEO4J_URL`, `FASTQA_NEO4J_USERNAME`, `FASTQA_NEO4J_DATABASE` | fastQA 图谱非密连接。 |
| `PATENT_NEO4J_URL`, `PATENT_NEO4J_USERNAME`, `PATENT_NEO4J_DATABASE` | patent 图谱非密连接。 |
| `PUBLIC_SERVICE_NEO4J_URL`, `PUBLIC_SERVICE_NEO4J_USERNAME`, `PUBLIC_SERVICE_NEO4J_DATABASE` | public-service 如需图谱检索/preview 时使用。 |

建议 secret：

| 配置 | 说明 |
| --- | --- |
| `FASTQA_NEO4J_PASSWORD` | fastQA 图谱密码。 |
| `PATENT_NEO4J_PASSWORD` | patent 图谱密码。 |
| `PUBLIC_SERVICE_NEO4J_PASSWORD` | public-service 图谱密码。 |

兼容期可以继续读取旧的 `NEO4J_URL`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`，但应标为 legacy。

## 4. 开关退役分类

### 4.1 删除并常开

这些是已经稳定成为主路径的能力，不应继续作为“是否启用”开关暴露。

| 服务 | 当前开关 | 建议 | 理由 |
| --- | --- | --- | --- |
| fastQA | `FASTQA_GRAPH_KB_ENABLED` | 删除或仅保留测试覆盖用 override；运行时默认常开。 | 图谱能力已经可用，是 fastQA KB 主能力之一。 |
| fastQA | `FASTQA_GRAPH_KB_V2_ENABLED` | 删除并常开。 | v2 已是当前图谱主路径，v1 只应作为代码兼容期 fallback。 |
| fastQA | `FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED` | 删除并常开。 | 图谱+RAG 混合是目标体验，不应被环境误关。 |
| fastQA | `FASTQA_GRAPH_COMMUNITY_ROUTE_ENABLED` | 删除并常开。 | community 路由已纳入四路体验。 |
| fastQA | `FASTQA_GRAPH_PRECISE_NUMERIC_ENABLED` | 删除并常开。 | 精确数值图谱能力属于主能力。 |
| patent | `PATENT_GRAPH_KB_ENABLED` | 删除或默认常开。 | patent 后续也要做到类似 fastQA 图谱效果。 |
| patent | `PATENT_GRAPH_KB_V2_ENABLED` | 删除并常开。 | v2 是目标图谱链路。 |
| patent | `PATENT_GRAPH_KB_RAG_INJECTION_ENABLED` | 删除并常开。 | graph-for-RAG 是目标能力。 |
| fastQA | `FASTQA_GENERATION_RUNTIME_ENABLED` | 若旧 runtime 不再使用，删除并常开。 | 新 generation runtime 已是主路径时不应保留关闭入口。 |
| fastQA | `FASTQA_ALLOW_PLACEHOLDER_FALLBACK` | 生产删除或强制关闭；测试可保留。 | placeholder 会掩盖真实运行问题。 |
| public-service | `PUBLIC_SERVICE_ENABLE_LEGACY_CONVERSATION_FALLBACK` | 若 public-service 已是权威会话存储，删除 legacy fallback。 | 避免会话状态分裂。 |

### 4.2 默认开启但保留调参

这些能力建议默认开，但保留参数，因为它们影响性能、成本、质量或容量。

| 服务 | 配置 | 建议 | 理由 |
| --- | --- | --- | --- |
| fastQA | `QA_STAGE1_CACHE_TTL_SECONDS`, `QA_STAGE2_CACHE_TTL_SECONDS`, `QA_STAGE25_CACHE_TTL_SECONDS`, `QA_STAGE3_CACHE_TTL_SECONDS` | 保留 TTL，缓存默认开启。 | 缓存是性能主路径，但 TTL 需要调。 |
| fastQA | `QA_CACHE_EPOCH`, `QA_STAGE1_GRAPH_CACHE_VERSION`, `QA_STAGE2_GRAPH_CACHE_VERSION`, `QA_STAGE2_RETRIEVAL_VERSION` | 保留版本号。 | 用于批量失效和隔离图谱/检索变更。 |
| fastQA | `QA_STAGE4_REFERENCE_TOPK` | 保留。 | 控制最终证据候选规模。 |
| fastQA | `QA_STAGE4_MIN_CITATIONS` | 保留。 | 控制最少文献/引用数，属于质量调参。 |
| fastQA | `QA_STAGE4_EVIDENCE_CHUNKS_PER_DOI`, `QA_STAGE4_EVIDENCE_CHUNK_MAX_CHARS` | 保留。 | 控制 Stage4 上下文体积。 |
| fastQA | `QA_STAGE25_MD_*` | 保留。 | 控制 MD 扩展召回规模和补充策略。 |
| fastQA | `QA_RETRIEVAL_RERANK_CANDIDATES` | 保留。 | 控制 rerank 成本和召回质量。 |
| fastQA | `QA_STAGE2_PARALLEL_WORKERS` | 保留。 | 控制 Stage2 并发。 |
| fastQA | `FASTQA_GRAPH_KB_TIMEOUT_MS`, `FASTQA_GRAPH_KB_MAX_ROWS` | 保留。 | 图谱查询必须有超时和行数边界。 |
| patent | `PATENT_GRAPH_KB_TIMEOUT_MS`, `PATENT_GRAPH_KB_MAX_ROWS` | 保留。 | patent 图谱更大，必须可调。 |
| patent | `PATENT_STAGE4_REFERENCE_TOPK`, `PATENT_STAGE4_MIN_CITATIONS` | 保留。 | 控制最少专利证据和引用数量。 |
| patent | `PATENT_STAGE4_EVIDENCE_*` | 保留。 | 控制 Stage4 专利证据上下文大小。 |
| highThinkingQA | `HT_QA_*_CACHE_TTL_SECONDS`, `HT_QA_CACHE_EPOCH` | 保留。 | thinking 模式缓存成本高，TTL/epoch 需要调。 |
| highThinkingQA | `MAX_CHUNK_TOKENS`, `SEMANTIC_CHUNK_*`, `RETRIEVAL_TOP_K`, `NUM_SUB_QUESTIONS` | 保留。 | 影响 thinking 质量和成本。 |
| public-service | `QUOTA_*_TTL_SECONDS`, `CONVERSATION_*_TTL_SECONDS` | 保留。 | 公共服务缓存和锁参数需要按负载调。 |
| public-service | `UPLOAD_PROCESSING_*`, `OUTBOX_*` | 保留。 | 后台任务容量和可靠性参数。 |
| gateway | `INTERACTIVE_EXECUTION_*`, `INTERACTIVE_QUEUE_*` | 保留。 | 全局准入和并发控制。 |

### 4.3 保留为开关

这些不应删除，因为它们是运维安全、成本控制、调试或灰度能力。

| 服务 | 开关 | 理由 |
| --- | --- | --- |
| fastQA | `FASTQA_GRAPH_KB_QUERY_LOGGING` | 详细图谱日志可能很大，保留调试开关。 |
| fastQA | `FASTQA_GRAPH_ALLOW_SUSPICIOUS_DOI_FOR_RAG` | DOI 质量保护，默认关闭合理。 |
| fastQA | `QA_STAGE2_QUERY_EXPANSION_ENABLED` | query expansion 增加延迟和成本，应保留。 |
| fastQA | `QA_STAGE3_SKIP_PDF_WHEN_MD_HIT` | 是否跳过 PDF 会影响证据完整性，应保留。 |
| fastQA | `PDF_QA_USE_DEDICATED_LLM` | 专用模型涉及成本和路由，应保留。 |
| fastQA | `UPLOAD_QA_USE_SIDECAR` | sidecar 是否可用是部署差异，应保留。 |
| patent | `PATENT_GRAPH_KB_QUERY_LOGGING` | 详细图谱日志保留开关。 |
| patent | `PATENT_FILE_ROUTES_ENABLED` | 文件路由依赖部署能力，应保留。 |
| patent | `PATENT_DURABLE_MODE_ENABLED`, `PATENT_DURABLE_AUTHORITY_ENABLED` | 持久化权威切换属于部署/灰度控制。 |
| patent | `PATENT_PLANNING_HOT_POOL_ENABLED`, `PATENT_PLANNING_UPSTREAM_GATE_ENABLED` | 热连接和闸门与资源容量相关。 |
| highThinkingQA | `CHAT_PERSIST_ENABLED`, `CHAT_PERSIST_ASYNC` | 会话持久化模式可按部署选择。 |
| public-service | `UPLOAD_FILE_PROCESSING_ENABLED` | 上传后台处理可能按部署关闭。 |
| gateway | `GATEWAY_ADMISSION_ENABLED`, `GATEWAY_ADMISSION_DISPATCHER_ENABLED` | admission 是容量治理开关。 |
| gateway | `GATEWAY_ROUTE_CLASSIFIER_ENABLED` | gateway 意图分类可能灰度上线。 |
| gateway | `GATEWAY_STRICT_BACKEND_CONFIG` | 严格配置校验可按环境开启。 |
| gateway | `GATEWAY_TASK_EVENTS_DEBUG` | 调试日志开关。 |

## 5. 服务端口统一清单

建议统一放入 `resource/config/shared/infrastructure.shared.env`。

| 服务 | 当前常见端口 | 建议公共变量 | 说明 |
| --- | ---: | --- | --- |
| gateway | `8101` | `GATEWAY_PORT=8101` | 前端统一入口。 |
| public-service | `8102` | `PUBLIC_SERVICE_PORT=8102` | 用户、会话、文件、quota 等公共能力。 |
| fastQA | `8008` | `FASTQA_PORT=8008`, `FASTQA_FASTAPI_PORT=8008` | 兼容当前 `APP_PORT/BACKEND_PORT/FASTAPI_PORT`。后续可收敛为一个。 |
| highThinkingQA | `8009` | `HIGHTHINKINGQA_PORT=8009` | thinking 模式 QA。 |
| patent | `8010` | `PATENT_PORT=8010` | patent QA。 |
| PDF QA sidecar | `8012` | `PDFQA_SIDECAR_PORT=8012` | 如果 sidecar 长期保留，端口也应公共可见。 |
| LLM | DashScope OpenAI-compatible endpoint | `LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1` | 当前默认仍是阿里云模型；本地模型只能作为显式本地覆盖。 |
| embedding | `8001` | `EMBEDDING_PORT=8001`, `EMBEDDING_API_URL=http://127.0.0.1:8001/v1/embeddings` | 属于 model endpoints。 |
| rerank | `8084` | `RERANK_PORT=8084`, `QA_RETRIEVAL_RERANK_BASE_URL=http://127.0.0.1:8084` | 属于 model endpoints。 |
| Redis | `6379` | `REDIS_PORT=6379` | 已属于 infrastructure。 |
| MySQL | `3306` | `MYSQL_PORT=3306` | 已属于 infrastructure。 |
| MinIO | `9101` 或部署值 | `MINIO_ENDPOINT=127.0.0.1:9101` | endpoint 比单独端口更直接。 |
| fastQA Neo4j | `7688` | `FASTQA_NEO4J_URL=bolt://127.0.0.1:7688` | 建议放 graph shared。 |
| patent Neo4j | `8687` | `PATENT_NEO4J_URL=bolt://127.0.0.1:8687` | 建议放 graph shared。 |

注意：

- 服务监听端口放 `infrastructure.shared.env`。
- 模型服务端口放 `model-endpoints.shared.env` 更合理，因为它们是模型 endpoint 的组成部分。
- 图谱端口放 `graph.shared.env` 更合理，因为 fastQA 和 patent 图谱实例不同。
- MySQL/Redis/MinIO 继续留在 `infrastructure.shared.env`。

## 6. 各服务配置精简建议

### 6.1 fastQA

应从 service secret 移出：

- `FASTQA_GRAPH_KB_ENABLED`
- `FASTQA_GRAPH_KB_V2_ENABLED`
- `FASTQA_GRAPH_KB_RAG_INJECTION_ENABLED`

应从 service shared 逐步迁到公共 model config：

- `OPENAI_MODEL`
- `DASHSCOPE_MODEL`
- `QUERY_EXPANSION_MODEL`
- `FASTQA_LLM_HTTP_*`

应保留在 fastQA service shared：

- vector/db/data 路径；
- QA stage/cache 参数；
- graph timeout/max rows/logging；
- Stage4 最少引用数和证据上下文参数；
- PDF/file QA 行为参数；
- Redis key prefix。

### 6.2 patent

应从 `.env` 移出到 service shared 或 graph shared：

- `PATENT_GRAPH_KB_ENABLED`
- `PATENT_GRAPH_KB_V2_ENABLED`
- `PATENT_GRAPH_KB_RAG_INJECTION_ENABLED`

应留在 secret：

- `PATENT_OPENAI_API_KEY`，直到迁到统一 `LLM_API_KEY`；
- `PATENT_NEO4J_PASSWORD`。

应补到 service shared：

- `PATENT_GRAPH_KB_TIMEOUT_MS`
- `PATENT_GRAPH_KB_MAX_ROWS`
- `PATENT_GRAPH_KB_QUERY_LOGGING`
- `PATENT_TABULAR_*`
- `PATENT_HYBRID_*`
- `PATENT_STAGE4_EVIDENCE_*`

应逐步迁到公共 model config：

- `PATENT_OPENAI_*`
- `PATENT_EMBEDDING_*`
- `PATENT_LLM_HTTP_*`

### 6.3 highThinkingQA

应逐步迁到公共 model config：

- `LLM_*`
- `DECOMPOSE_MODEL`
- `DIRECT_ANSWER_MODEL`
- `SUB_ANSWER_MODEL`
- `EMBEDDING_*`
- `OCR_*`

应保留在 service shared：

- chunking；
- retrieval topK；
- sub-question 数；
- checker loops；
- ingestion concurrency；
- Chroma/papers/prompts/upload 路径；
- ask/SSE/conversation/cache 参数。

### 6.4 public-service

应迁到 `resource/config/services/public-service`，减少根目录 service config 依赖。

应从 service shared 移出到公共 graph config：

- `NEO4J_USERNAME`

应从 service secret 移出到公共 infrastructure/model/graph secret：

- MySQL secret；
- Redis secret；
- MinIO secret；
- LLM API key；
- Neo4j password。

应保留在 public-service service shared：

- data root；
- quota cache；
- conversation cache；
- upload processing；
- outbox；
- cleanup；
- reference preview；
- local storage；
- public-service 自己的 API/docs/CORS。

### 6.5 gateway

应从 service secret 移出到 service shared：

- `GATEWAY_REFRESH_SURVIVABLE_QA_TASKS_ENABLED`
- `GATEWAY_ADMISSION_ENABLED`
- `GATEWAY_ADMISSION_DISPATCHER_ENABLED`
- `GATEWAY_ADMISSION_WORKER_ENABLED`
- `REDIS_ENABLED`
- `REDIS_HOST`
- `REDIS_PORT`
- `REDIS_DB`
- `REDIS_KEY_PREFIX`

应留在 secret：

- `PUBLIC_SERVICE_INTERNAL_AUTH_TOKEN`
- `GATEWAY_ADMISSION_CONTROL_TOKEN`
- `REDIS_PASSWORD`，如果不迁到 shared infrastructure secret。

应补到 gateway service shared：

- backend base URLs；
- request/SSE timeout；
- admission queue/concurrency；
- route classifier；
- strict backend config；
- task event debug。

## 7. 推荐执行顺序

1. 统一各服务 env loader，让 gateway、public-service、patent 都能读取 `resource/config/shared` 和 `resource/config/services/<service>`。
2. 新增或整理公共配置文件：
   - `infrastructure.shared.env`
   - `infrastructure.secret.env`
   - `model-endpoints.shared.env`
   - `model-endpoints.secret.env`
   - `graph.shared.env`
   - `graph.secret.env`
3. 先迁移端口和公共连接信息，不改业务逻辑。
4. 清理 secret 文件里的非 secret 开关。
5. 将稳定功能开关改为代码默认常开，再从 env 文件删除。
6. 保留一轮兼容读取旧变量，并在日志中提示 legacy env。
7. 删除 legacy root/service-dir 配置依赖。
8. 最后跑全服务启动、health、gateway 真实请求、fastQA/patent 图谱请求、public-service 会话/文件接口测试。
