# Patent Process-Local Upstream Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `patent` 服务落地进程内共享的 LLM 上游连接池，在不破坏 `runtime=None` 时纯文件路由可用性的前提下，减少 `pdf_qa` 与普通 QA 多 client 割裂导致的冷连接首字延迟。

**Architecture:** 新增一个 app-owned 的 shared upstream HTTP provider，统一持有面向 OpenAI-compatible 上游的 `httpx.Client` 和连接池参数；`PatentPlanningClient`、`PatentAnswerBuilder`、`PatentPdfAnswerClient` 只消费注入的 shared client，并把 timeout 下沉到 request-level。FastAPI bootstrap 负责创建与关闭 shared provider 以及 app-owned `PatentPdfService`，`patent_runtime` 只消费该 shared client，不再独占其生命周期，从而继续保留 `runtime=None` 时的纯文件路由 degraded-mode。

**Tech Stack:** Python 3, FastAPI, httpx, pytest, patent server runtime/bootstrap, OpenAI-compatible upstream APIs

---

## Source Documents

- Spec: `docs/superpowers/specs/2026-04-14-patent-process-local-upstream-pool-design.md`
- Existing runtime/bootstrap tests:
  - `patent/tests/test_runtime_controls.py`
  - `patent/tests/test_patent_retrieval_service.py`
  - `patent/tests/fastapi_contract/test_health_contract.py`
  - `patent/tests/fastapi_contract/test_ask_contract.py`
- Existing LLM wrapper tests:
  - `patent/tests/test_patent_stage4_synthesis.py`
  - `patent/tests/test_patent_pdf_contract.py`
  - `patent/tests/test_patent_executor.py`
  - `patent/tests/test_patent_file_routes.py`

## Hard Rules

1. 不能把 shared pool 绑定为 `patent_runtime` 的独占资源；`runtime=None` 时纯文件路由必须继续可用。
2. 任何 wrapper 在消费外部注入的 shared client 时，`close()` 都不能误关共享资源。
3. 如果 shared pool 开关关闭，或 provider 初始化失败，系统必须回退到私有 client 路径，而不是直接让 `pdf_qa` 不可用。
4. request timeout 必须按调用链路保留，不允许因为共享 client 而把所有请求硬绑成同一个 timeout。
5. 每个 task 都先写红灯测试，再做最小实现，再跑目标测试，再走 subagent review，直到 pass 才能进入下一个 task。
6. 运行测试如果受沙箱限制，必须提权执行；如果无法提权，就停在阻塞点，不得声称验证完成。

## Per-Task Review Gate

每个 task 完成后都必须执行同一条流程：

1. 红灯测试
2. 最小实现
3. 目标测试转绿
4. 发给同一个 reviewer subagent 做 review
5. 根据 review 修正并重跑本 task 目标测试，直到 reviewer pass
6. 然后再 commit，进入下一 task

## File Map

### Shared Upstream Pool

- Create: `patent/server/patent/upstream_http.py`
- Create: `patent/tests/test_patent_upstream_http.py`

### Runtime And LLM Wrappers

- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/answering.py`
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/tests/test_runtime_controls.py`
- Modify: `patent/tests/test_patent_retrieval_service.py`
- Modify: `patent/tests/test_patent_stage1_planning.py`
- Modify: `patent/tests/test_patent_stage4_synthesis.py`
- Modify: `patent/tests/test_patent_pdf_contract.py`

### App Bootstrap And Contracts

- Modify: `patent/server_fastapi/app.py`
- Modify: `patent/config.shared.env.example`
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `patent/tests/test_patent_file_routes.py`
- Modify: `patent/tests/fastapi_contract/test_health_contract.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`

## Lock Decisions

1. shared pool 的唯一所有者是 FastAPI app bootstrap，不是 `PatentRuntime`。
2. `PatentRuntime.resources` 只能关闭 runtime 自己拥有的资源；app-owned shared provider 不能被重复注册进去。
3. `PatentExecutor` 现有 `pdf_service=` 注入点继续复用，不单独重构执行器接口。
4. `PatentPlanningClient`、`PatentAnswerBuilder`、`PatentPdfAnswerClient` 都要支持 `http_client=` 注入；若显式传入 `transport=`，则创建私有 client，不和 shared client 混用。
5. 跨 `pdf_qa -> 普通 QA` 的热连接复用，自动化先锁住 wiring 与 ownership，真实复用效果通过集成日志验证，不把它伪装成当前单元测试已经直接证明的事实。
6. provider 初始化失败时，`create_app()` 仍需成功，且 app-owned `PatentPdfService` 必须明确退回私有 client 路径，不能因为 shared pool 启用而把 `pdf_qa` 一起打挂。

