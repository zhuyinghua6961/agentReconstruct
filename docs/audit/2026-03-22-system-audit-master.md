# 2026-03-22 系统细粒度审查总文档

## 审查范围
- 聊天记录持久化职责归属：`fastQA` / `highThinkingQA` / `gateway` / `public-service`
- Redis 缓存是否生效、谁在使用、`public-service` 是否承担 Redis 缓存职责
- 当前向量数据库中的 chunk 结构、片段内容、位置信息元数据
- 旧版 highThinking 与新版 `highThinkingQA` 的功能差异
- `highThinkingQA` 的聊天记录持久化归属
- `highThinking` / `highThinkingQA` 从提问到输出答案过程中可用 Redis 缓存点

## 方法
- 只读审查代码、配置、向量库文件、SQLite 元数据表、现有测试与资源目录
- 不修改任何业务代码
- 边查边写，把中间结论直接沉淀到分文档

## 子文档
- [聊天持久化与 Redis 审查](./2026-03-22-chat-persistence-and-redis-audit.md)
- [向量数据库与 Chunk 元数据审查](./2026-03-22-vector-db-chunk-audit.md)
- [highThinking 新旧版本对照审查](./2026-03-22-highthinking-parity-audit.md)
- [highThinking 缓存机会点审查](./2026-03-22-highthinking-cache-opportunities.md)

## 服务与基线映射
- `fastQA/`: 新版快速问答服务
- `gateway/`: 统一入口、模式判定、代理、SSE 汇总、主前端接入层
- `public-service/`: 公共能力服务，负责 conversation authority、文件元数据、上传 worker、quota、Redis 公共缓存与锁
- `highThinkingQA/`: 新版迁移后的思考模式后端
- 旧版 highThinking 并不在单独的 `highThinking/` 目录中，而是仓库根目录的原始单体代码树：`server/`、`server_fastapi/`、`retriever/`、`ingest/`、`vectordb/`、`papers/`、`prompts/`、`uploads/` 等共同构成旧版 baseline

---

## 核心结论摘要

### 1. 聊天记录持久化的真实责任边界
- `gateway` 主链下，**`public-service` 是当前 authority**。
- `fastQA` 直连路径下，**不是 `public-service` 落库，而是 `fastQA` 调旧版 `server.services.conversation.conversation_service`**。
- `highThinkingQA` 也是同样：**当前仍直接依赖旧版 conversation service 做持久化与上下文读取**。

### 2. public-service 的 Redis 是实装，不是空壳
- 默认配置 `REDIS_ENABLED=1`
- 启动时真实 bootstrap
- 真实承担：
  - conversation list/detail cache
  - recent pages cache
  - conversation JSON distributed lock
  - upload worker lock
  - quota cache / quota lease
  - system diagnostics

### 3. fastQA 的 Redis 已接入主问答代码，但默认共享配置关闭
- 已接入普通 `kb_qa` generation-driven 主链：
  - stage1 cache
  - stage2 cache
  - singleflight lock
  - pdf cache
- 但默认 `resource/config/services/fastQA/config.shared.env` 是 `REDIS_ENABLED=0`
- 所以更准确的结论是：
  - **代码已接入**
  - **默认提交态不自动生效**

### 4. highThinkingQA 当前没有 Redis 业务缓存层
- 无 Redis 配置
- 无 Redis bootstrap
- 只有进程内 / 文件级轻缓存

### 5. 当前向量库确实保存 chunk 文本，但位置元数据普遍偏弱
- highThinking / highThinkingQA：
  - 有 `doi/title/section_name/chunk_index/total_chunks/token_count`
  - 没有稳定页码/段号
- fastQA 主向量库：
  - 有 `doi/title/source_file/chunk_id/data_quality`
  - 没有页码/section 名
- fastQA MD 库：
  - 抽样看到 `document_name/filename/chunk_id/is_full_document`
  - 代码支持更丰富字段，但样本上没有证明稳定存在 `page/doi/source_doi`

### 6. highThinkingQA 问答主链本身迁移度较高，但服务解耦度不够
- 核心 agent pipeline 基本保留
- 新版主要增强在 envelope 与 metadata
- 但 conversation authority、上下文读取、缓存层仍没有完全迁走

---

## 分主题导航

### A. 如果你只关心“聊天记录到底谁在做”
看：`2026-03-22-chat-persistence-and-redis-audit.md`

结论一句话：
- gateway 主链是 `public-service`
- 直打 `fastQA/highThinkingQA` 仍是旧版 `conversation_service`

### B. 如果你只关心“Redis 到底有没有在用”
看：`2026-03-22-chat-persistence-and-redis-audit.md`

结论一句话：
- `public-service` 在用
- `fastQA` 代码接好了但默认配置没开
- `highThinkingQA` 没有 Redis 业务缓存层

### C. 如果你只关心“向量数据库的 chunk 有没有保存正文和位置”
看：`2026-03-22-vector-db-chunk-audit.md`

结论一句话：
- 正文片段保存了
- 精确页码/段号大多没有

### D. 如果你只关心“新版 highThinkingQA 比旧版还差什么”
看：`2026-03-22-highthinking-parity-audit.md`

结论一句话：
- agent 主链差得不多
- 真正没完成的是 authority 解耦和缓存层

### E. 如果你只关心“highThinking 哪些地方应该上 Redis”
看：`2026-03-22-highthinking-cache-opportunities.md`

结论一句话：
- 最值得缓存的是 context / rewrite / decomposition / retrieval / sub-answer，不是 final answer

---

## 当前最重要的架构判断

### 判断 1
`public-service` 已经是公共 authority 和公共缓存服务，但 mode-specific QA 服务还没有完全收口到它。

### 判断 2
当前系统最明显的架构断层在于：
- `gateway -> public-service` 这条线已经在收口
- `fastQA/highThinkingQA -> old monolith conversation_service` 这条旧耦合还没彻底切断

### 判断 3
如果下一步目标是“真正完成迁移”，优先级应该高于新 feature 的是：
- 统一 conversation authority
- 明确 mode service 的上下文读取来源
- 给 highThinkingQA 补 Redis 中间结果缓存
- 统一向量 chunk metadata schema

---

## 产出说明
本次审查严格遵守：
- 不改业务代码
- 只写文档
- 边查边写

当前审查文档已经覆盖用户提出的四个核心问题：
1. 聊天持久化责任归属
2. Redis 是否生效、谁在用、public-service 是否承担 Redis
3. 当前向量数据库 chunk 是否保存正文与位置信息
4. 旧版 highThinking vs 新版 highThinkingQA 的差异，以及 highThinking 可接入 Redis 的位置
