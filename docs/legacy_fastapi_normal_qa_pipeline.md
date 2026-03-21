# 旧版普通问答五阶段实现说明

本文只说明旧版 `fastapi-version` 的普通问答链路，即前端调用 `/api/v1/ask_stream` 后，后端被路由到 `kb_qa` 时的处理过程。不包含文件问答、PDF 问答、表格问答、混合问答。

文档写法遵循三层结构：

1. 调用链说明
2. 源码摘录
3. 行为解释

这样后面做 `fastQA` 对齐时，可以直接拿这份文档对照具体实现，不需要再来回翻旧版源码。

## 1. 总入口与调用关系

旧版普通问答主入口：

- 前端请求：`/home/cqy/worktrees/fastapi-version/frontend-vue/src/services/api.js`
- 后端路由：`/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/api.py`
- 后端编排：`/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/service.py`
- 普通问答服务：`/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_kb/service.py`
- 五阶段 orchestrator：`/home/cqy/worktrees/fastapi-version/backend/app/modules/qa_kb/orchestrators/generation.py`
- 真实阶段实现：`/home/cqy/worktrees/fastapi-version/backend/app/modules/generation_pipeline/`

普通问答实际调用链：

1. 前端 `askStream()` 发 `POST /api/v1/ask_stream`
2. `ask_gateway/api.py` 做鉴权、配额、并发槽位控制
3. `ask_gateway/service.py` 规范化请求，并默认路由到 `kb_qa`
4. `_dispatch_kb()` 构造 `QaKbRequest`
5. `qa_kb_service.iter_answer_events()` 读取 `QA_QUERY_PIPELINE_MODE`
6. 默认配置为 `new`，因此进入生成驱动五阶段链路
7. orchestrator 按阶段发 SSE：`step/metadata/content/done`
8. `AskStreamTap` 汇总完整 assistant 内容、steps、references、timings，最后持久化 assistant 消息

默认配置见：`/home/cqy/worktrees/fastapi-version/config.shared.env`

- `QA_QUERY_PIPELINE_MODE=new`
- `QA_STAGE4_MIN_CITATIONS=10`
- `QA_STAGE25_MD_*`
- `QA_STAGE3_SKIP_PDF_*`

这意味着旧版普通问答默认不是老 semantic 链，而是新的生成驱动链。

## 2. ask_gateway 入口编排

### 2.1 路由入口源码

文件：`backend/app/modules/ask_gateway/api.py`

```python
@router.post("/api/v1/ask")
@router.post("/api/v1/ask_stream")
@router.post("/ask")
@router.post("/ask_stream")
def ask_question_stream(
    payload: AskRequest,
    request: Request,
    runtime: AppRuntime = Depends(get_runtime),
    auth: AuthContext = Depends(require_auth_context),
    _quota: QuotaGrant | None = Depends(require_quota("ask_query")),
):
    if not ask_gateway_service.acquire_slot(runtime=runtime):
        error_payload, status_code = ask_gateway_service.busy_payload(runtime=runtime)
        return JSONResponse(status_code=status_code, content=error_payload)

    payload_dict: dict[str, Any] = payload.model_dump()
    context = _ctx(auth)

    try:
        ask_gateway_service.enrich_request(payload=payload_dict, context=context, runtime=runtime)
        ask_gateway_service.persist_user_request(payload=payload_dict, context=context, runtime=runtime)
        source, cancel_event = ask_gateway_service.stream_events(payload=payload_dict, context=context, runtime=runtime)
    except Exception:
        ask_gateway_service.release_slot()
        raise

    response = sse_response(
        request=request,
        source=source,
        heartbeat_sec=runtime.settings.sse_heartbeat_sec,
        on_disconnect=cancel_event.set,
    )
    finalize_quota(_quota, result=response)
    return response
```

### 2.2 行为解释

这段入口代码很关键，它说明旧版问答不是“前端直接调某个 QA 服务”，而是统一经过 `ask_gateway` 编排层。

这个入口一共做了 5 件事：

1. 鉴权
2. 配额检查
3. 并发槽位控制
4. 请求增强和用户消息持久化
5. 创建 SSE 响应

这里最重要的两个调用是：

- `enrich_request(...)`
- `stream_events(...)`

普通问答最终是否走 `kb_qa`，是在这个编排层和 `qa_kb_service` 共同决定的。

这个入口还有一个容易漏掉的边界行为：

- 如果并发槽位拿不到，不会进入问答链
- 而是直接返回 `429`

对应限流器实现是：

```python
class AskConcurrencyLimiter:
    def __init__(self, *, max_concurrent: int) -> None:
        self._limit = max(1, int(max_concurrent))
        self._sem = BoundedSemaphore(self._limit)
        self._active = 0

    def try_acquire(self) -> bool:
        acquired = self._sem.acquire(blocking=False)
        if not acquired:
            return False
        self._active += 1
        return True
```

因此旧版普通问答除了 stage2 自己的动态并发外，在 ask 入口层还有一层“总流式问答并发上限”。

## 2.3 前端请求体与后端 schema 契约

这一层在旧版实现里也是固定的，后面迁移时不能随便改字段。

前端发送流式问答请求的代码在：

- `/home/cqy/worktrees/fastapi-version/frontend-vue/src/services/api.js`

核心逻辑是：

```javascript
async *askStream(question, chatHistory = [], conversationId = null, pdfContext = null, signal = undefined) {
  const body = {
    question,
    chat_history: chatHistory.slice(-10),
  };
  if (conversationId) body.conversation_id = conversationId;
  if (pdfContext) body.pdf_context = pdfContext;

  const response = await fetchWithErrorHandling(`${API_BASE}${V1}/ask_stream`, {
    method: 'POST',
    headers: authHeaders(true),
    body: JSON.stringify(body),
    signal,
  });
  ...
}
```

对应后端 schema 在：

- `/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/schemas.py`

```python
class AskRequest(BaseModel):
    question: str = Field(default="")
    chat_history: list[dict[str, Any]] = Field(default_factory=list)
    use_pdf: bool = Field(default=False)
    pdf_path: str | None = Field(default=None)
    trace_id: str | None = Field(default=None)
    conversation_id: int | None = Field(default=None)
    pdf_context: dict[str, Any] = Field(default_factory=dict)
    use_generation_driven: bool = Field(default=False)
    route_hint: str | None = Field(default=None)
    n_results_per_claim: int = Field(default=10, ge=1, le=50)
```

这说明旧版普通问答在 HTTP 契约上有几个固定事实：

- 前端默认只传 `question/chat_history/conversation_id/pdf_context`
- 前端只保留最近 10 轮历史：`chat_history.slice(-10)`
- `use_generation_driven` 默认是 `false`
- `n_results_per_claim` 默认是 `10`，并且被限制在 `1~50`
- 即使普通问答不走文件链，`pdf_context` 字段仍然是统一请求体的一部分

所以后面迁移时，不能把 ask 接口简化成只接一个 `question`，否则会丢旧版契约。

## 3. ask_gateway 如何把请求送进普通问答

### 3.1 `_dispatch_kb()` 源码

文件：`backend/app/modules/ask_gateway/service.py`

```python
def _dispatch_kb(
    self,
    *,
    payload: dict[str, Any],
    runtime: AppRuntime,
    cancel_event: Event,
) -> Iterator[dict[str, Any]]:
    request = QaKbRequest(
        question=str(payload.get("question") or ""),
        request_use_generation_driven=bool(payload.get("use_generation_driven")),
        route_hint=str(payload.get("route_hint") or "kb_qa"),
        n_results_per_claim=int(payload.get("n_results_per_claim") or 10),
        active_stream_count=self.active_stream_count(runtime),
        trace_id=str(payload.get("trace_id") or ""),
    )

    if runtime.agent is None and qa_kb_service.resolve_pipeline_mode(
        request_use_generation_driven=request.request_use_generation_driven,
        env_get=os.getenv,
        logger=self._logger,
    ).use_generation_driven is False:
        yield {
            "type": "error",
            "error": "legacy_agent_unavailable",
            "message": "Legacy KB pipeline requires an initialized runtime agent",
            "trace_id": request.trace_id,
        }
        return

    yield self._enrich_stream_event({"type": "step", "step": "dispatch", "route": "kb_qa", "trace_id": request.trace_id}, payload=payload, default_route="kb_qa")
    yield self._enrich_stream_event({"type": "thinking", "content": "🧠 正在进入知识库问答主链路", "trace_id": request.trace_id}, payload=payload, default_route="kb_qa")

    generation_runtime = getattr(runtime, "generation_runtime", None)
    redis_service = self._get_redis_service(runtime)
    for item in qa_kb_service.iter_answer_events(
        request=request,
        sse_event=lambda event: event,
        generation_runtime=generation_runtime,
        redis_service=redis_service,
        should_cancel=cancel_event.is_set,
        legacy_kwargs=legacy_kwargs,
        env_get=os.getenv,
        logger=self._logger,
    ):
        payload_dict = self._enrich_stream_event(
            dict(item or {}),
            payload=payload,
            default_route="kb_qa",
        )
        yield payload_dict
```

