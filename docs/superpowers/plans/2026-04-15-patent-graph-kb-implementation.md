# Patent Graph KB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不影响现有 `patent` staged QA 主链和文件问答语义的前提下，为普通 `kb_qa` 增加“专利图谱优先尝试，失败静默回退”的确定性图谱问答能力。

**Architecture:** 实现采用 patent-local graph module，不复用 fastQA 的 DOI Cypher 模板。`server_fastapi` 负责可关闭的 Neo4j runtime/bootstrap 和 health component，`PatentKbService.run()` 负责 graph-first preflight，命中后直接返回 shell-compatible result，未命中或失败则继续走现有 staged runtime / retrieval fallback / stub fallback。Phase 1 只做确定性模板查询与渲染，不引入新的 LLM 合成链路，也不把图谱逻辑深插进 generation orchestrator。

**Tech Stack:** FastAPI, Python dataclasses, pytest, Neo4j Python driver, patent `kb_qa` service boundary, deterministic Markdown rendering, env-based feature flags

---

## Source Documents

- Design spec:
  - `docs/superpowers/specs/2026-04-15-patent-graph-kb-design.md`
- Reference implementation plan:
  - `docs/superpowers/plans/2026-04-11-fastqa-literature-graph-kb-implementation.md`
- fastQA graph implementation reference:
  - `fastQA/app/modules/graph_kb/models.py`
  - `fastQA/app/modules/graph_kb/classifier.py`
  - `fastQA/app/modules/graph_kb/client.py`
  - `fastQA/app/modules/graph_kb/service.py`
  - `fastQA/app/integrations/neo4j/client.py`
- Patent entry points:
  - `patent/config.py`
  - `patent/server_fastapi/app.py`
  - `patent/server_fastapi/routers/health.py`
  - `patent/server/patent/kb_service.py`
  - `patent/server/patent/executor.py`
  - `patent/server/patent/models.py`
- Existing patent tests:
  - `patent/tests/test_patent_kb_service.py`
  - `patent/tests/test_patent_executor.py`
  - `patent/tests/fastapi_contract/test_health_contract.py`

## Current-State Implementation Notes

- `patent` 普通问答当前没有 Neo4j bootstrap、graph preflight、Cypher query layer。
- `PatentKbService.run()` 当前优先使用 staged runtime；没有 staged runtime 时才走 retrieval fallback；再不具备 live backend 时，普通 `kb_qa` 才走 stub fallback。
- `PatentExecutor.execute_with_progress()` 对普通问答只负责 normalize context 并转交 `PatentKbService.run()`，因此 graph preflight 应放在 `PatentKbService`，不是 executor 主逻辑。
- `patent/server/patent/orchestrators/generation.py` 负责 stage1-stage4，不应被图谱遍历逻辑污染。
- `patent` 配置入口是 `patent/config.py` 和本服务目录下的 env 文件，不是 `resource/config/services/fastQA/*`。
- `server_fastapi.app.create_app()` 当前初始化 `component_status` 的 `redis`、`authority`、`runtime`，health endpoint 会直接透出 `component_status`。
- fastQA 已经有 graph-first fallback 形态可以参考，但其查询主键是 DOI，Cypher 模板依赖 `:doi / :process / :testing / :raw_materials`，这些不能用于专利图谱。

## Patent Graph Facts To Preserve

- 专利 Neo4j 独立实例：
  - Root: `/home/cqy/neo4j/neo4j-community-test/neo4j-community-5.26.7/`
  - Bolt: `bolt://127.0.0.1:8687`
  - HTTP: `http://127.0.0.1:8474`
  - Database: `neo4j`
- 主入口字段：
  - `Patent.patent_id`
  - 不是 DOI
  - 没有 `publication_number`
  - 没有 `application_number`
- `Patent` 当前属性：
  - `patent_id`
  - `title`
  - `abstract`
  - `application_date`
  - `publication_date`
  - `ipc_main`
  - `patent_type`
  - `legal_status`
  - `source_file`
  - `stub`
  - `gid`
  - `_labels`
- `Patent` 节点分层：
  - `stub = NULL` 为完整专利主证据层
  - `stub = TRUE` 多数是引用占位节点
  - Phase 1 对用户直接查询的 target patent 若 `stub = TRUE`，默认回退 staged QA
- Phase 1 允许使用的 traversal：
  - `Patent -> CLASSIFIED_AS / IN_IPC_SUBCLASS`
  - `Patent -> HAS_APPLICANT / HAS_AGENCY / HAS_INVENTOR`
  - `Patent -> ADDRESSES / PROPOSES / HAS_APPLICATION_SCENARIO`
  - `Patent -> HAS_INVENTIVE_POINT / HAS_PERFORMANCE_FACT`
  - `Patent -> PROTECTION_INCLUDES / CLAIM_INCLUDES_STEP`
  - `Patent -> HAS_PROCESS_STEP -> INSTANCE_OF / NEXT_STEP`
  - `Patent -> HAS_MATERIAL_ROLE -> OPTION_INCLUDES -> Material`
  - `Patent -> HAS_EXPERIMENT_TABLE -> HAS_ROW -> HAS_MEASUREMENT`
  - `Patent -> CITES_PATENT -> Patent`

## Workspace Conventions

所有命令默认以仓库根目录为起点；不要把 `/home/cqy/worktrees/highThinking` 写死进执行命令。

推荐先拿根目录：

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
```

然后用 repo-relative 命令：

```bash
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest tests/test_patent_graph_kb_classifier.py -q
```

## Verification Tiers

1. **Tier A: Repo-local verification**
   - 单元测试、契约测试、health 响应测试、fallback 集成测试
   - 默认不依赖真实 Neo4j 服务
2. **Tier B: Service-backed verification**
   - 使用本机专利 Neo4j `bolt://127.0.0.1:8687`
   - 只作为 release gate / smoke test
   - 不把外部服务状态作为普通单测前置条件

## Hard Rules

1. 不允许在 patent graph code 中出现 fastQA 的 DOI 查询模板，例如 `MATCH (d:doi)`、`:raw_materials`、`:testing`、`:process`。
2. 不允许把通用 `NEO4J_URL` 当作 patent graph 的默认来源；必须使用 `PATENT_NEO4J_*`，避免误连 fastQA 文献图谱。
3. `PATENT_GRAPH_KB_ENABLED` 默认必须是关闭值。
4. 图谱关闭、图谱未配置、Neo4j 不可用、查询超时、模板未命中、结果为空、target patent 是薄 `stub`，都必须静默回退现有主链。
5. 图谱不可用不得让 `runtime` component 变成 not ready，不得让非 durable health 因图谱单独返回 503。
6. 只接入普通 `kb_qa`；不得改写 `pdf_qa / tabular_qa / hybrid_qa` 路由语义。
7. 不新增用户可见 route、mode 或前端开关。
8. Phase 1 图谱答案必须确定性渲染，不调用 generation runtime，不新增 LLM 合成。
9. Follow-up / 指代型 / 文件上下文问题一律 `skip` 图谱。
10. 不把 graph traversal 插入 `patent/server/patent/orchestrators/generation.py`。
11. 每个功能 task 按 TDD：红灯测试 -> 最小实现 -> 目标测试转绿 -> review -> commit。