### Task 1: 新增 shared upstream provider，并让普通 QA wrappers 支持注入 shared client

**Files:**
- Create: `patent/server/patent/upstream_http.py`
- Create: `patent/tests/test_patent_upstream_http.py`
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/server/patent/answering.py`
- Modify: `patent/tests/test_runtime_controls.py`
- Modify: `patent/tests/test_patent_retrieval_service.py`
- Modify: `patent/tests/test_patent_stage1_planning.py`
- Modify: `patent/tests/test_patent_stage4_synthesis.py`

**Testing Requirement:**
- 锁死 shared provider 的配置解析、client 复用、以及 `PatentAnswerBuilder` 的 injected-client ownership 语义。
- 额外锁死 `PatentPlanningClient` 的 injected-client / request-level timeout 语义，不能只靠 runtime 间接覆盖。
- 额外锁死 `build_default_patent_runtime(...)` 在 runtime bootstrap 相关测试里的兼容性，不回归现有 `test_patent_retrieval_service.py` 调用面。
- 必跑命令：
  - `bash patent/scripts/test.sh tests/test_patent_upstream_http.py tests/test_patent_stage1_planning.py tests/test_patent_stage4_synthesis.py tests/test_runtime_controls.py tests/test_patent_retrieval_service.py -q`
- 如 pytest 受沙箱限制，按用户要求提权执行同一命令。

- [ ] **Step 1: 写 shared provider 与 answer builder ownership 的红灯测试**

覆盖：
- shared pool 开启时，同一个 provider 多次取 client 返回同一个 `httpx.Client`
- `keepalive_expiry/max_keepalive_connections/max_connections` 按 env 解析
- `PatentAnswerBuilder(http_client=shared_client)` 的 `close()` 不会关闭 shared client
- `PatentPlanningClient(http_client=shared_client)` 会复用注入 client，并在请求级透传 timeout
- `PatentPlanningClient` 消费注入 shared client 时，`close()` 不会关闭共享 client
- `PatentAnswerBuilder(transport=...)` 仍走私有 client
- `transport` 与 `http_client` 混用时明确失败
- `build_default_patent_runtime(...)` 可以消费外部注入的 shared client，而不是自己新建一套
- `test_patent_retrieval_service.py` 现有 `build_default_patent_runtime()` 调用在不传 shared client 时仍保持兼容

- [ ] **Step 2: 跑 Task 1 红灯测试**

Run:
```bash
bash patent/scripts/test.sh tests/test_patent_upstream_http.py tests/test_patent_stage1_planning.py tests/test_patent_stage4_synthesis.py tests/test_runtime_controls.py tests/test_patent_retrieval_service.py -q
```

Expected:
- FAIL
- 失败点集中在缺少 `upstream_http.py`、`PatentAnswerBuilder` 不支持 `http_client=`、以及 runtime 还不能消费外部 shared client

- [ ] **Step 3: 最小实现 shared provider 与普通 QA wrapper 注入**

实现要求：
- 在 `patent/server/patent/upstream_http.py` 增加 app 可复用的 provider，至少暴露：

```python
class PatentSharedUpstreamHttpProvider:
    def __init__(self, *, enabled: bool, keepalive_expiry_seconds: float, max_keepalive_connections: int, max_connections: int):
        ...

    @classmethod
    def from_env(cls) -> "PatentSharedUpstreamHttpProvider":
        ...

    def client(self) -> httpx.Client | None:
        ...

    def close(self) -> None:
        ...
```

- `PatentAnswerBuilder` 构造函数增加：

```python
def __init__(..., transport: httpx.BaseTransport | None = None, http_client: httpx.Client | None = None):
    ...