### 3.2 行为解释

普通问答真正进入 `qa_kb_service` 的入口就在这里。

这段代码说明了 4 个事实：

1. `n_results_per_claim` 默认就是 10
2. `active_stream_count` 会传给下游阶段 2，用于动态并发策略
3. 是否走新旧链，不在 `ask_gateway` 决定，而在 `qa_kb_service.resolve_pipeline_mode()` 决定
4. `ask_gateway` 本身不做问答逻辑，只负责把事件流再补齐成前端需要的统一 SSE 结构

这里还暴露出一个旧版特征：

- 如果是 legacy 链且 `runtime.agent` 没初始化，会直接报 `legacy_agent_unavailable`
- 但如果是新链，则依赖的是 `generation_runtime`

这也进一步说明：旧版普通问答默认已经是双实现并存，但主实现是 `new`。

### 3.3 `enrich_request()` 对普通问答的默认值处理

普通问答虽然不走文件链，但请求进入 `ask_gateway` 后，仍会先被统一规范化。

关键代码是：

```python
payload["question"] = str(payload.get("question") or "").strip()
payload["chat_history"] = payload.get("chat_history") if isinstance(payload.get("chat_history"), list) else []
payload["pdf_context"] = payload.get("pdf_context") if isinstance(payload.get("pdf_context"), dict) else {}
payload["trace_id"] = str(payload.get("trace_id") or uuid.uuid4().hex).strip()
payload["route_hint"] = str(payload.get("route_hint") or "kb_qa").strip() or "kb_qa"
payload["conversation_id"] = self._coerce_int(payload.get("conversation_id"))
payload.setdefault("used_files", [])
payload.setdefault("execution_files", [])
payload.setdefault("allow_kb_verification", False)
payload.setdefault("turn_mode", "kb_only")
```

这说明旧版普通问答即使没有文件上下文，也依赖这些默认值：

- `question` 会先 `strip()`
- 非法 `chat_history` 会被重置为空数组
- 非法 `pdf_context` 会被重置为空对象
- 如果没传 `trace_id`，后端会自动生成
- `route_hint` 默认是 `kb_qa`
- `turn_mode` 默认是 `kb_only`

因此后面要做严格对齐，不能只对齐阶段逻辑，还要对齐入口层默认值。

## 4. `qa_kb_service` 如何决定走新链还是旧链

### 4.1 `iter_answer_events()` 源码

文件：`backend/app/modules/qa_kb/service.py`

```python
def resolve_pipeline_mode(
    self,
    *,
    request_use_generation_driven: bool,
    env_get: Callable[[str, str], str] | None = None,
    logger: Any | None = None,
) -> QaKbPipelineMode:
    lookup = env_get or os.getenv
    log = logger or self._logger
    raw_mode = str(lookup("QA_QUERY_PIPELINE_MODE", "new") or "new").strip().lower()
    aliases = {
        "new": "new",
        "generation": "new",
        "generation_driven": "new",
        "legacy": "legacy",
        "old": "legacy",
        "semantic": "legacy",
        "request": "request",
        "client": "request",
    }
    mode = aliases.get(raw_mode)
    if mode is None:
        log.warning("Unknown QA_QUERY_PIPELINE_MODE=%r, falling back to new", raw_mode)
        mode = "new"
    if mode == "request":
        return QaKbPipelineMode(mode=mode, use_generation_driven=bool(request_use_generation_driven))
    return QaKbPipelineMode(mode=mode, use_generation_driven=(mode == "new"))

def iter_answer_events(
    self,
    *,
    request: QaKbRequest,
    sse_event: Callable[[dict[str, Any]], Any],
    generation_runtime: GenerationRuntime | None = None,
    redis_service: RedisService | None = None,
    should_cancel: Callable[[], bool] | None = None,
    legacy_kwargs: dict[str, Any] | None = None,
    legacy_dependencies: QaKbLegacyDependencies | None = None,
    env_get: Callable[[str, str], str] | None = None,
    logger: Any | None = None,
) -> Iterator[Any]:
    log = logger or self._logger
    resolved_mode = self.resolve_pipeline_mode(
        request_use_generation_driven=request.request_use_generation_driven,
        env_get=env_get,
        logger=log,
    )
    if resolved_mode.use_generation_driven and request.route_hint not in {"tabular_qa", "hybrid_qa"}:
        yield from self.iter_generation_answer_events(
            question=request.question,
            generation_runtime=generation_runtime,
            redis_service=redis_service,
            sse_event=sse_event,
            n_results_per_claim=request.n_results_per_claim,
            should_cancel=should_cancel,
            active_stream_count=request.active_stream_count,
            logger=log,
        )
        return
    ...
    yield from self.iter_legacy_answer_events(**payload)
```

### 4.2 行为解释

这里是旧版普通问答最核心的分叉点。

它不是只有一条实现，而是有三种模式：

- `new`
- `legacy`
- `request`

含义分别是：

- `new`：强制走生成驱动五阶段链路
- `legacy`：强制走旧 semantic/precise 链
- `request`：由请求参数 `use_generation_driven` 决定

但旧版默认配置是：

```env
QA_QUERY_PIPELINE_MODE=new
```

所以正常线上行为就是：

- 普通问答默认走 `iter_generation_answer_events()`
- 即 `GenerationPipelineOrchestrator.stream()`

这也是为什么后面迁移时，不能只迁“阶段 4 输出”，而必须把整个 `iter_answer_events()` 的分流逻辑一起迁过去。

### 4.3 `model_identity_shortcut` 特殊分支

除了 `new/legacy/request` 分流，旧版普通问答还有一个特殊短路分支。

源码在：

```python
def _identity_shortcut_result(self, question: str) -> QaKbExecutionResult | None:
    qlow = str(question or "").lower()
    model_queries = [
        "什么模型",
        "是什么模型",
        "which model",
        "what model",
        "你是谁",
        "who are you",
        "who created",
        "是谁",
        "哪个模型",
    ]
    if not any(keyword in qlow for keyword in model_queries):
        return None
    return QaKbExecutionResult(
        success=True,
        final_answer="您好，我是运行在claude-4.5-sonnet-thinking模型上的AI助手...",
        metadata=QaKbExecutionMetadata(
            route="kb_qa",
            pipeline_mode="new",
            query_mode="model_identity_shortcut",
            use_generation_driven=True,
        ),
        raw={"shortcut": "model_identity"},
    )
```

这个分支的含义是：

- 某些“你是什么模型/你是谁”类问题，不进入五阶段主链
- 直接返回固定答案
- 仍然走统一的 `QaKbExecutionResult -> SSE` 包装

所以如果后面要做严格功能对齐，这个 shortcut 也属于旧版普通问答的一部分，不能漏掉。

## 5. orchestrator 视角下的阶段顺序

`GenerationPipelineOrchestrator.stream()` 的顺序固定：

1. `stage1`: 生成深度预回答与检索规划
2. `stage2`: 按 claim 做精准检索
3. `stage25`: 尝试从 MD 向量库补证据
4. `stage3`: 依据 DOI 加载 PDF chunk；若 MD 命中满足阈值可跳过
5. `stage4`: 基于 `deep_answer + pdf/md chunk + top references` 流式生成最终答案

这条链路的设计目的不是“先检索后回答”，而是：