## Query Spec Freeze

这个 implementation plan 显式包含 spec 要求的“query spec 阶段”。任何代码任务开始前，必须先把本节里的 query contract 重新对照 live schema 复核一遍；如果某个模板无法用真实 schema 稳定支撑，就先从 Phase 1 范围里删掉，而不是边实现边猜字段。

### Graph-First Question Buckets

只允许以下问题族进入 Phase 1 graph-first：

1. 专利号直查
2. IPC / 申请人维度的专利列表
3. 技术问题 / 技术方案 / 应用场景列举
4. 工艺步骤抽取
5. 原料角色与候选材料抽取
6. 实验表格 / 数值测量抽取
7. 发明点 / 性能事实 / 保护范围 / claim step 标签抽取
8. 引证网络查询

### Staged-QA Fallback Buckets

以下问题族一律不做 graph-first：

1. follow-up / 指代型问题
2. 带文件上下文的问题
3. 需要 paragraph / claim 原文锚点的问题
4. 需要长文本推理或开放式综述的问题
5. 图谱覆盖稀薄或结果只剩 `stub` 的问题
6. 文献 DOI 问题

### Schema Revalidation Gate

实现每个模板前必须先执行只读 schema 确认，锁定真实叶子属性名：

```bash
CALL db.schema.nodeTypeProperties()
CALL db.schema.relTypeProperties()
```

必要时补样本确认：

```bash
MATCH (n:TechnicalProblem) RETURN keys(n), n LIMIT 5
MATCH (n:TechnicalSolution) RETURN keys(n), n LIMIT 5
MATCH (n:ApplicationScenario) RETURN keys(n), n LIMIT 5
MATCH (n:InventivePoint) RETURN keys(n), n LIMIT 5
MATCH (n:PerformanceFact) RETURN keys(n), n LIMIT 5
MATCH (n:ProtectionScope) RETURN keys(n), n LIMIT 5
MATCH (n:ClaimStepLabel) RETURN keys(n), n LIMIT 5
MATCH (n:ExperimentTable) RETURN keys(n), n LIMIT 5
MATCH (n:TableRow) RETURN keys(n), n LIMIT 5
MATCH (n:Measurement) RETURN keys(n), n LIMIT 5
MATCH (n:Organization) RETURN keys(n), n LIMIT 5
MATCH (n:Person) RETURN keys(n), n LIMIT 5
MATCH (n:IPC) RETURN keys(n), n LIMIT 5
MATCH (n:IPCPrefix) RETURN keys(n), n LIMIT 5
```

规则：

1. plan 中出现的 alias 名是实现契约。
2. Neo4j 节点真实属性名可以不同，但必须在 query 中被 alias 成这里约定的字段。
3. 如果某个模板无法从 live schema 稳定产出这些 alias，就缩小 Phase 1 模板范围，不允许硬猜字段。

### Stub Handling Policy

`stub` 处理必须统一，不允许不同模板各自随意决定：

1. 直接 target patent 模板：
   - 只要命中行的 target `Patent.stub = TRUE`，整体 fallback 到 staged QA
2. 返回 Patent 列表的模板：
   - `list_patents_by_ipc`
   - `list_patents_by_applicant`
   - `list_patent_citations`
   - 默认过滤结果中的 `stub = TRUE`
3. 引证模板中的被引专利：
   - 默认过滤 `cited_stub = TRUE`
4. 过滤后如果没有任何完整专利结果：
   - `handled=False`
   - `fallback_reason="stub_only_result"`
5. renderer 不得把 stub-only 专利直接当主证据输出。
6. service 可以在 metadata 里记录 `stub_filtered_count`，但答案正文不需要解释过滤细节。

### Template Contracts

#### `lookup_patent_by_id`

适用问题：
- `CN100355122C 这件专利是什么？`
- `CN100355122C 的基本信息是什么？`

Traversal：
- `Patent -> CLASSIFIED_AS / IN_IPC_SUBCLASS`
- `Patent -> HAS_APPLICANT / HAS_AGENCY / HAS_INVENTOR`

行粒度：
- 一行一个 target patent

Required aliases：
- `patent_id`
- `title`
- `abstract`
- `application_date`
- `publication_date`
- `ipc_main`
- `patent_type`
- `legal_status`
- `source_file`
- `stub`
- `ipc_codes`
- `ipc_subclasses`
- `applicants`
- `agencies`
- `inventors`

Fallback：
- target patent 未命中
- target patent 为 `stub`
- 标题等核心元数据缺失到无法形成最小确定性回答

#### `list_patent_process_steps`

适用问题：
- `CN100355122C 的工艺步骤是什么？`

Traversal：
- `Patent -> HAS_PROCESS_STEP -> ProcessStep`
- `ProcessStep -> INSTANCE_OF -> StepTemplate`
- `ProcessStep -> NEXT_STEP -> ProcessStep`

行粒度：
- 一行一个 `ProcessStep`

Required aliases：
- `patent_id`
- `stub`
- `step_order`
- `step_name`
- `step_operation`
- `step_params_json`
- `step_template`

Fallback：
- target patent 为 `stub`
- 没有任何 step 行

#### `list_patent_material_roles`

适用问题：
- `CN100355122C 使用了哪些原料？`
- `CN100355122C 的原料角色是什么？`

Traversal：
- `Patent -> HAS_MATERIAL_ROLE -> MaterialRole`
- `MaterialRole -> OPTION_INCLUDES -> Material`

行粒度：
- 一行一个 `MaterialRole x Material` 组合

Required aliases：
- `patent_id`
- `stub`
- `role_name`
- `role_type`
- `role_ratio`
- `role_note`
- `material_name`
- `material_type`
- `material_canonical_key`

Fallback：
- target patent 为 `stub`
- 没有任何 material role 行

#### `list_patent_experiment_tables`

适用问题：
- `CN100355122C 有哪些实验表格和性能数据？`

Traversal：
- `Patent -> HAS_EXPERIMENT_TABLE -> ExperimentTable`
- `ExperimentTable -> HAS_ROW -> TableRow`
- `TableRow -> HAS_MEASUREMENT -> Measurement`

行粒度：
- 一行一个 measurement