```

- `PatentAnswerBuilder` 的请求调用改成 request-level `timeout=self.timeout_seconds`
- `PatentPlanningClient` 同样支持注入 `http_client`，请求调用改成 request-level timeout
- `build_default_patent_runtime(...)` 接受外部 shared client 或 provider 参数，并只把 runtime 自己拥有的资源放进 `resources`
- 在 provider / wrapper 初始化和关键请求开始时补上可用于集成验证的 shared/private client 日志字段，至少能区分：
  - wrapper 名称
  - `base_url`
  - `timeout`
  - `shared_client_id` 或等价标识
  - `client_owner=shared|private`

- [ ] **Step 4: 重跑 Task 1 目标测试**

Run:
```bash
bash patent/scripts/test.sh tests/test_patent_upstream_http.py tests/test_patent_stage1_planning.py tests/test_patent_stage4_synthesis.py tests/test_runtime_controls.py tests/test_patent_retrieval_service.py -q
```

Expected:
- PASS
- shared provider 配置、runtime wiring、answer builder ownership 全部被锁住

- [ ] **Step 5: 发起 Task 1 review，修到 pass**

要求：
- 把 Task 1 的文件列表、测试结果、以及 `PatentRuntime.resources` ownership 设计发给 reviewer
- 如果 reviewer 质疑 runtime ownership、timeout 下沉或 transport 混用语义，必须先修并重跑本 task 测试
- reviewer pass 前不得进入 Task 2

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/upstream_http.py patent/server/patent/runtime.py patent/server/patent/answering.py patent/tests/test_patent_upstream_http.py patent/tests/test_runtime_controls.py patent/tests/test_patent_retrieval_service.py patent/tests/test_patent_stage1_planning.py patent/tests/test_patent_stage4_synthesis.py
git commit -m "feat(patent): add process-local shared upstream provider for qa runtime"
```

### Task 2: 让 PDF client/service 接入 shared client，并锁住 private fallback ownership

**Files:**
- Modify: `patent/server/patent/pdf_service.py`
- Modify: `patent/tests/test_patent_pdf_contract.py`
- Modify: `patent/tests/test_patent_executor.py`
- Modify: `patent/tests/test_patent_file_routes.py`

**Testing Requirement:**
- 锁死 `PatentPdfAnswerClient` 和 `PatentPdfService` 在 shared-client / private-client 两种路径下的关闭语义，并保证执行器现有注入点继续可用。
- 额外锁死 file-route 层直接消费 `PatentPdfService` 的行为，不让 route-level 用例在 service 注入后静默回归。
- 必跑命令：
  - `bash patent/scripts/test.sh tests/test_patent_pdf_contract.py tests/test_patent_executor.py tests/test_patent_file_routes.py -q`
- 如受沙箱限制，提权跑同一命令。

- [ ] **Step 1: 写 PDF shared-client 与 fallback ownership 红灯测试**

覆盖：
- `PatentPdfAnswerClient(http_client=shared_client)` 的 `close()` 不会关闭共享 client
- `PatentPdfAnswerClient.from_env(http_client=...)` 或等价构造路径可以消费 shared client
- `PatentPdfService(answer_client=...)` 或等价注入路径优先使用外部构造好的 answer client
- shared pool 关闭或 provider 不可用时，`PatentPdfService()` 仍退回私有 client
- `PatentExecutor(pdf_service=...)` 现有注入行为不变
- `test_patent_file_routes.py` 中直接实例化 `PatentPdfService(...)` 的现有用例仍保持兼容

- [ ] **Step 2: 跑 Task 2 红灯测试**

Run:
```bash
bash patent/scripts/test.sh tests/test_patent_pdf_contract.py tests/test_patent_executor.py tests/test_patent_file_routes.py -q
```

Expected:
- FAIL
- 失败点集中在 `PatentPdfAnswerClient` 还不支持 injected client / close ownership，或 `PatentPdfService` 还不能稳定消费已构造的 answer client

- [ ] **Step 3: 最小实现 PDF wrapper 与 service 注入**

实现要求：
- `PatentPdfAnswerClient` 增加 `http_client=`，并在请求级显式传 `timeout=self._timeout_seconds`
- 如果 `PatentPdfAnswerClient` 自己创建 client，则 `close()` 关闭；如果消费外部 shared client，则 `close()` 为 no-op
- `PatentPdfService` 增加明确注入位，例如：

```python
class PatentPdfService:
    def __init__(..., answer_client: PatentPdfAnswerClient | None = None, ...):
        self._client = answer_client or PatentPdfAnswerClient.from_env()
```

