# 近期未提交改动说明（问题 → 对策）

> 本文档概括当前工作区**尚未提交/推送**的改动所针对的**原有问题**与**解决思路**，不按文件罗列实现细节。  
> 范围以 `git status` / `git diff --stat` 为准；根目录下个别未跟踪文件（如 `CLAUDE.md`、规划类 `docs/superpowers/*`、二进制表格等）若未纳入下文主题，可视为与核心功能无关的附属材料。

---

## 一、highThinkingQA（思考模式）

### 1.1 预回答阶段可能自拟文献标识

**原问题**：直接预回答与子问题预回答的提示词主要约束「勿乱编数、勿瞎猜」，未明确禁止模型自拟 DOI、`[DOI, Section]` 等文献标识；存在「预回答里的标识被后续综合沿用」的风险。

**对策**：在 `direct_answer.txt`、`sub_answer.txt` 中增加明确的 **Citation / DOI 规则**：本阶段无检索证据，禁止输出无法核验的 DOI 与括号引用，正式证据引用留在综合阶段。

### 1.2 综合答案中的 DOI 与当次检索不一致时难以归因

**原问题**：出现「答案里带了库里或业务侧对不上的 DOI」时，难以区分是预回答泄漏、综合幻觉、还是向量元数据与业务库不同步；缺少与 `ask_service` 引用抽取规则一致的运行时对比数据。

**对策**：

- 增加 **DOI 溯源诊断**（检索集合 R、预回答集合 P、答案集合 F），在 Step4 草稿与 Step5 终稿后输出 `DOI_TRACE` 日志；与正文 DOI 扫描逻辑统一到 `server/utils/doi.py` 的 `extract_dois_from_answer_text`，避免两套正则长期分叉。
- 在 `resource/config/services/highThinkingQA/config.shared.env` 中默认打开 `HIGHTHINKINGQA_DOI_DIAGNOSTICS=1`，便于在现网/联调日志中直接做 **F−R、与 P 的交集** 判断。

### 1.3 Checker 报错时日志信息不足以「确诊」到具体引用

**原问题**：仅记录「发现 N 个问题」，不便于对照是哪条 `citation`、何种 `problem`（例如程序化预检的「引用不在检索证据中」）。

**对策**：Checker 未通过时，将每条 issue 的 **`problem` + `citation`** 以有限条数写入 INFO 日志，便于与同一 `trace_id` 下的检索 `top_dois`、`DOI_TRACE` 交叉分析。

### 1.4 其他（若已包含在未提交 diff 中）

**原问题**：部分阶段对「问题锚定 / 提示边界」类行为需要与分解、综合、检查等调用一致，减少模型跑题或上下文漂移（具体以当前分支中 `question_anchor` 等改动为准）。

**对策**：在相关调用链上统一注入或约束问题锚定逻辑，并与现有测试、缓存键策略对齐（避免缓存误命中）。

---

## 二、fastQA（生成驱动检索 / LFP 等）

### 2.1 对比类问题检索与证据组织偏弱

**原问题**：用户问题含「对比 / 差异 / 优劣」等意图时，缺少结构化的对比检索规划与校验，证据 DOI 与 chunk 容易发散，综合阶段难以稳定对齐「各对比对象」。

**对策**：引入 **对比意图规划与校验**（`comparison_intent`、`comparison_validation`），在编排层按对比组约束 **每对象 DOI 数量、总 DOI 上限** 等，使检索与 Stage4 输入更可控。

### 2.2 检索命中多、噪声大，影响综合质量与成本

**原问题**：仅靠向量 TopK 时，进入合成的证据块偏多、偏杂，关键段落不突出。

**对策**：增加 **证据重排（evidence rerank）** 及配套开关与 TopK 环境变量（如 Stage3.5 相关配置），在合成前压缩并重排证据块。

### 2.3 Stage4 合成与「专家/深度预稿」脱节

**原问题**：两阶段或事实类合成未能稳定利用阶段一产出的 deep answer / 专家向结构稿，旧版「专家初稿参与综合」的体验与可控性不足。

**对策**：通过 **`QA_STAGE4_FACT_SYNTHESIS_INCLUDE_DEEP_ANSWER`** 等开关，在事实/受限合成路径中 **可选注入 deep answer（明确为非事实源、仅结构与衔接）**，并支持 **`QA_STAGE4_EXPERT_DRAFT_MAX_CHARS`** 等截断，避免提示过长。