- 先让 LLM 产出一个结构化、较深的 `deep_answer`
- 再把 `deep_answer` 拆成多个“可检索的验证主张”
- 再针对这些主张检索证据
- 再用证据回填、约束、修正最终答案

所以 `deep_answer` 是后续所有阶段的骨架，不是最终答案。

补充一个边界条件：

- 如果命中 `model_identity_shortcut`，这五阶段链不会执行
- 它会直接走 `iter_result_events(...)` 输出 `metadata/content/done`

另一个边界条件在 `ask_gateway._default_event_source()`：

```python
if not str(payload.get("question") or "").strip():
    yield {"type": "error", "error": "问题不能为空", "trace_id": str(payload.get("trace_id") or "")}
    return
```

所以空问题不会进入五阶段，而是直接返回 `error` 事件。

## 6. 阶段 1：深度预回答 + 检索规划

核心文件：

- facade 调用：`generation_driven_rag_facade.py::stage1_pre_answer_and_planning`
- 真正实现：`generation_pipeline/stage1_planning.py`
- prompt：`generation_pipeline/prompt_templates.py::STAGE1_PROMPT`
- 缓存：`qa_cache/stage1_cache.py`

### 6.1 阶段 1 源码

```python
def run_stage1_pre_answer_and_planning(
    *,
    user_question: str,
    stage1_prompt: str,
    vector_db_context: str,
    client: Any,
    model: str,
    logger: Any,
) -> Dict[str, Any]:
    full_system_prompt = stage1_prompt + vector_db_context
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": full_system_prompt
                + "\\n\\n你必须严格按照上文给出的 JSON 模板输出，"
                "返回值只能是一个 JSON 对象，不能包含任何解释性文字或前后缀说明。",
            },
            {"role": "user", "content": f"用户问题：{user_question}"},
        ],
        temperature=0.5,
        max_tokens=3000,
        response_format={"type": "json_object"},
    )

    result_text = response.choices[0].message.content.strip()
    cleaned_text = result_text
    if "```json" in cleaned_text:
        cleaned_text = cleaned_text.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned_text:
        cleaned_text = cleaned_text.split("```", 1)[1].split("```", 1)[0].strip()

    try:
        stage1_result = json.loads(cleaned_text)
    except json.JSONDecodeError:
        try:
            stage1_result = json.loads(result_text)
            cleaned_text = result_text
        except json.JSONDecodeError as e_inner:
            return {
                "success": True,
                "deep_answer": result_text,
                "retrieval_claims": [],
                "raw_response": result_text,
                "fallback": "json_parse_failed",
            }

    deep_answer = stage1_result.get("deep_answer", "")
    raw_claims = stage1_result.get("retrieval_claims", []) or []

    retrieval_claims = []
    for rc in raw_claims:
        if isinstance(rc, dict):
            retrieval_claims.append(
                {
                    "claim": (rc.get("claim") or "").strip(),
                    "keywords": rc.get("keywords") or [],
                    "preferred_sections": rc.get("preferred_sections") or rc.get("preferred") or [],
                    "filters": rc.get("filters") or {},
                }
            )
        else:
            retrieval_claims.append(
                {
                    "claim": str(rc).strip(),
                    "keywords": [],
                    "preferred_sections": [],
                    "filters": {},
                }
            )

    return {
        "success": True,
        "deep_answer": deep_answer,
        "retrieval_claims": retrieval_claims,
        "raw_response": cleaned_text,
    }
```

### 6.2 输入

阶段 1 的唯一业务输入是用户问题 `user_question`。

同时还会拼接两类运行时上下文：

- `stage1_prompt`
- `vector_db_context`

`vector_db_context` 由 `_get_vector_db_context_for_prompt()` 生成，本质上是把向量库主题索引信息拼进 prompt，帮助模型知道数据库更擅长哪些方向、该生成什么样的 retrieval claims。

### 6.3 阶段 1 的真实职责

阶段 1 不是“先给个草稿答案”这么简单，它同时负责：

1. 生成 `deep_answer`
2. 把答案拆成 `retrieval_claims`
3. 规范化 claims 结构，供阶段 2 直接消费

所以阶段 1 是全链路的骨架生成器。

### 6.4 Prompt 约束重点

`STAGE1_PROMPT` 非常重，作用不是简单回答，而是同时完成两件事：

1. 生成结构化 Markdown 预回答
2. 从预回答中抽取 3-5 个与用户核心意图直接相关的、可检索的主张

关键约束：

- 深度预回答必须是 Markdown
- 不能输出 LaTeX
- 不能单独列 DOI 列表
- 检索主张必须直接服务用户问题核心意图，不能被预回答带偏
- `keywords` 必须尽量保留原问题核心术语及同义词扩展
- `filters` 仅支持 `must_contains`

### 6.5 阶段 1 的降级策略

阶段 1 有两层降级：

1. JSON 解析失败：
   - 不报错中断
   - 直接把原始模型输出当作 `deep_answer`
   - `retrieval_claims=[]`
   - 返回 `success=True`
2. 模型调用或其他异常：
   - 返回 `success=False`
   - orchestrator 直接发 `error`

这意味着：

- “模型输出不是标准 JSON”不会直接让整条链失败
- 只会退化成“仅预回答”

### 6.6 阶段 1 缓存

orchestrator 在 `_run_stage1()` 中先读缓存，再决定是否实际调用模型。

阶段 1 cache key 源码如下：

```python
def build_stage1_cache_key(...):
    return redis_service.key_factory.cache(
        "qa",
        "stage1",
        _qa_cache_epoch(),
        route_hint,
        _runtime_model_name(runtime),
        _runtime_prompt_version(runtime),
        _question_hash(question),
    )
```

这里最重要的是 `stage1_prompt_version`：

- 如果显式配置了 `QA_STAGE1_PROMPT_VERSION`，直接用配置
- 否则把 `stage1_prompt + vector_db_context` 做 hash

也就是说，只要 prompt 或向量库上下文变了，阶段 1 缓存天然失效。

缓存 key 由以下因素组成：

- `QA_CACHE_EPOCH`
- route（默认 `kb_qa`）
- model 名称
- stage1 prompt 版本
- 归一化后的问题 hash

如果 Redis 可用，还会通过 `run_singleflight()` 做锁：

- 抢到锁：自己计算并写缓存
- 没抢到：等待别人把结果写入缓存
- 等不到：自己回退计算

对应源码：

```python
def run_singleflight(...):
    if redis_service is None or not redis_service.available or not _cache_lock_enabled():
        return compute_fn()

    handle = lock_manager.acquire(lock_key, ttl_seconds=_cache_lock_ttl_seconds())
    if handle is not None:
        try:
            return compute_fn()
        finally:
            lock_manager.release(handle)

    deadline = time.monotonic() + (_cache_wait_ms() / 1000.0)
    while time.monotonic() < deadline:
        cached = read_cached_fn()
        if cached is not None:
            return cached
        time.sleep(min(poll_seconds, remaining))

    return compute_fn()
```

因此阶段 1 同样问题在高并发下不会被重复打爆。

### 6.7 阶段 1 输出结构

成功时输出：

- `success`
- `deep_answer`
- `retrieval_claims`
- `raw_response`

其中真正给后续阶段用的是：

- `deep_answer`
- `retrieval_claims`

## 7. 阶段 2：按 claim 精准检索 DOI

核心文件：

- facade 调用：`generation_driven_rag_facade.py::stage2_targeted_retrieval`
- 真正实现：`generation_pipeline/stage2_retrieval.py`
- 查询预处理：`generation_pipeline/text_processing.py`
- 检索结果校验：`generation_pipeline/retrieval_validation.py`
- 查询扩展：`generation_pipeline/query_expander.py`
- 缓存：`qa_cache/stage2_cache.py`

### 7.1 阶段 2 前半段源码

```python
@dataclass
class Stage2RuntimeToggles:
    force_keyword_injection_enabled: bool
    entity_lock_enabled: bool
    use_rerank: bool
    rerank_candidates: int