Required aliases：
- `patent_id`
- `stub`
- `table_title`
- `row_label`
- `measurement_name`
- `measurement_value`
- `measurement_unit`
- `measurement_note`

Fallback：
- target patent 为 `stub`
- 没有任何 measurement 行
- live schema 无法稳定确认 measurement value / unit 叶子属性时，先从 Phase 1 下掉这个模板

#### `list_patent_problem_solution`

适用问题：
- `CN100355122C 解决了什么技术问题，提出了什么方案？`

Traversal：
- `Patent -> ADDRESSES -> TechnicalProblem`
- `Patent -> PROPOSES -> TechnicalSolution`
- `Patent -> HAS_APPLICATION_SCENARIO -> ApplicationScenario`

行粒度：
- 一行一个 target patent，相关叶子字段聚合为数组或去重文本列表

Required aliases：
- `patent_id`
- `stub`
- `problem_texts`
- `solution_texts`
- `scenario_texts`

Fallback：
- target patent 为 `stub`
- 三类叶子都为空

#### `list_patent_inventive_scope`

适用问题：
- `CN100355122C 的发明点和保护范围是什么？`

Traversal：
- `Patent -> HAS_INVENTIVE_POINT -> InventivePoint`
- `Patent -> HAS_PERFORMANCE_FACT -> PerformanceFact`
- `Patent -> PROTECTION_INCLUDES -> ProtectionScope`
- `Patent -> CLAIM_INCLUDES_STEP -> ClaimStepLabel`

行粒度：
- 一行一个 target patent，相关叶子字段聚合为数组或去重文本列表

Required aliases：
- `patent_id`
- `stub`
- `inventive_point_texts`
- `inventive_categories`
- `performance_fact_texts`
- `performance_categories`
- `protection_scope_texts`
- `protection_kinds`
- `claim_step_labels`

Fallback：
- target patent 为 `stub`
- 四类叶子都为空

#### `list_patent_citations`

适用问题：
- `CN100355122C 引用了哪些专利？`

Traversal：
- `Patent -> CITES_PATENT -> Patent`

行粒度：
- 一行一个被引专利

Required aliases：
- `patent_id`
- `stub`
- `cited_patent_id`
- `cited_title`
- `cited_publication_date`
- `cited_stub`

Fallback：
- target patent 为 `stub`
- 没有任何被引专利
- 过滤 `cited_stub = TRUE` 后结果为空

#### `list_patents_by_ipc`

适用问题：
- `H01M10/0525 下有哪些专利？`

Traversal：
- `Patent -> CLASSIFIED_AS -> IPC`
- 或 `Patent -> IN_IPC_SUBCLASS -> IPCPrefix`

行粒度：
- 一行一个 patent

Required aliases：
- `patent_id`
- `title`
- `application_date`
- `publication_date`
- `ipc_match`
- `stub`

Fallback：
- 未命中任何专利
- 过滤 `stub = TRUE` 后结果为空

#### `list_patents_by_applicant`

适用问题：
- `宁德时代新能源科技股份有限公司有哪些专利？`

Traversal：
- `Patent -> HAS_APPLICANT -> Organization`

行粒度：
- 一行一个 patent

Required aliases：
- `patent_id`
- `title`
- `application_date`
- `publication_date`
- `applicant_name`
- `stub`

Fallback：
- 未命中任何专利
- 过滤 `stub = TRUE` 后结果为空

## File Structure Lock-In

### Config / Runtime Surface

- Modify: `patent/config.py`
- Modify: `patent/config.shared.env.example`
- Modify: `patent/server_fastapi/app.py`
- Modify: `patent/server_fastapi/routers/health.py`

### Patent Graph Module

- Create: `patent/server/patent/graph_kb/__init__.py`
- Create: `patent/server/patent/graph_kb/models.py`
- Create: `patent/server/patent/graph_kb/classifier.py`
- Create: `patent/server/patent/graph_kb/client.py`
- Create: `patent/server/patent/graph_kb/rendering.py`
- Create: `patent/server/patent/graph_kb/service.py`
- Create: `patent/server/patent/graph_kb/neo4j_client.py`

### QA Integration

- Modify: `patent/server/patent/kb_service.py`
- Modify: `patent/server/patent/executor.py`

### Tests

- Create: `patent/tests/test_patent_graph_kb_config.py`
- Create: `patent/tests/test_patent_graph_kb_neo4j_client.py`
- Create: `patent/tests/test_patent_graph_kb_classifier.py`
- Create: `patent/tests/test_patent_graph_kb_client.py`
- Create: `patent/tests/test_patent_graph_kb_service.py`
- Modify: `patent/tests/test_patent_kb_service.py`
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `patent/tests/fastapi_contract/test_health_contract.py`

## Acceptance Targets

### A. Main-Path Safety

以下场景必须全部成立：

1. `PATENT_GRAPH_KB_ENABLED=<deprecated-disable-value>` 时，现有 `kb_qa` staged QA 行为不变。
2. `PATENT_GRAPH_KB_ENABLED=true` 但 Neo4j 不可用时，`kb_qa` 仍返回 staged QA 答案。
3. 图谱分类命中但查询失败、超时、结果为空时，`kb_qa` 仍返回 staged QA 答案。
4. 目标专利为 `stub = TRUE` 时，图谱层不输出薄证据答案，直接回退 staged QA。
5. 列表型模板默认不输出 `stub = TRUE` 的专利；若过滤后只剩 stub，则整体回退 staged QA。
6. 文件 `pdf_qa / tabular_qa / hybrid_qa` 行为无变化。

### B. Phase-1 Graph Success Path

图谱成功路径至少满足：

1. 顶层 `query_mode == "patent_graph_kb"`。
2. `metadata.query_mode == "patent_graph_kb"`。
3. 至少存在一个 `step == "patent_graph_kb"` 的 success step。
4. `route == "kb_qa"`。
5. `references` 返回专利号列表，不返回 DOI。
6. `reference_objects` 中每个对象至少包含 `canonical_patent_id` 或 `patent_id`。
7. 答案来自确定性渲染，不依赖 `PatentGenerationOrchestrator`。
8. 成功列表结果中不包含 `stub = TRUE` 的专利号。

### C. Classification / Query Contract

必须 graph-first 的问题：

1. `CN100355122C 这件专利是什么？`
2. `CN100355122C 的工艺步骤是什么？`
3. `CN100355122C 使用了哪些原料？`
4. `CN100355122C 有哪些实验表格和性能数据？`
5. `CN100355122C 解决了什么技术问题，提出了什么方案？`
6. `CN100355122C 的发明点和保护范围是什么？`
7. `CN100355122C 引用了哪些专利？`
8. `H01M10/0525 下有哪些专利？`
9. `宁德时代新能源科技股份有限公司有哪些专利？`

