# fastQA Rerank 本地迁移方案

## 1. 背景

当前 fastQA 的 `rerank_service.py` 只支持 **DashScope 原生 API** 格式：

```
POST https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank
```

请求体：
```json
{
  "model": "qwen3-vl-rerank",
  "input": {
    "query": "用户的查询",
    "documents": ["文档1", "文档2"]
  },
  "parameters": {
    "return_documents": false,
    "top_n": 5
  }
}
```

响应体：
```json
{
  "output": {
    "results": [
      {"index": 2, "relevance_score": 0.95},
      {"index": 0, "relevance_score": 0.82}
    ]
  }
}
```

本地已部署的 `Qwen3-VL-Reranker-2B` 服务运行在 `localhost:8084`，使用的是自定义 FastAPI 格式，与 DashScope 不兼容。

## 2. 目标

在 **不改动现有 DashScope 逻辑** 的前提下，增加一个 `provider == "local"` 分支，使其能对接本地部署的 rerank 服务（采用 OpenAI 兼容的 `/v1/rerank` 格式）。

## 3. 环境变量

`microscopic_expert.py` 已经读取以下变量，但需要做 provider-aware 解析，避免 local provider 继承 DashScope 的 key 和默认 URL：

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `QA_RETRIEVAL_RERANK_PROVIDER` | `dashscope` | 改为 `local` 即走本地分支 |
| `QA_RETRIEVAL_RERANK_API_KEY` | `""` | 本地服务若无需鉴权可留空 |
| `QA_RETRIEVAL_RERANK_BASE_URL` | DashScope: `https://dashscope.aliyuncs.com`；local: `http://localhost:8084` | 可按部署地址覆盖 |
| `QA_RETRIEVAL_RERANK_MODEL` | `qwen3-vl-rerank` | 本地模型名按实际填写 |

## 4. 改造方案

### 4.1 核心改动范围

核心改动覆盖：

- `fastQA/app/modules/generation_pipeline/rerank_service.py`
- `fastQA/app/modules/microscopic_expert.py`
- `fastQA/app/core/runtime.py`
- `fastQA/tests/test_rerank_service.py`
- `fastQA/tests/test_microscopic_expert.py`
- `fastQA/tests/test_stage2_hot_connection_runtime.py`

在 `rerank_documents()` 函数中，根据 `provider` 增加分支逻辑：

```python
provider = str(os.getenv("QA_RETRIEVAL_RERANK_PROVIDER", "dashscope") or "dashscope").strip().lower()

if provider == "local":
    # OpenAI 兼容格式：POST {base_url}/v1/rerank
    endpoint = str(base_url or "http://localhost:8084").rstrip("/") + "/v1/rerank"
    payload = {
        "model": model,
        "query": query,
        "documents": docs_to_rerank,
        "top_n": min(max(int(top_n), 1), len(docs_to_rerank)),
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response = req.post(endpoint, headers=headers, json=payload, timeout=timeout_seconds)
    data = response.json() if hasattr(response, "json") else {}
    items = data.get("results", [])

    for item in items:
        idx = item.get("index")
        score = item.get("relevance_score", 0.0)
        ...
else:
    # 现有 DashScope 逻辑，完全不动
    ...
```

### 4.2 两种可选协议

需要确认本地服务 `/v1/rerank` 接口的入参格式。

#### Option A：fastQA 适配 xinference 标准（推荐）

**xinference / OpenAI 兼容格式：**

```json
POST /v1/rerank
{
  "model": "qwen3-vl-rerank",
  "query": "纯字符串查询",
  "documents": ["文档1", "文档2"],
  "top_n": 5
}
```

响应：
```json
{
  "results": [
    {"index": 2, "relevance_score": 0.95, "document": "文档3"}
  ]
}
```

- **fastQA 改动**：按上述标准格式发送请求。
- **本地服务改动**：需要在 `localhost:8084` 新增或改造一个 `/v1/rerank` 端点，接受 `query` 为字符串、`documents` 为字符串数组。
- **优点**：符合业界标准（xinference、vLLM、TEI 均支持），后期交付给其他本地部署模型时零改动。
- **缺点**：本地服务需要加接口。

#### local provider 鉴权与默认 URL

- `QA_RETRIEVAL_RERANK_PROVIDER=local` 且未设置 `QA_RETRIEVAL_RERANK_BASE_URL` 时，默认使用 `http://localhost:8084`。
- `QA_RETRIEVAL_RERANK_API_KEY` 为空时，local provider 不发送 `Authorization`。
- local provider 不回退到 `DASHSCOPE_API_KEY`，避免内网本地服务误收 DashScope 凭证。
- DashScope provider 继续保留原有 `DASHSCOPE_API_KEY` 回退逻辑。

#### 热池 warmup 注意事项

如果 `FASTQA_STAGE2_RERANK_HOT_POOL_ENABLED=1`，热池 warmup 必须使用和当前 provider 一致的协议。否则 local provider 主调用可用，但 lane warmup 仍会向 DashScope endpoint 发送请求并导致热池降级。

#### Option B：fastQA 适配本地服务现有格式

**本地服务当前格式：**

```json
POST /rerank
{
  "instruction": "Retrieve text relevant to the user's query.",
  "query": {"text": "查询"},
  "documents": [{"text": "文档1"}, {"text": "文档2"}],
  "return_documents": false
}
```

响应：
```json
{
  "model_path": "...",
  "device": "cuda",
  "count": 2,
  "results": [
    {"index": 0, "score": 0.92}
  ]
}
```

- **fastQA 改动**：`provider == "local"` 分支构造上述对象格式请求，并从 `results` 中读取 `score` 字段。
- **本地服务改动**：无需改动。
- **优点**：本地服务零改动，立即可用。
- **缺点**：非标准格式，后期交付给其他模型时需要再改 fastQA；且当前格式支持多模态（image/video），如果只用文本 rerank 会引入不必要的复杂性。

## 5. 工作量评估

- **rerank_service.py**：新增 provider 分支代码。
- **microscopic_expert.py**：需要 provider-aware 环境变量解析，避免 local 继承 DashScope key/default URL。
- **runtime.py**：需要 provider-aware rerank 热池 warmup。
- **config.py**：无需改动。
- **本地服务（Option A）**：若选 A，需新增 `/v1/rerank` 端点，约 30 行。

**总体：小改动，风险低，完全向后兼容。**

## 6. 决策清单

| 决策项 | 建议 | 影响 |
|--------|------|------|
| 走 Option A 还是 Option B？ | **建议 Option A**，更标准 | 本地服务需加 `/v1/rerank` 接口 |
| 本地服务 `/v1/rerank` 是否需要鉴权？ | 若在内网可免鉴权 | fastQA 分支中 `Authorization` 头部可省略 |
| `top_n` 是否传给本地服务？ | 传，但本地服务可忽略 | fastQA 本身会在收到结果后做切片 |

## 7. 下一步

1. 确认选择 **Option A** 或 **Option B**。
2. 确认本地服务是否已有 `/v1/rerank` 端点，或是否需要新建。
3. 开始修改 `rerank_service.py` 的 `rerank_documents()` 函数，增加 `provider == "local"` 分支。
4. 本地启动 fastQA，设置环境变量 `QA_RETRIEVAL_RERANK_PROVIDER=local` 进行验证。