def resolve_stage2_parallel_workers(
    *,
    base_workers: int,
    active_stream_count: Optional[int],
) -> tuple[int, Dict[str, Any]]:
    base = max(1, int(base_workers))
    dynamic_enabled = env_bool("QA_STAGE2_DYNAMIC_WORKERS_ENABLED", False)
    if not dynamic_enabled:
        return base, {...}
    ...
    active = max(0, int(active_stream_count or 0))
    overload_units = max(0, active - trigger_active + 1)
    reduced = base - overload_units * step
    effective = max(min_workers, reduced)
    effective = min(base, max(1, effective))
    return effective, {...}

def apply_stage2_query_constraints(
    *,
    query: str,
    user_question: str,
    claim_keywords: Iterable[Any],
    preprocess_retrieval_query_fn: Callable[[str], str],
    toggles: Stage2RuntimeToggles,
    extract_question_keywords_fn: Optional[Callable[[str], List[str]]],
) -> Tuple[str, Dict[str, Any]]:
    normalized_question = normalize_user_question_for_stage2(user_question)
    merged_prefix: List[str] = []
    details: Dict[str, Any] = {
        "injected_keywords": [],
        "injected_entities": [],
    }

    if toggles.force_keyword_injection_enabled:
        top_keywords = select_force_keywords(...)
        missing_keywords = [kw for kw in top_keywords if not _contains_keyword(query, kw)]
        if missing_keywords:
            details["injected_keywords"] = missing_keywords
            merged_prefix.extend(missing_keywords)

    if toggles.entity_lock_enabled:
        missing_entities: List[str] = []
        for canonical, aliases in extract_critical_entity_groups(normalized_question):
            if any(_contains_keyword(query, alias) for alias in aliases):
                continue
            missing_entities.append(canonical)
        if missing_entities:
            details["injected_entities"] = missing_entities
            merged_prefix.extend(missing_entities)

    constrained = query
    if merged_prefix:
        constrained = " ".join(merged_prefix) + " " + query

    constrained = preprocess_retrieval_query_fn(constrained)
    return constrained, details
```

### 7.2 阶段 2 输入

阶段 2 输入包括：

- `retrieval_claims`
- `n_results_per_claim`
- `user_question`
- `literature_expert`（向量检索专家）
- `client/model`（用于先生成检索 query）
- 可选取消函数 `should_cancel`
- 活跃流数 `active_stream_count`

注意：阶段 2 不是直接拿 claim 文本去向量库搜，它会先用 LLM 再生成一遍“更适合检索的 query”。

### 7.3 运行时开关

阶段 2 受多组配置控制：

- `QA_STAGE2_PARALLEL_WORKERS`
- `QA_STAGE2_DYNAMIC_WORKERS_ENABLED`
- `QA_STAGE2_DYNAMIC_WORKERS_TRIGGER_ACTIVE`
- `QA_STAGE2_DYNAMIC_WORKERS_MIN`
- `QA_STAGE2_DYNAMIC_WORKERS_STEP`
- `QA_STAGE2_FORCE_KEYWORD_INJECTION`
- `QA_STAGE2_ENTITY_LOCK_ENABLED`
- `QA_RETRIEVAL_RERANK_ENABLED`
- `QA_RETRIEVAL_RERANK_CANDIDATES`
- `QA_STAGE2_QUERY_EXPANSION_ENABLED`
- `QUERY_EXPANSION_MODEL`

默认逻辑：

- 并发检索开启
- 强制关键词注入开启
- 元素锁开启
- rerank 开启
- query expansion 默认关闭

### 7.4 每个 claim 的真实处理流程

对每个 claim，`_process_claim()` 的顺序是：

1. 解析 claim 结构：`claim/keywords/preferred_sections/filters`
2. 先拼装一个“提示前缀”，包括关键词、目标段落、有限支持的 filters
3. 用 LLM 生成一个更适合搜索的 query
4. 调 `preprocess_retrieval_query()` 进行标准化
5. 如果启用了 query expansion，则调用轻量模型继续扩写
6. 调 `apply_stage2_query_constraints()` 施加检索护栏
7. 调 `literature_expert.search()` 实际检索
8. 调 `validate_retrieval_relevance()` 过滤结果
9. 返回该 claim 的检索结果

### 7.5 阶段 2 主体源码

```python
def run_stage2_targeted_retrieval(
    *,
    retrieval_claims: List[Any],
    n_results_per_claim: int = 3,
    user_question: Optional[str] = None,
    client: Any,
    model: str,
    literature_expert: Any,
    preprocess_retrieval_query_fn: Callable[[str], str],
    validate_retrieval_relevance_fn: Callable[[Dict[str, Any], str, str], Dict[str, Any]],
    current_answer_context: Optional[str],
    logger: Any,
    extract_question_keywords_fn: Optional[Callable[[str], List[str]]] = None,
    expand_query_fn: Optional[Callable[[str], str]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    active_stream_count: Optional[int] = None,
    ...
) -> Dict[str, Any]:
    toggles = resolve_stage2_runtime_toggles(...)
    configured_parallel_workers = env_int("QA_STAGE2_PARALLEL_WORKERS", 5, minimum=1, maximum=16)
    parallel_workers, worker_policy = resolve_stage2_parallel_workers(
        base_workers=configured_parallel_workers,
        active_stream_count=active_stream_count,
    )
    query_expansion_enabled = env_bool("QA_STAGE2_QUERY_EXPANSION_ENABLED", False)
    normalized_user_question = normalize_user_question_for_stage2(str(user_question or ""))
    ...

    def _process_claim(i: int, claim: Any) -> Dict[str, Any]:
        ...
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "你是一个学术检索专家，擅长根据研究内容生成精准的文献检索查询。"},
                {"role": "user", "content": query_generation_prompt},
            ],
            temperature=0.3,
            max_tokens=150,
        )
        ai_generated_query = response.choices[0].message.content.strip()
        combined_query = preprocess_retrieval_query_fn(ai_generated_query)
        ...
        if query_expansion_enabled and expand_query_fn is not None:
            expanded_query = str(expand_query_fn(combined_query) or "").strip()
            if expanded_query:
                combined_query = preprocess_retrieval_query_fn(expanded_query)
        combined_query, query_guardrail_details = apply_stage2_query_constraints(...)
        search_results = _search_with_optional_rerank(
            literature_expert=literature_expert,
            combined_query=combined_query,
            n_results=max(n_results_per_claim * 3, 8),
            toggles=toggles,
        )
        if search_results and "documents" in search_results:
            search_results = validate_retrieval_relevance_fn(search_results, combined_query, claim_text)
        ...

    if len(claim_jobs) <= 1 or parallel_workers <= 1:
        ...
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            ...

    for output in sorted(claim_outputs, key=lambda item: int(item.get("index", 0))):
        if not output.get("ok"):
            continue
        claim_to_results[output["claim_key"]] = {...}
        all_documents.extend(output["documents"])
        all_metadatas.extend(output["metadatas"])
        all_distances.extend(output["distances"])

    unique_indices = []
    seen_contents = set()
    for i, doc in enumerate(all_documents):
        content_key = doc[:200] if doc else ""
        if content_key not in seen_contents:
            seen_contents.add(content_key)
            unique_indices.append(i)