必须直接 skip 图谱的问题：

1. `它的工艺步骤是什么？`
2. `上面那个专利的申请人是谁？`
3. `前者和后者有什么区别？`
4. `为什么这种技术路线更有前景？`
5. `结合我上传的 PDF 和知识库回答`
6. `10.1039/c4ra15767b 这篇文献是什么？`

### D. Health / Runtime Contract

实现后 health payload 至少能看到：

1. `components.patent_graph_kb`
2. `patent_graph_kb_enabled`
3. `patent_graph_kb_ready`

并满足：

1. 图谱关闭时 `components.patent_graph_kb.status == "skipped"`。
2. 图谱开启但连接失败时 `components.patent_graph_kb.status == "degraded"`。
3. 图谱开启且可用时 `components.patent_graph_kb.status == "ok"`。
4. 图谱 degraded 不会单独导致普通 `/api/health` 返回 503。
5. `durable` readiness 仍只由既有 durable dependencies 和 route runtime requirements 决定，不把图谱列入硬依赖。

## Task 1: Freeze Patent Graph Config And Health Contract

**Files:**
- Modify: `patent/config.py`
- Modify: `patent/config.shared.env.example`
- Modify: `patent/server_fastapi/app.py`
- Modify: `patent/server_fastapi/routers/health.py`
- Test: `patent/tests/test_patent_graph_kb_config.py`
- Test: `patent/tests/fastapi_contract/test_health_contract.py`

- [ ] **Step 1: Write failing tests for graph settings defaults**

Cover:
- `PATENT_GRAPH_KB_ENABLED` default false
- `PATENT_GRAPH_KB_TIMEOUT_MS` default `3000`
- `PATENT_GRAPH_KB_MAX_ROWS` default `20`
- `PATENT_GRAPH_KB_QUERY_LOGGING` default false
- `PATENT_NEO4J_URL` default `bolt://127.0.0.1:8687`
- `PATENT_NEO4J_DATABASE` default `neo4j`
- `PATENT_NEO4J_USERNAME` default `neo4j`
- `PATENT_NEO4J_PASSWORD` default empty string

- [ ] **Step 2: Write failing tests for health exposure**

Cover:
- `create_app()` initializes `component_status["patent_graph_kb"]`
- `/api/health` returns `patent_graph_kb_enabled`
- `/api/health` returns `patent_graph_kb_ready`
- `/api/health` returns `components.patent_graph_kb`
- graph disabled status is `skipped`
- graph degraded status does not make health 503 when runtime is ready and durable mode is disabled

- [ ] **Step 3: Run targeted tests to confirm failure**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest tests/test_patent_graph_kb_config.py tests/fastapi_contract/test_health_contract.py -q
```

Expected:
- FAIL because graph settings and health fields do not exist yet

- [ ] **Step 4: Add config dataclass and settings fields**

Implement in `patent/config.py`:

```python
@dataclass(frozen=True)
class PatentGraphSettings:
    enabled: bool
    neo4j_url: str
    neo4j_username: str
    neo4j_password: str
    neo4j_database: str
    timeout_ms: int
    max_rows: int
    query_logging: bool
```

Extend `Settings`:

```python
graph_kb: PatentGraphSettings
```

Populate in `get_settings()`:

```python
graph_kb=PatentGraphSettings(
    enabled=_read_bool("PATENT_GRAPH_KB_ENABLED", False),
    neo4j_url=str(os.getenv("PATENT_NEO4J_URL", "bolt://127.0.0.1:8687") or "").strip(),
    neo4j_username=str(os.getenv("PATENT_NEO4J_USERNAME", "neo4j") or "neo4j").strip(),
    neo4j_password=str(os.getenv("PATENT_NEO4J_PASSWORD", "") or ""),
    neo4j_database=str(os.getenv("PATENT_NEO4J_DATABASE", "neo4j") or "neo4j").strip() or "neo4j",
    timeout_ms=max(100, _read_int("PATENT_GRAPH_KB_TIMEOUT_MS", 3000)),
    max_rows=max(1, _read_int("PATENT_GRAPH_KB_MAX_ROWS", 20)),
    query_logging=_read_bool("PATENT_GRAPH_KB_QUERY_LOGGING", False),
)
```

- [ ] **Step 5: Document env defaults**

Add to `patent/config.shared.env.example`:

```bash
PATENT_GRAPH_KB_ENABLED=<deprecated-disable-value>
PATENT_GRAPH_KB_TIMEOUT_MS=3000
PATENT_GRAPH_KB_MAX_ROWS=20
PATENT_GRAPH_KB_QUERY_LOGGING=false
PATENT_NEO4J_URL=bolt://127.0.0.1:8687
PATENT_NEO4J_DATABASE=neo4j
PATENT_NEO4J_USERNAME=neo4j
PATENT_NEO4J_PASSWORD placeholder: local secret
```

Do not commit real password values.

- [ ] **Step 6: Initialize graph health state**

In `create_app()`, add default component status:

```python
"patent_graph_kb": {
    "ready": False,
    "enabled": bool(settings.graph_kb.enabled),
    "status": "skipped" if not settings.graph_kb.enabled else "degraded",
}
```

In `health.py`, include top-level fields:

```python
"patent_graph_kb_enabled": bool(settings.graph_kb.enabled),
"patent_graph_kb_ready": bool(dict(components.get("patent_graph_kb") or {}).get("ready", False)),
```

Do not include `patent_graph_kb` in `_durable_dependencies_ready()` or `_runtime_ready()`.

- [ ] **Step 7: Re-run targeted tests**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest tests/test_patent_graph_kb_config.py tests/fastapi_contract/test_health_contract.py -q
```

Expected:
- PASS

- [ ] **Step 8: Commit**

```bash
git add patent/config.py patent/config.shared.env.example patent/server_fastapi/app.py patent/server_fastapi/routers/health.py patent/tests/test_patent_graph_kb_config.py patent/tests/fastapi_contract/test_health_contract.py
git commit -m "feat: add patent graph kb runtime contract"
```

## Task 2: Add Patent-Local Neo4j Bootstrap

**Files:**
- Create: `patent/server/patent/graph_kb/__init__.py`
- Create: `patent/server/patent/graph_kb/neo4j_client.py`
- Modify: `patent/server_fastapi/app.py`
- Test: `patent/tests/test_patent_graph_kb_neo4j_client.py`
- Test: `patent/tests/fastapi_contract/test_health_contract.py`

- [ ] **Step 1: Write failing tests for bootstrap result shapes**

Cover:
- disabled graph creates no client and status `skipped`
- missing Neo4j driver degrades without raising
- `GraphDatabase.driver(...).verify_connectivity()` success marks available
- connectivity failure marks degraded and stores error
- `close()` closes driver and is idempotent
- bootstrap uses `PATENT_NEO4J_URL`, not generic `NEO4J_URL`