### 2.4 Markdown 直读、流式合成与编排边界

**原问题**：MD 扩展、流式综合、后处理与编排之间存在行为不一致或覆盖不全（边界用例、清洗 flush、与 orchestrator 的衔接）。

**对策**：扩展 **`md_expansion`**、强化 **`synthesis_streaming` / `synthesis_postprocess`** 与 **`generation` orchestrator** 的协同；补充/调整 **Stage1～Stage4 及编排层** 的测试用例，删除或合并过时单测（如原 `test_generation_driven_rag_init` 一类与当前初始化方式不一致的用例）。

### 2.5 运行参数暴露不足

**原问题**：对比检索、证据重排、DOI 预算、Stage4 引用与 deep answer 注入等缺少可运维的默认配置说明与键名。

**对策**：在 **`resource/config/services/fastQA/config.shared.env`** 中补充一组可调的默认值（与代码中的 feature flag / env 读取一致）。

---

## 三、deploy / 基础设施脚本与数据面

### 3.1 部署包与真实栈不一致

**原问题**：文档与 Compose 主要覆盖部分后端，缺少 **patentQA**、**前端 Nginx 网关** 等组件的一体化说明；离线/远端模型与 **Embedding、Rerank** 的环境变量约定在 README 中过时或与现网实践不一致。

**对策**：更新 **`deploy/docker-compose.yml`**、**`.env.example` / `.env.production.example`**、**`deploy/README.md`**，增加中文说明入口 **`README.zh-CN.md`**；补充 **`Dockerfile.patent`**、**`Dockerfile.frontend-nginx`**、**`nginx/frontend-vue-gateway.docker.conf`** 等构建与路由材料。

### 3.2 种子数据与图数据库

**原问题**：专利向量、Neo4j 等数据在交付或离线打包时缺少标准目录与收集说明。

**对策**：增加 **`deploy/neo4j-seed/`**、**`deploy/seed-data/patentQA/`** 等占位或样例路径，并更新 **`collect_minio_seed.sh`、`collect_seed_data.sh`**、**`export_images.sh`、`preflight_check.sh`** 等脚本与 README，使预检、导出与种子收集覆盖新服务。

### 3.3 MySQL 初始化与 schema 演进

**原问题**：业务库表结构与应用侧期望不一致或缺表，会导致会话、文献、配额等能力在部署环境失败。

**对策**：扩展 **`deploy/mysql-init/001_schema.sql`**，并与新增 **`tests/test_mysql_init_schema.py`** 等校验用例对齐，减少「手工改库」漂移。

### 3.4 其他

**原问题**：`.dockerignore` 等可能导致构建上下文过大或漏排除产物。

**对策**：微调忽略规则，缩短镜像构建上下文、避免无关文件进入构建。

---

## 四、顶层与跨服务测试

**原问题**：部署脚本、离线镜像包、MinIO 种子收集等缺少自动化回归，易在改 Compose 或路径后静默损坏。

**对策**：在仓库 **`tests/`** 下增加或补充 **`test_deploy_offline_bundle.py`、`test_collect_minio_seed_patents.py`** 等与部署/收集脚本相关的测试（与 `deploy` 变更配套）。

---

## 五、使用与后续建议

| 领域 | 建议 |
|------|------|
| highThinkingQA | 关注日志中 **`DOI_TRACE`** 与 **`checker issue detail`**；若生产不希望刷屏，可将 **`HIGHTHINKINGQA_DOI_DIAGNOSTICS`** 设为 `0`。 |
| fastQA | 对比类问题与证据重排相关行为以 **`config.shared.env`** 中 Stage2/3.5/4 开关为准；发版前跑全量 **`fastQA` pytest**。 |
| deploy | 合并前在目标环境执行 **`preflight_check`**、按需 **`export_images`**，并核对 **MySQL init** 与现网已有库迁移策略（避免重复执行 init 脚本的生产风险）。 |

---

*文档生成依据：当前分支工作区未提交 diff 的模块划分与公开配置键名；若你本地另有未列出的小改动，以 `git diff` 为准补一节即可。*