```

### 7.6 LLM 生成检索 query

阶段 2 不是把 claim 原文原样送去检索，而是专门让模型再做一次 query generation。

query generation prompt 强调：

- 必须紧扣原始用户问题，而不是被预回答带偏
- 保留核心关键词和具体参数
- 对“最佳比例/最佳参数”类问题，要尽量带数值、单位、比例
- 尽量生成 40-60 字左右、适合学术检索的关键词串

如果 query generation 失败，才回退到：

- `keywords + claim_text`
- 再做统一预处理

### 7.7 query 预处理

`preprocess_retrieval_query()` 会做：

- 化学式大小写标准化，如 `lifepo4 -> LiFePO4`
- 同义词扩展，如 `PEG <-> 聚乙二醇`
- 去括号、布尔词、标点
- 保留中文术语、英文术语、化学式、温度/比例等 token
- 去重，最多保留前 15 个 token

这一步很重要，因为最后送给向量库的是“空格分隔关键词串”，不是自然语言长句。

### 7.8 query 护栏：关键词注入 + 元素锁

`apply_stage2_query_constraints()` 是阶段 2 的关键安全阀。

它做两件事：

1. 强制关键词注入
   - 从用户问题里抽核心关键词
   - 如果生成 query 里没有这些词，则补到 query 前缀里
2. 元素锁
   - 如果用户问题出现 Ti/Mg/Mn/Zn/V/F/Cu/Al 等关键元素
   - 但生成 query 没保留该元素，则强行补进去

目的：防止 LLM query generation 把问题语义带偏，导致“用户问 Ti，检索出 V 掺杂”。

### 7.9 relevance validation

`validate_retrieval_relevance()` 会给每个结果算相关性分数：

- 关键词匹配占 40%
- 向量相似度占 60%

其中：

- 关键词来自 query + claim + 一组硬编码核心词
- 向量相似度由 distance 近似反推
- 阈值是 `0.3`

如果过滤后不足 3 个结果，但原始结果至少有 3 个，则强制保留距离最近的前 3 个，避免结果太少。

### 7.10 并行模型

如果 claim 数 > 1 且 worker > 1，则进入线程池并行：

- `ThreadPoolExecutor`
- `wait(..., return_when=FIRST_COMPLETED)` 循环回收
- 每 0.2 秒轮询一次
- 如果外部取消，则取消剩余 future 并整体返回 cancelled

动态 worker 策略的目标是：

- 平时用较高并发提升检索速度
- 当系统同时有很多流式问答时，自动缩减 stage2 并发，降低 CPU/embedding/rerank 压力

### 7.11 结果聚合与去重

所有 claim 执行完成后，stage2 会：

1. 把每个 claim 的结果保存到 `claim_to_results`
2. 同时把所有 documents/metadatas/distances 汇总到全局列表
3. 以 `doc[:200]` 作为去重 key 去重

最终输出：

- `documents`
- `metadatas`
- `distances`
- `unique_count`
- `total_count`
- `claim_to_results`

其中：

- `claim_to_results` 给阶段 4 做“按主张分组证据”使用
- `metadatas[].doi` 给后续 DOI 提取使用

### 7.12 阶段 2 缓存

阶段 2 也有 Redis 缓存和 singleflight。

阶段 2 cache key 源码如下：

```python
def build_stage2_cache_key(...):
    return redis_service.key_factory.cache(
        "qa",
        "stage2",
        _qa_cache_epoch(),
        _kb_data_epoch(),
        _stage2_retrieval_version(),
        route_hint,
        _runtime_model_name(runtime),
        int(n_results_per_claim),
        _flags_hash(),
        _question_hash(question),
        _claims_hash(retrieval_claims),
    )
```

`_flags_hash()` 里实际包含：

- `QA_STAGE2_FORCE_KEYWORD_INJECTION`
- `QA_STAGE2_ENTITY_LOCK_ENABLED`
- `QA_RETRIEVAL_RERANK_ENABLED`
- `QA_RETRIEVAL_RERANK_CANDIDATES`
- `QA_RETRIEVAL_RERANK_PROVIDER`
- `QA_RETRIEVAL_RERANK_MODEL`
- `QA_STAGE2_QUERY_EXPANSION_ENABLED`
- `QUERY_EXPANSION_MODEL`

所以阶段 2 的缓存不是简单按问题命中，而是把“问题 + claims + 检索策略版本”一起纳入 key。

缓存 key 组成更复杂，包含：

- `QA_CACHE_EPOCH`
- `KB_DATA_EPOCH`
- `QA_STAGE2_RETRIEVAL_VERSION`
- route
- model
- `n_results_per_claim`
- 一组 flags hash
- question hash
- claims hash

也就是说，只要检索策略、kb epoch、claim 列表发生变化，缓存就会失效。

### 7.13 阶段 2 失败和取消分支

阶段 2 可能出现的非成功分支：

- 开始前已取消
- 并行中取消
- 聚合前取消
- 单个 claim 失败
- 全部 claim 最终没有有效结果

orchestrator 的处理策略是：

- 只要整体 `success=False`，就退回 `deep_answer`
- 不会把整条问答直接打挂

## 8. 阶段 2.5：MD 原文补证据

核心文件：

- facade 调用：`generation_driven_rag_facade.py::stage25_md_expansion`
- 真正实现：`generation_pipeline/md_expansion.py`

阶段 2.5 的目标不是替代阶段 3，而是先看看是否能直接从 MD 原文向量库里补到足够多的证据，降低后续 PDF 加载成本。

### 8.1 orchestrator 中的阶段 2.5 / 3 / 4 串接源码

这段源码是后半条链的总控制器，决定了 2.5、3、4 三个阶段如何衔接。

文件：`backend/app/modules/qa_kb/orchestrators/generation.py`

```python
yield sse_event({"type": "thinking", "content": "🧩 阶段二点五：尝试MD原文扩展检索..."})
md_expansion_result = self._timed(
    timings,
    "stage25",
    lambda: self.stage25.run(
        runtime=runtime,
        retrieval_results=stage2_result,
        user_question=question,
        dois=dois,
    ),
)

skip_decision = self.evaluate_stage3_pdf_skip_fn(md_expansion_result=md_expansion_result)
skip_pdf = bool(skip_decision.get("should_skip"))
skip_reason = str(skip_decision.get("reason") or "")

if skip_pdf:
    pdf_chunks = dict(md_expansion_result.get("md_chunks_by_doi") or {})
    timings["stage3"] = 0.0
    yield sse_event(
        {
            "type": "thinking",
            "content": (
                "📄 阶段三：MD证据命中阈值，跳过PDF溯源..."
                f"（hit_doi={skip_decision.get('hit_doi_count', 0)}, "
                f"md_chunks={skip_decision.get('total_md_chunks', 0)}）"
            ),
        }
    )
else:
    yield sse_event(
        {
            "type": "thinking",
            "content": f"📄 阶段三：加载 {len(dois)} 个文献的原文（提取 top 8 个最相关chunk）...",
        }
    )
    pdf_chunks = self._timed(
        timings,
        "stage3",
        lambda: self.stage3.run(
            runtime=runtime,
            dois=dois,
            max_chunks_per_doi=3,
            should_cancel=should_cancel,
        ),
    )
    if (
        md_expansion_result.get("applied")
        and self.merge_pdf_chunks_with_md_fn is not None
        and md_expansion_result.get("md_chunks_by_doi")
    ):
        pdf_chunks = self.merge_pdf_chunks_with_md_fn(
            pdf_chunks=pdf_chunks,
            md_chunks=md_expansion_result.get("md_chunks_by_doi", {}),
        )

yield sse_event({"type": "thinking", "content": "✍️ 阶段四：综合预回答与原文chunk生成答案..."})
yield sse_event(
    {
        "type": "metadata",
        "query_mode": "生成驱动检索（PDF溯源）",
        "route": "kb_qa",
        "pipeline_mode": "new",
        "use_generation_driven": 1,
        "stage3_pdf_skipped": skip_pdf,
        "stage3_pdf_skip_reason": skip_reason,
        "stage_timings_ms": timings,
    }
)
```

### 8.2 阶段 2.5 源码

文件：`backend/app/modules/generation_pipeline/md_expansion.py`

```python
def _resolve_md_runtime(...):
    resolved_enabled = env_bool("QA_STAGE25_MD_EXPANSION_ENABLED", True)
    resolved_db_path = str(db_path or os.getenv("VECTOR_DB_MD_PATH", "vector_database_md")).strip()
    resolved_collection = str(collection_name or os.getenv("VECTOR_DB_MD_COLLECTION", "md_papers")).strip() or "md_papers"
    resolved_max_dois = env_int("QA_STAGE25_MD_MAX_DOIS", 20, minimum=1, maximum=100)
    resolved_chunks_per_doi = env_int("QA_STAGE25_MD_CHUNKS_PER_DOI", 5, minimum=1, maximum=20)
    resolved_global_enabled = env_bool("QA_STAGE25_MD_GLOBAL_SUPPLEMENT_ENABLED", True)
    resolved_global_topk = env_int("QA_STAGE25_MD_GLOBAL_TOPK", 20, minimum=1, maximum=100)
    resolved_global_max_new_dois = env_int("QA_STAGE25_MD_GLOBAL_MAX_NEW_DOIS", 5, minimum=0, maximum=50)
    ...