- [ ] **Step 2: Run tests to confirm failure**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest tests/test_patent_graph_kb_neo4j_client.py tests/fastapi_contract/test_health_contract.py -q -k "graph or neo4j"
```

Expected:
- FAIL because `neo4j_client.py` and app bootstrap do not exist yet

- [ ] **Step 3: Implement bootstrap model**

Create `patent/server/patent/graph_kb/neo4j_client.py` with:

```python
@dataclass
class PatentNeo4jClient:
    driver: Any | None
    available: bool
    degraded: bool
    error: str = ""
    database: str = "neo4j"

    def close(self) -> None:
        ...

    def query(self, cypher: str, params: dict[str, Any], *, timeout_ms: int) -> list[dict[str, Any]]:
        ...
```

Create bootstrap function:

```python
def bootstrap_patent_neo4j_client(*, url: str, username: str, password: str, database: str, logger: Any | None = None) -> PatentNeo4jClient:
    ...
```

Implementation constraints:
- Import `neo4j.GraphDatabase` inside the function so tests can run without Neo4j installed.
- Use official driver connectivity verification.
- No APOC dependency.
- On any exception, return `PatentNeo4jClient(driver=None, available=False, degraded=True, error=str(exc), database=database)`.
- Do not raise from bootstrap except for programmer errors in tests.

- [ ] **Step 4: Implement timed query helper**

In `PatentNeo4jClient.query()`:
- if no driver or unavailable, return `[]`
- use `neo4j.Query(text=cypher, timeout=timeout_ms / 1000.0)` when available
- use configured database
- normalize records via `record.data()`
- convert driver timeout exceptions into Python `TimeoutError`
- return only `list[dict[str, Any]]`

- [ ] **Step 5: Wire bootstrap into app state**

In `server_fastapi/app.py`:
- add `patent_graph_kb_client = None` local variable in `_bootstrap_service_state`
- when `settings.graph_kb.enabled` is false, set component `status=skipped`
- when enabled, call `bootstrap_patent_neo4j_client(...)`
- set `app.state.patent_graph_kb_client`
- set `component_status["patent_graph_kb"]` with `ready`, `enabled`, `status`, `url`, `database`, `error`
- close `patent_graph_kb_client` in exception cleanup and lifespan shutdown

- [ ] **Step 6: Re-run targeted tests**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest tests/test_patent_graph_kb_neo4j_client.py tests/fastapi_contract/test_health_contract.py -q
```

Expected:
- PASS

- [ ] **Step 7: Commit**

```bash
git add patent/server/patent/graph_kb/__init__.py patent/server/patent/graph_kb/neo4j_client.py patent/server_fastapi/app.py patent/tests/test_patent_graph_kb_neo4j_client.py patent/tests/fastapi_contract/test_health_contract.py
git commit -m "feat: bootstrap patent neo4j graph client"
```

## Task 3: Add Patent Graph Models And Classifier

**Files:**
- Create: `patent/server/patent/graph_kb/models.py`
- Create: `patent/server/patent/graph_kb/classifier.py`
- Test: `patent/tests/test_patent_graph_kb_classifier.py`

- [ ] **Step 1: Write failing classifier tests**

Cover graph-first:
- direct patent metadata lookup
- problem/solution lookup
- process step lookup
- material role lookup
- experiment/performance lookup
- inventive point/protection lookup
- citation lookup
- IPC listing
- applicant listing

Cover skip:
- DOI question
- broad semantic question
- follow-up/pronoun question
- file-context question via `conversation_context.source_selection`
- empty question

- [ ] **Step 2: Run tests to confirm failure**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest tests/test_patent_graph_kb_classifier.py -q
```

Expected:
- FAIL because graph models/classifier do not exist

- [ ] **Step 3: Implement dataclasses**

In `models.py`:

```python
@dataclass(frozen=True)
class PatentGraphKbDecision:
    decision: str
    reason: str
    standalone: bool
    signals: tuple[str, ...] = ()

@dataclass(frozen=True)
class PatentGraphKbQueryPlan:
    template_id: str
    params: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class PatentGraphKbExecutionResult:
    handled: bool
    answer: str = ""
    references: tuple[str, ...] = ()
    reference_objects: tuple[dict[str, Any], ...] = ()
    query_mode: str = "patent_graph_kb"
    template_id: str = ""
    result_count: int = 0
    latency_ms: float = 0.0
    fallback_reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
```

- [ ] **Step 4: Implement classifier**

Classifier rules:
- normalize whitespace and trailing punctuation
- detect patent id via uppercase token like `CN100355122C`, `US6720110B2`, `WO2004045007A2`, `JP2001307726A`
- reject DOI-like `10.xxxx/...` questions
- reject file context if source selection contains pdf/table routes or selected files
- reject follow-up hints: `它`, `这个`, `那件`, `上面`, `前者`, `后者`
- reject broad hints: `为什么`, `如何评价`, `趋势`, `综述`, `对比分析`, `替代窗口`
- return `try_graph` only for direct patent-id structured questions or supported listing questions

Suggested classifier reasons:
- `patent_id_lookup`
- `patent_process_steps`
- `patent_material_roles`
- `patent_experiment_tables`
- `patent_problem_solution`
- `patent_inventive_scope`
- `patent_citations`
- `ipc_listing`
- `applicant_listing`
- `doi_not_supported`
- `ambiguous_followup`
- `file_context_present`
- `broad_semantic_question`
- `no_graph_signal`

- [ ] **Step 5: Re-run classifier tests**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest tests/test_patent_graph_kb_classifier.py -q
```