- 保持现有 `answer_question_fn` 测试替身路径不回归
- 不改 `PatentExecutor` 接口，只复用现有 `pdf_service=` 注入点

- [ ] **Step 4: 重跑 Task 2 目标测试**

Run:
```bash
bash patent/scripts/test.sh tests/test_patent_pdf_contract.py tests/test_patent_executor.py tests/test_patent_file_routes.py -q
```

Expected:
- PASS
- PDF shared/private ownership 与 executor 注入兼容性被锁住

- [ ] **Step 5: 发起 Task 2 review，修到 pass**

要求：
- 把 PDF shared-client 语义、fallback 保底路径、以及 executor 注入兼容性发给 reviewer
- reviewer 如果指出 `answer_question_fn`、file-route fallback 或关闭语义回归，必须修正并重跑本 task 测试

- [ ] **Step 6: Commit**

```bash
git add patent/server/patent/pdf_service.py patent/tests/test_patent_pdf_contract.py patent/tests/test_patent_executor.py patent/tests/test_patent_file_routes.py
git commit -m "feat(patent): wire pdf qa client to shared upstream pool"
```

### Task 3: 在 app bootstrap 中统一 wiring/cleanup，并锁死 degraded-mode 与 shutdown 契约

**Files:**
- Modify: `patent/server_fastapi/app.py`
- Modify: `patent/server/patent/runtime.py`
- Modify: `patent/config.shared.env.example`
- Modify: `patent/tests/fastapi_contract/test_health_contract.py`
- Modify: `patent/tests/fastapi_contract/test_ask_contract.py`
- Modify: `patent/tests/test_runtime_controls.py`
- Modify: `patent/tests/test_patent_retrieval_service.py`

**Testing Requirement:**
- 必须锁死 app bootstrap 对 shared provider 与 app-owned `PatentPdfService` 的创建和关闭语义，并验证 `runtime=None` 时纯文件路由仍可用。
- 额外锁死 shared provider 初始化失败时的 app-level fallback：`create_app()` 仍成功，且 app-owned `PatentPdfService` 回退到私有 client。
- 必跑命令：
  - `bash patent/scripts/test.sh tests/fastapi_contract/test_health_contract.py tests/fastapi_contract/test_ask_contract.py tests/test_runtime_controls.py tests/test_patent_retrieval_service.py -q`
- 如需要启动 FastAPI contract 依赖或 pytest 写入超出沙箱，提权执行。

- [ ] **Step 1: 写 app-owned wiring / cleanup / degraded-mode 红灯测试**

覆盖：
- `create_app()` 会把 shared provider 挂到 `app.state`
- app bootstrap 成功时，`ask_service._patent_executor._pdf_service` 使用 app-owned `PatentPdfService`
- `runtime=None` 时，`pdf_qa` / file-only routes 仍可运行
- shared provider 初始化抛错时，`create_app()` 不失败，而是记录降级并让 app-owned `PatentPdfService` 退回私有 client
- shutdown 与 bootstrap fail 都会关闭：
  - app-owned shared provider
  - app-owned `PatentPdfService`
- shared pool 初始默认关闭，env example 暴露新变量：
  - `PATENT_LLM_HTTP_SHARED_POOL_ENABLED=false`
  - `PATENT_LLM_HTTP_KEEPALIVE_EXPIRY_SECONDS=120`
  - `PATENT_LLM_HTTP_MAX_KEEPALIVE_CONNECTIONS=20`
  - `PATENT_LLM_HTTP_MAX_CONNECTIONS=100`

- [ ] **Step 2: 跑 Task 3 红灯测试**

Run:
```bash
bash patent/scripts/test.sh tests/fastapi_contract/test_health_contract.py tests/fastapi_contract/test_ask_contract.py tests/test_runtime_controls.py tests/test_patent_retrieval_service.py -q
```

Expected:
- FAIL
- 失败点集中在 app 还没创建/关闭 shared provider，或 `runtime=None` degraded-mode / bootstrap-fail cleanup 仍未覆盖 shared pool 资源

- [ ] **Step 3: 最小实现 app bootstrap 与 cleanup wiring**

实现要求：
- 在 `server_fastapi/app.py` bootstrap 中：