def _search_md_chunks_for_doi(...):
    where_candidates = [
        {"doi": doi},
        {"DOI": doi},
        {"source_doi": doi},
        {"document_name": doi.replace("/", "_", 1) + ".md"},
    ]
    for where in where_candidates:
        ...
    global_n = max(n_results * 5, 30)
    ...

def evaluate_stage3_pdf_skip(...):
    resolved_enabled = env_bool("QA_STAGE3_SKIP_PDF_WHEN_MD_HIT", False)
    resolved_min_hit_dois = env_int("QA_STAGE3_SKIP_PDF_MIN_MD_HIT_DOIS", 1, minimum=1, maximum=50)
    resolved_min_chunks = env_int("QA_STAGE3_SKIP_PDF_MIN_MD_CHUNKS", 3, minimum=1, maximum=200)
    ...
    if not resolved_enabled:
        decision["reason"] = "switch_off"
        return decision
    if not applied or not has_chunks:
        decision["reason"] = "md_not_applied"
        return decision
    if hit_doi_count < resolved_min_hit_dois:
        decision["reason"] = "hit_doi_below_threshold"
        return decision
    if total_md_chunks < resolved_min_chunks:
        decision["reason"] = "chunk_below_threshold"
        return decision
    decision["should_skip"] = True
    decision["reason"] = "threshold_matched"
    return decision
```

### 8.3 阶段 2.5 的真实行为

阶段 2.5 的输入是：

- `retrieval_results`
- `user_question`
- `dois`
- `literature_expert.embedding_model`

它的真实步骤是：

1. 解析 MD runtime 配置
2. 打开 MD Chroma collection
3. 用当前问题生成一个 query embedding
4. 对阶段 2 得到的 DOI 列表逐个做 MD 检索
5. 若启用 global supplement，再从全库补若干新 DOI
6. 输出 `md_chunks_by_doi` 和统计信息
7. 交给 `evaluate_stage3_pdf_skip()` 决定是否跳过阶段 3

### 8.4 为什么阶段 2.5 不只是“补一点文本”

从源码看，阶段 2.5 有两个非常重要的系统作用：

1. 证据补强  
   - 如果检索到了 DOI，但 PDF 还没加载，MD 库可以先补到一批证据
2. 阶段裁剪  
   - 如果 MD 证据已经足够，后面可以直接跳过 PDF 溯源，省掉阶段 3 的 IO 和 PDF 解析成本

所以阶段 2.5 不是附属功能，而是旧版普通问答里非常实用的一个优化层。

### 8.5 阶段 2.5 的配置项

- `QA_STAGE25_MD_EXPANSION_ENABLED`
- `VECTOR_DB_MD_PATH`
- `VECTOR_DB_MD_COLLECTION`
- `QA_STAGE25_MD_MAX_DOIS`
- `QA_STAGE25_MD_CHUNKS_PER_DOI`
- `QA_STAGE25_MD_GLOBAL_SUPPLEMENT_ENABLED`
- `QA_STAGE25_MD_GLOBAL_TOPK`
- `QA_STAGE25_MD_GLOBAL_MAX_NEW_DOIS`
- `QA_STAGE25_MD_GLOBAL_MIN_SCORE`

### 8.6 `fallback_reason` 的真实含义

阶段 2.5 输出里的 `stats.fallback_reason` 可能是：

- `disabled`
- `empty_doi_list`
- `chromadb_unavailable`
- `collection_unavailable:*`
- `embedding_unavailable`
- `no_md_match`

这类失败不会中断整条链，只会让后续继续走 PDF 路径。

## 9. 阶段 3：按 DOI 加载 PDF chunk

核心文件：

- facade 调用：`generation_driven_rag_facade.py::stage3_load_pdf_chunks`
- 真正实现：`generation_pipeline/pdf_pipeline.py`

### 9.1 阶段 3 源码

文件：`backend/app/modules/generation_pipeline/pdf_pipeline.py`

```python
def find_pdf_path(doi: str, project_file: str, logger: Any) -> Optional[str]:
    papers_dir = Path(os.path.dirname(os.path.abspath(project_file))) / "papers"

    resolved = ensure_local_paper_pdf(doi=doi, papers_dir=papers_dir, logger=logger)
    if resolved:
        return str(resolved)

    possible_names = [
        f"{doi_clean}.pdf",
        doi_clean.replace("/", "_") + ".pdf",
    ]
    for filename in possible_names:
        pdf_path = papers_dir / filename
        if pdf_path.exists():
            return str(pdf_path)

    pattern = f"{prefix}_{suffix}*.pdf"
    matches = glob.glob(str(papers_dir / pattern))
    if matches:
        return matches[0]
    return None

def extract_chunks_from_pdf(
    pdf_path: str,
    doi: str,
    max_chunks: int,
    logger: Any,
) -> List[Dict[str, Any]]:
    doc = fitz.open(pdf_path)
    max_pages = min(doc.page_count, 15)
    skip_first_page_chars = 1500
    ...
    for page_num in range(max_pages):
        ...
        if page_num == 0:
            if len(text) > skip_first_page_chars:
                text = text[skip_first_page_chars:]
            else:
                continue
        paragraphs = text.split("\\n\\n")
        ...
        if len(para) < 50:
            continue
        if current_chars + len(para) > chunk_max_chars and current_chunk:
            chunks.append({...})
            if len(chunks) >= max_chunks:
                break
```

### 9.2 阶段 3 的真实行为

阶段 3 的输入是 DOI 列表，不是全文检索结果。

它的处理顺序：

1. 用 DOI 去 `papers/` 和对象存储查 PDF
2. 找到 PDF 后用 PyMuPDF 打开
3. 最多扫描前 15 页
4. 首页前 1500 字直接跳过
5. 用双换行拆段
6. 少于 50 字的段落丢弃
7. 每个 chunk 最多约 800 字
8. 每个 DOI 最多提取 `max_chunks_per_doi=3`

### 9.3 阶段 3 为什么会跳过首页前 1500 字

从源码看，这不是偶然，是明确策略：

- 首页通常是标题、作者、单位、摘要模板、版权信息
- 这些内容对问答帮助有限，还容易污染引用

所以阶段 3 默认会避开首页前半段，尽量把 chunk 留给正文实验部分。

### 9.4 PDF 查找顺序

`find_pdf_path()` 的查找顺序是：

1. 先通过 `ensure_local_paper_pdf()` 尝试对象存储同步到本地
2. 再找 `papers/<doi>.pdf`
3. 再找 `papers/<doi_with_underscore>.pdf`
4. 最后再用 glob 模糊匹配

这说明旧版阶段 3 默认支持：

- 本地 papers 目录
- MinIO/对象存储同步
- 多种 DOI 命名方式

### 9.5 阶段 3 的取消语义

阶段 3 支持 `should_cancel()`：

- 每处理一个 DOI 前检查一次
- 找到 PDF 后、正式抽取前再检查一次

一旦取消，不会继续剩余 DOI 的处理。

### 9.6 阶段 3 输出结构

输出是：

- `Dict[doi, List[chunk]]`

chunk 包含：

- `doi`
- `page`
- `chunk_id`
- `chunk_type`
- `text`
- `word_count`

如果某个 DOI 找不到 PDF 或没有有效 chunk，不报错，只是该 DOI 不进入结果。

## 10. 阶段 4：基于证据流式合成最终答案

核心文件：

- facade 调用：`generation_driven_rag_facade.py::stage4_synthesis_with_pdf_chunks`
- 真正实现：`generation_pipeline/synthesis_streaming.py`

### 10.1 Stage4 引用策略源码

文件：`backend/app/modules/generation_pipeline/synthesis_postprocess.py`

```python
def resolve_stage4_reference_policy(
    *,
    topk: int | None = None,
    min_citations: int | None = None,
    element_guard: bool | None = None,
) -> Tuple[int, int, bool]:
    resolved_topk = env_int("QA_STAGE4_REFERENCE_TOPK", 5, minimum=3, maximum=20)
    resolved_min_citations = env_int("QA_STAGE4_MIN_CITATIONS", 3, minimum=1, maximum=20)
    if resolved_min_citations > resolved_topk:
        resolved_min_citations = resolved_topk
    resolved_element_guard = env_bool("QA_STAGE4_ELEMENT_GUARD", True)
    return resolved_topk, resolved_min_citations, resolved_element_guard