Expected:
- PASS

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/graph_kb/models.py patent/server/patent/graph_kb/classifier.py patent/tests/test_patent_graph_kb_classifier.py
git commit -m "feat: classify patent graph kb questions"
```

## Task 4: Add Patent Graph Query Planner And Cypher Client

**Files:**
- Create: `patent/server/patent/graph_kb/client.py`
- Test: `patent/tests/test_patent_graph_kb_client.py`

- [ ] **Step 1: Write failing query planner tests**

Cover:
- `CN100355122C 这件专利是什么？` -> `lookup_patent_by_id`
- `CN100355122C 的工艺步骤是什么？` -> `list_patent_process_steps`
- `CN100355122C 使用了哪些原料？` -> `list_patent_material_roles`
- `CN100355122C 有哪些实验表格和性能数据？` -> `list_patent_experiment_tables`
- `CN100355122C 解决了什么技术问题，提出了什么方案？` -> `list_patent_problem_solution`
- `CN100355122C 的发明点和保护范围是什么？` -> `list_patent_inventive_scope`
- `CN100355122C 引用了哪些专利？` -> `list_patent_citations`
- `H01M10/0525 下有哪些专利？` -> `list_patents_by_ipc`
- `宁德时代新能源科技股份有限公司有哪些专利？` -> `list_patents_by_applicant`
- DOI question returns `None`
- every plan maps to the template contracts frozen in `Query Spec Freeze`

- [ ] **Step 2: Write failing Cypher safety tests**

Cover:
- every generated query uses `Patent.patent_id` where a direct patent id is present
- every generated query has a `LIMIT`
- every template returns the aliases required by its contract
- direct target templates expose `stub`
- listing templates expose patent-level `stub`
- citation template exposes `cited_stub`
- no query contains `MATCH (d:doi)`
- no query contains `:raw_materials`
- no query contains `:testing`
- no query contains `:process)`
- query params never contain raw string interpolation for patent id
- `execute_patent_graph_plan()` trims rows to `max_rows`
- `execute_patent_graph_plan()` returns `[]` for unavailable client
- `execute_patent_graph_plan()` propagates `TimeoutError`

- [ ] **Step 3: Run tests to confirm failure**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest tests/test_patent_graph_kb_client.py -q
```

Expected:
- FAIL because query planner/client do not exist

- [ ] **Step 4: Implement planner**

Expose:

```python
def plan_patent_graph_query(question: str) -> PatentGraphKbQueryPlan | None:
    ...
```

Implementation notes:
- Extract at most one target `patent_id` for direct patent queries in Phase 1.
- Use `patent_id` param name, not `doi`.
- IPC listing uses `ipc_code`.
- applicant listing uses `organization_name`.
- If question has both patent id and recognized structural hint, prefer specific structural template over generic lookup.
- If direct patent id has no structural hint, use `lookup_patent_by_id`.
- Query planner output must only target templates whose row contracts are frozen in `Query Spec Freeze`.

- [ ] **Step 5: Implement Cypher templates**

Supported templates:
- `lookup_patent_by_id`
- `list_patent_process_steps`
- `list_patent_material_roles`
- `list_patent_experiment_tables`
- `list_patent_problem_solution`
- `list_patent_inventive_scope`
- `list_patent_citations`
- `list_patents_by_ipc`
- `list_patents_by_applicant`

Each direct-patent template must start from:

```cypher
MATCH (p:Patent {patent_id: $patent_id})
```

Each template must return `p.stub AS stub` or an equivalent `stub` field for quality gating.
Every template must alias its output into the exact contract field names from `Query Spec Freeze`; tests should assert aliases, not underlying Neo4j property names.

- [ ] **Step 6: Implement representative direct lookup query**

Use a query equivalent to:

```cypher
MATCH (p:Patent {patent_id: $patent_id})
OPTIONAL MATCH (p)-[:CLASSIFIED_AS]->(ipc:IPC)
OPTIONAL MATCH (p)-[:IN_IPC_SUBCLASS]->(sub:IPCPrefix)
OPTIONAL MATCH (p)-[:HAS_APPLICANT]->(applicant:Organization)
OPTIONAL MATCH (p)-[:HAS_AGENCY]->(agency:Organization)
OPTIONAL MATCH (p)-[:HAS_INVENTOR]->(inventor:Person)
RETURN
  p.patent_id AS patent_id,
  p.title AS title,
  p.abstract AS abstract,
  p.application_date AS application_date,
  p.publication_date AS publication_date,
  p.ipc_main AS ipc_main,
  p.patent_type AS patent_type,
  p.legal_status AS legal_status,
  p.source_file AS source_file,
  p.stub AS stub,
  collect(DISTINCT ipc.code)[0..10] AS ipc_codes,
  collect(DISTINCT sub.prefix)[0..10] AS ipc_subclasses,
  collect(DISTINCT applicant.name)[0..10] AS applicants,
  collect(DISTINCT agency.name)[0..5] AS agencies,
  collect(DISTINCT inventor.name)[0..10] AS inventors
LIMIT 1
```

If live property names for `IPC`, `IPCPrefix`, `Organization`, or `Person` differ during implementation, adjust only those leaf property reads based on actual `CALL db.schema.nodeTypeProperties()` output, not by guessing.

- [ ] **Step 7: Freeze the remaining template row shapes before coding them**

Before writing the non-lookup queries, add explicit test fixtures or inline constants that lock the alias set for:
- `list_patent_process_steps`
- `list_patent_material_roles`
- `list_patent_experiment_tables`
- `list_patent_problem_solution`
- `list_patent_inventive_scope`
- `list_patent_citations`
- `list_patents_by_ipc`
- `list_patents_by_applicant`

If live schema verification shows a template cannot satisfy its contract without guessing field names, remove that template from Phase 1 in the same change instead of shipping a weaker implicit contract.

- [ ] **Step 8: Implement execution helper**

Expose:

```python
def execute_patent_graph_plan(
    plan: PatentGraphKbQueryPlan,
    *,
    neo4j_client: Any,
    max_rows: int,
    timeout_ms: int,
) -> list[dict[str, Any]]:
    ...
```

Rules:
- if `neo4j_client.available` is false, return `[]`
- call `neo4j_client.query(cypher, params, timeout_ms=timeout_ms)` when available
- otherwise support simple graph-like test doubles with `.query(cypher, params)`
- normalize to `list[dict]`
- trim to `max_rows`
- preserve `TimeoutError`

- [ ] **Step 9: Re-run query client tests**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest tests/test_patent_graph_kb_client.py -q
```

Expected:
- PASS

- [ ] **Step 10: Commit**

```bash
git add patent/server/patent/graph_kb/client.py patent/tests/test_patent_graph_kb_client.py
git commit -m "feat: add patent graph query planner"
```

## Task 5: Add Deterministic Patent Graph Renderer And Service

**Files:**
- Create: `patent/server/patent/graph_kb/rendering.py`
- Create: `patent/server/patent/graph_kb/service.py`
- Test: `patent/tests/test_patent_graph_kb_service.py`

- [ ] **Step 1: Write failing renderer tests**

Cover:
- direct lookup renders title, abstract summary, applicant/inventor/IPC metadata
- process steps render ordered list and step templates
- material roles render role, ratio/note, candidate materials
- experiment table rows render compact table-like Markdown
- problem/solution renders technical problem, solution, scenario
- inventive/scope renders inventive point, performance fact, protection scope, claim step labels
- citations render cited patent IDs and titles
- list by IPC/applicant renders patent list
- rows with target `stub=True` return empty answer or fallback signal
- citation / ipc / applicant results that become stub-only after filtering return fallback signal
- output references are patent IDs only

- [ ] **Step 2: Write failing service orchestration tests**

Cover:
- classifier skip returns `handled=False`
- no plan returns `handled=False, fallback_reason="no_plan"`
- unavailable client returns fallback
- empty rows returns fallback
- timeout returns fallback with latency
- target stub returns fallback with `fallback_reason="stub_patent"`
- list results with only stub patents return `fallback_reason="stub_only_result"`
- citation results with only `cited_stub=True` rows return `fallback_reason="stub_only_result"`
- graph hit returns `handled=True`, `query_mode="patent_graph_kb"`, references and reference_objects
- service never touches generation runtime

- [ ] **Step 3: Run tests to confirm failure**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest tests/test_patent_graph_kb_service.py -q
```