```python
try:
    shared_provider = PatentSharedUpstreamHttpProvider.from_env()
    shared_client = shared_provider.client()
except Exception:
    shared_provider = None
    shared_client = None

pdf_answer_client = PatentPdfAnswerClient.from_env(http_client=shared_client) if shared_client is not None else PatentPdfAnswerClient.from_env()
pdf_service = PatentPdfService(answer_client=pdf_answer_client)
runtime = build_default_patent_runtime(execution_cache=execution_cache, http_client=shared_client)
```

- 具体命名可以调整，但必须满足：
  - shared provider 是 app-owned 资源
  - app-owned `pdf_service` 是显式资源
  - `patent_runtime` 只消费 shared client，不拥有 shared provider 生命周期
  - shared provider 初始化失败时，app 继续启动，并明确走 private PDF fallback
- `_bootstrap_service_state()` 内部对本地新建的 `shared_provider` / `pdf_service` / `runtime` 继续保留局部 `try/except` 清理，避免资源在挂到 `app.state` 之前泄漏
- 在 `_bootstrap_app_state()` 的失败清理和 lifespan shutdown 中补上：
  - `patent_pdf_service`
  - `patent_shared_upstream_provider`
- `config.shared.env.example` 补充新 env，并保持默认关闭

- [ ] **Step 4: 重跑 Task 3 目标测试**

Run:
```bash
bash patent/scripts/test.sh tests/fastapi_contract/test_health_contract.py tests/fastapi_contract/test_ask_contract.py tests/test_runtime_controls.py tests/test_patent_retrieval_service.py -q
```

Expected:
- PASS
- app wiring、shutdown cleanup、runtime none degraded-mode、env defaults 全部被锁住

- [ ] **Step 5: 发起 Task 3 review，修到 pass**

要求：
- 把 app-owned ownership、shutdown 清理、`runtime=None` 文件路由回归保护、以及 env 默认值发给 reviewer
- reviewer 如指出 bootstrap fail cleanup、重复 close 或资源双归属，先修后重跑本 task 测试

- [ ] **Step 6: Commit**

```bash
git add patent/server_fastapi/app.py patent/server/patent/runtime.py patent/config.shared.env.example patent/tests/fastapi_contract/test_health_contract.py patent/tests/fastapi_contract/test_ask_contract.py patent/tests/test_runtime_controls.py patent/tests/test_patent_retrieval_service.py
git commit -m "feat(patent): bootstrap shared upstream pool in app lifecycle"
```

## Final Verification

所有 tasks 完成后，必须再做一次最小回归验证：

- [ ] **Step 1: 跑全套本期相关 pytest**

Run:
```bash
bash patent/scripts/test.sh tests/test_patent_upstream_http.py tests/test_patent_stage1_planning.py tests/test_patent_stage4_synthesis.py tests/test_patent_pdf_contract.py tests/test_patent_executor.py tests/test_patent_file_routes.py tests/test_runtime_controls.py tests/test_patent_retrieval_service.py tests/fastapi_contract/test_health_contract.py tests/fastapi_contract/test_ask_contract.py -q
```

Expected:
- PASS
- 无 shared-provider ownership 回归
- 无 `runtime=None` 文件路由回归

- [ ] **Step 2: 做一轮手工集成验证记录**

最少记录：
- `PATENT_LLM_HTTP_SHARED_POOL_ENABLED=false` 时，`pdf_qa` 仍可正常回答
- shared provider 初始化失败时，`create_app()` 仍成功且 `pdf_qa` 仍可正常回答
- `PATENT_LLM_HTTP_SHARED_POOL_ENABLED=true` 时，同一 worker 内三类 wrapper 初始化日志显示同一个 shared client 标识
- `pdf_qa -> 普通 QA` 连续请求时，日志中能看到 shared client 复用而不是三套独立 client

- [ ] **Step 3: 发起最终 review**

要求：
- reviewer 看到最终文件清单、最终测试结果、以及手工集成验证结论
- 如果 reviewer 仍有重要问题，回到对应 task 修复并补测

- [ ] **Step 4: 最终 commit（如果前面按 task 已分步提交，这一步只在需要收尾文件时执行）**

```bash
git status
```

Expected:
- 仅剩本期允许的未提交改动；如果没有额外收尾文件，可跳过新 commit