def build_top_reference_context(...):
    ranked = _compute_doi_scores_from_retrieval(retrieval_results)
    if not ranked and pdf_chunks:
        ranked = [(doi, float(len(chunks))) for doi, chunks in pdf_chunks.items() if chunks]
    if resolved_element_guard and ranked:
        element_groups = _extract_question_elements(user_question)
        ...
        if preferred:
            ranked = preferred + rest
    top_refs_with_scores = ranked[:resolved_topk]
    reference_text = _build_reference_instruction_text(...)
    return top_refs_with_scores, reference_text
```

### 10.2 Stage4 主体源码

文件：`backend/app/modules/generation_pipeline/synthesis_streaming.py`

```python
def iter_stage4_synthesis_with_pdf_chunks(...):
    stage4_topk = env_int("QA_STAGE4_REFERENCE_TOPK", 5, minimum=3, maximum=20)
    stage4_min_citations = env_int("QA_STAGE4_MIN_CITATIONS", 3, minimum=1, maximum=20)
    if stage4_min_citations > stage4_topk:
        stage4_min_citations = stage4_topk
    stage4_element_guard = env_bool("QA_STAGE4_ELEMENT_GUARD", True)
    stage4_citation_verify = env_bool("QA_STAGE4_CITATION_VERIFY_AFTER_SYNTHESIS", ...)
    use_two_stage = env_bool("QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED", ...)
    use_structure_only = env_bool("QA_STAGE4_STRUCTURE_ONLY_MODE", ...)

    top5_with_scores, top5_reference_list = build_top5_reference_context_fn(
        retrieval_results=retrieval_results,
        logger=logger,
        topk=stage4_topk,
        min_citations=stage4_min_citations,
        element_guard=stage4_element_guard,
        user_question=user_question,
        pdf_chunks=pdf_chunks,
    )

    evidence_documents = format_pdf_chunks_evidence_fn(pdf_chunks, user_question)
    ...
    if use_two_stage:
        facts = _extract_citable_facts_from_evidence(...)
        if facts:
            prompt = STAGE4_FACT_SYNTHESIS_PROMPT.format_map(...)
    if not prompt and use_structure_only and not use_two_stage:
        opening_paragraph, structure_outline = _extract_structure_from_deep_answer(deep_answer)
        if structure_outline:
            prompt = STAGE4_STRUCTURE_ONLY_PROMPT.format_map(...)
    if not prompt:
        prompt = stage2_prompt.format_map(safe_kwargs)

    stream = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
        max_tokens=4000,
        stream=True,
    )

    final_chunks = []
    for chunk in stream:
        ...
        if hasattr(delta, "content") and delta.content:
            text = delta.content
            final_chunks.append(text)
            yield text

    final_answer = "".join(final_chunks).strip()

    if stage4_citation_verify and pdf_chunks:
        final_answer, valid_dois, invalid_dois = _validate_answer_dois_with_pdf_chunks(
            answer=final_answer,
            pdf_chunks=pdf_chunks,
        )

    cited_dois, cited_dois_set = extract_cited_dois_fn(final_answer=final_answer, logger=logger)
    log_top5_coverage_fn(...)
    references = build_references_from_pdf_chunks_fn(cited_dois=cited_dois, pdf_chunks=pdf_chunks)

    yield {
        "success": True,
        "final_answer": final_answer,
        "references": references,
        "cited_dois": cited_dois,
        "source_count": len(pdf_chunks),
    }
```

### 10.3 Stage4 证据整理源码

Stage4 在把 `pdf_chunks` 送进模型之前，还会做一轮相关性筛选。

文件：`backend/app/modules/generation_pipeline/reference_alignment.py`

```python
def format_pdf_chunks_evidence(
    pdf_chunks: Dict[str, List[Dict[str, Any]]],
    user_question: str,
    logger: Any,
) -> str:
    chunks_per_doi = env_int("QA_STAGE4_EVIDENCE_CHUNKS_PER_DOI", ..., minimum=1, maximum=20)
    chunk_max_chars = env_int("QA_STAGE4_EVIDENCE_CHUNK_MAX_CHARS", ..., minimum=200, maximum=5000)

    keywords_with_weights = extract_question_keywords_with_weights(user_question)

    doi_scores = []
    for doi, chunks in pdf_chunks.items():
        score = 0.0
        matched_keywords = set()
        core_match_count = 0
        for chunk in chunks:
            text = chunk.get("text", "").lower()
            for kw, weight in keywords_with_weights.items():
                if kw.lower() in text:
                    matched_keywords.add(kw)
                    if weight >= 3.0:
                        score += weight
                        core_match_count += 1
                    else:
                        score += weight * 0.5
        doi_scores.append({...})

    doi_scores.sort(key=lambda item: (item["score"], item["core_match_count"]), reverse=True)
    relevant_dois = doi_scores[:10]
    ...
```

这段代码说明：

- Stage4 不是把所有 DOI 证据都塞给模型
- 它会先根据“问题关键词命中情况”给 DOI 再排一次序
- 默认只保留前 10 个相关 DOI
- 每个 DOI 再受 `QA_STAGE4_EVIDENCE_CHUNKS_PER_DOI` 和 `QA_STAGE4_EVIDENCE_CHUNK_MAX_CHARS` 约束

所以旧版普通问答的 Stage4 实际是“检索结果 -> PDF/MD 证据 -> 再压缩筛选 -> 再喂给模型”，而不是简单拼接全文。

### 10.4 Stage4 的真实职责

阶段 4 不是“把 deep_answer 重新说一遍”，而是做下面这些事情：

1. 从检索结果里挑出 top-k DOI
2. 根据问题元素做 element guard 排序
3. 把 PDF/MD chunk 整理成证据文档
4. 选择合适的 prompt 模式
5. 用流式 LLM 生成最终答案
6. 对答案里的 DOI 做后校验
7. 抽取引用 DOI，构造 references

所以 Stage4 实际上是：

- 证据压缩
- 引用策略控制
- LLM 流式生成
- 后处理清洗

全部合在一起的终端合成器。

### 10.5 Top-k 引用策略

从 `synthesis_postprocess.py` 可以确认，Stage4 的引用策略不是拍脑袋，而是明确算法：

1. 从 `claim_to_results` 里提取每个 DOI 的相似度
2. 对 DOI 求平均得分
3. 排序后取 top-k
4. 如开启 `element_guard`，优先把正文 chunk 中包含问题关键元素的 DOI 提前
5. 生成一段“必须至少引用 N 篇文献”的提示文本给模型

所以旧版答案里“至少插入 10 篇 DOI”的行为，根源不是前端，而是 Stage4 prompt 和引用策略共同强制出来的。

### 10.6 Stage4 的三种 prompt 模式

Stage4 可能进入 3 种模式：

1. 单阶段模式  
   - 默认
   - 直接用 `STAGE2_PROMPT`
2. 两阶段事实提取模式  
   - `QA_STAGE4_TWO_STAGE_SYNTHESIS_ENABLED=true`
   - 先抽 `fact + doi`
   - 再用事实列表生成答案
3. 结构大纲模式  
   - `QA_STAGE4_STRUCTURE_ONLY_MODE=true`
   - 从 `deep_answer` 中提取 opening + outline
   - 再根据证据重写

如果两阶段事实为空，会回退单阶段。
如果结构模式抽不出 outline，也回退单阶段。

### 10.7 真正的流式输出发生在哪

这点很关键。

旧版普通问答的正文 token 流式输出，只发生在 Stage4：

- 前面阶段只发 `thinking/step/metadata`
- Stage4 才真正 `yield text`
- orchestrator 再把这些文本片段转成 SSE `content`

所以如果后面迁移时前端看起来“前面一直是思考，最后突然一大段出来”，首先就要检查 Stage4 是否真的按 `delta.content` 实时往外 yield 了。

### 10.8 Stage4 的后校验

Stage4 结束后会做 3 个后处理：

1. `_validate_answer_dois_with_pdf_chunks()`  
   - 清掉不在证据 DOI 集合里的 DOI
2. `extract_cited_dois_fn()`  
   - 抽取答案中真正出现的 DOI
3. `build_references_from_pdf_chunks_fn()`  
   - 生成简化版 references 列表

注意第一步只校验“DOI 是否属于当前证据集合”，不做句级语义对齐。

### 10.9 Stage4 失败后的真实行为

如果 Stage4 失败：

- 不会直接 500
- orchestrator 会回退到 `deep_answer`
- 如果一个 token 都没吐出来，就直接走 fallback `iter_result_events`
- 如果已经吐出部分 token，就直接发一个 `done`，把现有文本拼起来交付前端

这说明旧版对“回答不中断”是有明确保护的。

## 11. SSE 事件顺序与回退原则

普通问答流式事件顺序大致如下：

1. `step/dispatch`
2. `thinking`: 正在进入知识库问答主链路
3. `thinking`: 阶段一
4. `thinking`: 阶段二
5. `thinking`: 阶段二点五
6. `thinking`: 阶段三（或跳过阶段三的解释）
7. `thinking`: 阶段四
8. `metadata`
9. 多个 `content`
10. `done`

### 11.1 `AskStreamTap` 如何归一化步骤并汇总结果

旧版 SSE 流并不是原样发给前端后就结束了，中间还有一层归一化与汇总。

源码在：

- `/home/cqy/worktrees/fastapi-version/backend/app/modules/ask_gateway/streaming.py`

```python
def normalize_stream_event(event: dict[str, Any]) -> dict[str, Any]:
    payload = dict(event or {})
    event_type = str(payload.get("type") or "").strip().lower()
    if event_type not in {"thinking", "step"}:
        return payload

    message = str(payload.get("message") or payload.get("content") or "").strip()
    title, detail = _split_step_message(message)
    normalized = dict(payload)
    normalized["type"] = "step"
    normalized["step"] = _derive_step_key(payload=payload, title=title, detail=detail)
    normalized["message"] = message or normalized["step"]
    normalized["title"] = str(payload.get("title") or title or normalized["step"])
    normalized["detail"] = str(payload.get("detail") or detail or "")
    normalized["status"] = _normalize_step_status(payload.get("status"), "processing")
    if event_type == "thinking":
        normalized.setdefault("legacy_type", "thinking")
    return normalized