Expected:
- FAIL because renderer/service do not exist

- [ ] **Step 4: Implement rendering helpers**

In `rendering.py`, expose:

```python
def render_patent_graph_answer(plan: PatentGraphKbQueryPlan, rows: list[dict[str, Any]]) -> tuple[str, tuple[str, ...], tuple[dict[str, Any], ...], dict[str, Any]]:
    ...
```

Rules:
- Return empty answer for rows that cannot support a safe deterministic answer.
- Deduplicate references by `patent_id`.
- Apply the `Stub Handling Policy` before rendering any list-style answer.
- Build `reference_objects` with minimal shape:

```python
{
    "canonical_patent_id": patent_id,
    "patent_id": patent_id,
    "title": title,
    "source": "patent_graph",
}
```

- Use stable headings and avoid saying “图谱证明” for partial facts.
- Mention partial coverage in metadata rather than overexplaining in answer text.

- [ ] **Step 5: Implement service orchestration**

In `service.py`, expose:

```python
def try_patent_graph_kb_answer(
    *,
    question: str,
    conversation_context: dict[str, Any] | None,
    neo4j_client: Any,
    max_rows: int,
    timeout_ms: int,
    generation_runtime: Any | None = None,
) -> PatentGraphKbExecutionResult:
    ...
```

Flow:
1. Ignore `generation_runtime`; keep parameter only for parity/testing.
2. `classify_patent_graph_kb_question(...)`
3. `plan_patent_graph_query(...)`
4. `execute_patent_graph_plan(...)`
5. quality gate:
   - empty rows -> fallback
   - target row `stub=True` -> fallback
   - list rows filtered to empty by stub policy -> fallback
   - render empty -> fallback
6. return `PatentGraphKbExecutionResult(handled=True, ...)`

- [ ] **Step 6: Re-run service tests**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest tests/test_patent_graph_kb_service.py -q
```

Expected:
- PASS

- [ ] **Step 7: Commit**

```bash
git add patent/server/patent/graph_kb/rendering.py patent/server/patent/graph_kb/service.py patent/tests/test_patent_graph_kb_service.py
git commit -m "feat: render patent graph kb answers"
```

## Task 6: Integrate Graph Preflight Into PatentKbService

**Files:**
- Modify: `patent/server/patent/kb_service.py`
- Test: `patent/tests/test_patent_kb_service.py`

- [ ] **Step 1: Write failing kb_service integration tests**

Cover:
- when graph service returns handled result, staged orchestrator is not called
- graph result maps to shell-compatible dict shape
- graph skip falls through to staged runtime
- graph timeout/fallback falls through to staged runtime
- graph disabled or missing client falls through to staged runtime
- graph success preserves `route`, `source_scope`, `file_selection`, `used_files=[]`
- graph success returns `timings["patent_graph_kb"]`
- file route request with `source_scope` including files does not use graph preflight

- [ ] **Step 2: Run tests to confirm failure**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest tests/test_patent_kb_service.py -q -k "graph or kb_service"
```

Expected:
- FAIL because `PatentKbService` has no graph dependency/preflight

- [ ] **Step 3: Extend PatentKbService constructor**

Add optional dependencies:

```python
graph_kb_service: Any | None = None
graph_kb_client: Any | None = None
graph_kb_enabled: bool = False
graph_kb_max_rows: int = 20
graph_kb_timeout_ms: int = 3000
```

Store them on `self`.

- [ ] **Step 4: Add graph preflight before staged runtime**

At the start of `run()` after `profile`, `active_runtime`, and `retrieval_service` are resolved:

```python
graph_result = self._try_graph_preflight(
    request=request,
    conversation_context=conversation_context,
    active_runtime=active_runtime,
)
if graph_result is not None:
    return graph_result
```

Constraints:
- run only when `request.route == "kb_qa"`
- run only when graph enabled
- run only when graph service and graph client exist
- catch all graph exceptions, log warning, return `None`
- do not pass `progress_callback` or `content_callback` into graph service in Phase 1

- [ ] **Step 5: Map graph result to existing payload shape**

Add helper:

```python
def _graph_execution_result_from_graph_result(...):
    return {
        "answer_text": result.answer,
        "route": str(profile.route),
        "query_mode": "patent_graph_kb",
        "steps": [
            {
                "step": "patent_graph_kb",
                "title": "专利图谱",
                "message": "专利图谱：已完成结构化图谱查询",
                "status": "success",
            }
        ],
        "references": list(result.references),
        "reference_objects": [dict(item) for item in result.reference_objects],
        "reference_links": [],
        "original_links": [],
        "metadata": {
            **dict(result.metadata or {}),
            "success": True,
            "query_mode": "patent_graph_kb",
            "template_id": result.template_id,
            "result_count": result.result_count,
            "source_ids": list(result.references),
            "graph_fallback_reason": "",
        },
        "timings": {"patent_graph_kb": result.latency_ms},
        "used_files": [],
        "file_selection": dict(request.file_selection or {}),
        "source_scope": request.source_scope,
    }
```

Use exact existing key names because router/executor contracts depend on them.

- [ ] **Step 6: Re-run kb_service tests**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest tests/test_patent_kb_service.py -q
```

Expected:
- PASS

- [ ] **Step 7: Commit**

```bash
git add patent/server/patent/kb_service.py patent/tests/test_patent_kb_service.py
git commit -m "feat: add patent kb graph preflight"
```

## Task 7: Inject Graph Service From Executor And FastAPI Bootstrap

**Files:**
- Modify: `patent/server/patent/executor.py`
- Modify: `patent/server_fastapi/app.py`
- Test: `patent/tests/test_patent_executor.py`
- Test: `patent/tests/fastapi_contract/test_health_contract.py`

- [ ] **Step 1: Write failing executor/app injection tests**

Cover:
- `PatentExecutor(graph_kb_service=..., graph_kb_client=...)` passes graph dependencies into default `PatentKbService`
- default executor without graph dependencies keeps old behavior
- `create_app()` stores `app.state.patent_graph_kb_client`
- `create_app()` constructs executor with graph service/client when enabled
- shutdown closes graph client

- [ ] **Step 2: Run tests to confirm failure**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest tests/test_patent_executor.py tests/fastapi_contract/test_health_contract.py -q -k "graph or executor or closes"
```