class AskStreamTap:
    def wrap(self, source: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
        for item in source:
            payload = normalize_stream_event(dict(item or {}))
            event_type = str(payload.get("type") or "")
            if event_type == "content":
                self.summary.assistant_content += str(payload.get("content") or "")
            elif event_type == "metadata":
                self.summary.query_mode = str(payload.get("query_mode") or self.summary.query_mode)
            elif event_type == "step":
                self._upsert_summary_step(payload)
            elif event_type == "done":
                self.summary.done_seen = True
                ...
            yield payload
```

这意味着旧版后端做了两件事：

1. 把 `thinking` 统一归一成前端可消费的 `step`
2. 在流结束时把完整 assistant 内容、步骤、引用、timings 汇总起来

所以前端看到的“阶段列表”并不一定是下游原始输出，而是经过 `normalize_stream_event()` 规整过后的结果。

## 11.2 旧版前端如何消费 SSE

后端流式语义是否正确，最终还要看前端如何消费。

关键代码在：

- `/home/cqy/worktrees/fastapi-version/frontend-vue/src/views/Home.vue`

核心逻辑是：

```javascript
for await (const data of api.askStream(...)) {
  if (data.type === 'thinking') {
    const stepPayload = buildStepPayload(data, `thinking_${thinkingIndex}`, 'processing')
    upsertStreamingStep(stepPayload, activeStepKey, { markPreviousActiveSuccess: true })
    activeStepKey = stepPayload.step
  } else if (data.type === 'step') {
    const stepPayload = buildStepPayload(data, `step_${Date.now()}`, 'processing')
    upsertStreamingStep(stepPayload, activeStepKey, {...})
    activeStepKey = stepPayload.step
  } else if (data.type === 'metadata') {
    store.updateLastBotMessage({ expert: data.expert, queryMode: modeRaw || modeFromExpert }, { persist: false })
  } else if (data.type === 'content') {
    pendingStreamContent += String(data.content || '')
    scheduleStreamContentFlush()
  } else if (data.type === 'done') {
    ...
    steps.forEach((step, idx) => {
      if (normalizeStepStatus(step.status) === 'processing') {
        steps[idx] = { ...step, status: 'success', updatedAt: new Date().toISOString() }
      }
    })
    store.updateLastBotMessage(updates)
  } else if (data.type === 'error') {
    ...
    store.updateLastBotMessage({ content: '错误: ' + data.error, isComplete: true })
  }
}
```

这里能看出旧版前端对后端 SSE 的几个真实假设：

- `thinking` 和 `step` 都会被当成“过程步骤”
- `metadata` 主要更新 `queryMode`
- `content` 是正文流
- `done` 到来后，所有还在 `processing` 的步骤都会被前端强制标记成 `success`
- `error` 到来后，当前活动步骤会被标成 `error`

所以如果后面迁移后前端“步骤不对”“一直 processing 不结束”，往往不是前端逻辑错了，而是后端 SSE 事件顺序或字段不兼容。

### 11.3 `metadata` 和 `done` 的真实字段

从 orchestrator 源码可以确认：

`metadata` 至少会包含：

- `query_mode`
- `route=kb_qa`
- `pipeline_mode=new`
- `use_generation_driven=1`
- `stage3_pdf_skipped`
- `stage3_pdf_skip_reason`
- `stage_timings_ms`

`done` 至少会包含：

- `query_mode`
- `route`
- `doi_count`
- `chunk_count`
- `source_count`
- `final_answer`
- `timings`
- `references`

这也是前端为什么既能显示阶段，又能在结束时一次性拿到完整答案、引用和 timings。

旧版普通问答的总体原则不是“某一步失败就整体报错”，而是尽量回退：

- 阶段 1 JSON 解析失败 -> 仅预回答
- 阶段 1 真异常 -> error
- 阶段 2 失败 -> 返回 `deep_answer`
- 阶段 2 找不到 DOI -> 返回 `deep_answer`
- 阶段 2.5 失败 -> 不影响，继续 PDF 路径
- 阶段 3 没找到 PDF/chunk -> 阶段 4 仍可能尝试，但证据会变弱
- 阶段 4 失败 -> 返回 `deep_answer`

### 11.4 流结束后的持久化

这一点文档之前也写浅了。旧版在流结束后，不只是结束 SSE，还会基于 `AskStreamTap.summary` 持久化 assistant 消息。

持久化入口在：

- `/home/cqy/worktrees/fastapi-version/backend/app/modules/conversation/service.py`

核心逻辑是：

```python
def persist_assistant_summary(
    self,
    *,
    summary: dict[str, Any],
    payload: dict[str, Any],
    context: Any,
    runtime: Any,
) -> None:
    if context is None or not bool((summary or {}).get("done_seen")):
        return
    conversation_id = self._conversation_id_from_payload(payload)
    if conversation_id is None:
        return
    content = str((summary or {}).get("assistant_content") or "").strip()
    if not content:
        return
    metadata = {
        "source": "ask_stream",
        "query_mode": str((summary or {}).get("query_mode") or ""),
        "references": (summary or {}).get("references") or [],
        "steps": (summary or {}).get("steps") or [],
        "route": str((summary or {}).get("route") or ""),
        "used_files": (summary or {}).get("used_files") or [],
        "timings": (summary or {}).get("timings") or {},
        "trace_id": str((summary or {}).get("trace_id") or ""),
        "file_selection": (summary or {}).get("file_selection") or {},
        "done_seen": True,
    }
    self.add_message(...)
```

这说明旧版普通问答的“聊天记录可回看阶段步骤、引用、timings”，不是前端自己拼出来的，而是后端把这些 metadata 一并落库了。

### 11.5 一句话总结

旧版普通问答的本质是：

- 先让 LLM 产出一个高质量结构骨架
- 再把骨架拆成可检索 claim
- 再并行检索 DOI
- 再用 MD/PDF 原文把答案重新约束和补证据
- 最后在流式生成阶段强制内联 DOI，并做引用合法性清洗

因此它不是“一个简单的 RAG 接口”，而是一条带缓存、并发控制、query 护栏、证据补强、PDF 溯源、引用约束和多级回退的完整问答流水线。