Expected:
- FAIL because executor/app injection is missing

- [ ] **Step 3: Extend PatentExecutor constructor**

Add optional params:

```python
graph_kb_service: Any | None = None
graph_kb_client: Any | None = None
graph_kb_enabled: bool = False
graph_kb_max_rows: int = 20
graph_kb_timeout_ms: int = 3000
```

When constructing default `PatentKbService`, pass these through.

- [ ] **Step 4: Wire app construction**

In `server_fastapi/app.py`:
- import `try_patent_graph_kb_answer`
- pass `graph_kb_service=try_patent_graph_kb_answer`
- pass `graph_kb_client=getattr(app.state, "patent_graph_kb_client", None)`
- pass `graph_kb_enabled=bool(settings.graph_kb.enabled)`
- pass `graph_kb_max_rows=settings.graph_kb.max_rows`
- pass `graph_kb_timeout_ms=settings.graph_kb.timeout_ms`

Keep file route services unchanged.

- [ ] **Step 5: Re-run targeted tests**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest tests/test_patent_executor.py tests/fastapi_contract/test_health_contract.py -q
```

Expected:
- PASS

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/executor.py patent/server_fastapi/app.py patent/tests/test_patent_executor.py patent/tests/fastapi_contract/test_health_contract.py
git commit -m "feat: inject patent graph kb service"
```

## Task 8: Regression And Contract Gate

**Files:**
- Modify tests only if regressions reveal outdated expectations:
  - `patent/tests/test_patent_kb_service.py`
  - `patent/tests/test_patent_executor.py`
  - `patent/tests/fastapi_contract/test_health_contract.py`

- [ ] **Step 1: Run graph unit tests**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest \
  tests/test_patent_graph_kb_config.py \
  tests/test_patent_graph_kb_neo4j_client.py \
  tests/test_patent_graph_kb_classifier.py \
  tests/test_patent_graph_kb_client.py \
  tests/test_patent_graph_kb_service.py \
  -q
```

Expected:
- PASS

- [ ] **Step 2: Run patent QA contract regression**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest \
  tests/test_patent_kb_service.py \
  tests/test_patent_executor.py \
  tests/fastapi_contract/test_health_contract.py \
  -q
```

Expected:
- PASS

- [ ] **Step 3: Run broader patent test suite if runtime allows**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT/patent" && PYTHONPATH=. pytest -q
```

Expected:
- PASS, or document any unrelated pre-existing failures with exact test names and failure signatures.

- [ ] **Step 4: Static audit for forbidden DOI templates**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT" && rg -n "MATCH \\(d:doi\\)|:raw_materials|:testing|:process" patent/server/patent/graph_kb patent/tests/test_patent_graph_kb_*.py
```

Expected:
- No output

- [ ] **Step 5: Static audit for generation orchestrator isolation**

Run:

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT" && rg -n "graph_kb|Neo4j|Cypher|patent_graph" patent/server/patent/orchestrators/generation.py
```

Expected:
- No output

- [ ] **Step 6: Commit any regression fixes**

```bash
git add patent/tests patent/server/patent patent/server_fastapi patent/config.py patent/config.shared.env.example
git commit -m "test: cover patent graph kb regressions"
```

Only commit if there are actual changes after Tasks 1-7.

## Task 9: Manual Neo4j Smoke Test

**Files:**
- No production code changes expected
- Optional doc update only if smoke test reveals changed live graph facts:
  - `docs/superpowers/specs/2026-04-15-patent-graph-kb-design.md`

- [ ] **Step 1: Confirm patent Neo4j is running**

Run:

```bash
/home/cqy/neo4j/neo4j-community-test/neo4j-community-5.26.7/bin/neo4j status
```

Expected:
- reports running process for the `neo4j-community-5.26.7` instance

- [ ] **Step 2: Confirm direct patent lookup in Cypher**

Run:

```bash
/home/cqy/neo4j/neo4j-community-test/neo4j-community-5.26.7/bin/cypher-shell \
  -a bolt://127.0.0.1:8687 \
  -u neo4j \
  -p "$PATENT_NEO4J_PASSWORD" \
  'MATCH (p:Patent {patent_id: "CN100355122C"}) RETURN p.patent_id, p.title, p.stub LIMIT 1'
```

Expected:
- one row for `CN100355122C`
- no DOI fields involved

- [ ] **Step 3: Run app-level graph success smoke test**

Start patent service with graph enabled in a local shell:

```bash
export PATENT_GRAPH_KB_ENABLED=true
export PATENT_NEO4J_URL=bolt://127.0.0.1:8687
export PATENT_NEO4J_DATABASE=neo4j
export PATENT_NEO4J_USERNAME=neo4j
export PATENT_NEO4J_PASSWORD
```

Then use the repository's normal patent startup path or test client harness.

Expected API result for `CN100355122C 这件专利是什么？`:
- `query_mode == "patent_graph_kb"`
- `references` contains `CN100355122C`
- answer contains patent title or metadata from graph
- no staged runtime step appears before graph step

- [ ] **Step 4: Run app-level fallback smoke test**

Ask a broad question:

```text
为什么磷酸铁锂路线在储能领域仍然有优势？
```

Expected:
- graph classifier skips
- response comes from existing staged QA path
- no graph exception leaks to client

- [ ] **Step 5: Run unavailable graph fallback smoke test**

Set:

```bash
export PATENT_GRAPH_KB_ENABLED=true
export PATENT_NEO4J_URL=bolt://127.0.0.1:18687
```

Expected:
- health shows `components.patent_graph_kb.status == "degraded"`
- ordinary `kb_qa` still returns staged QA answer
- `/api/health` degradation behavior remains governed by runtime/durable dependencies, not graph alone

- [ ] **Step 6: Document smoke result**

Add a short implementation note to the PR/body or handoff:
- graph enabled status
- sample patent id
- graph answer mode observed
- fallback mode observed
- any live schema mismatch discovered

## Execution Handoff

Use `@superpowers:subagent-driven-development` for implementation if the next session can run multiple bounded workers. Otherwise use `@superpowers:executing-plans` and execute tasks in order.

Recommended commit sequence:

1. `feat: add patent graph kb runtime contract`
2. `feat: bootstrap patent neo4j graph client`
3. `feat: classify patent graph kb questions`
4. `feat: add patent graph query planner`
5. `feat: render patent graph kb answers`
6. `feat: add patent kb graph preflight`
7. `feat: inject patent graph kb service`
8. `test: cover patent graph kb regressions`

Do not start implementation until the plan owner confirms this breakdown or asks to proceed.
