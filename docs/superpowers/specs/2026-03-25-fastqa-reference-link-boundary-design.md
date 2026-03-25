# fastQA 引用链接边界收口设计

**目标**
收口 `fastQA` 内部 `pdf_url/reference_links/pdf_links` 的重复构造点，保持常见 DOI 对外行为与旧版一致，同时把“DOI -> 前端可点击 PDF 链接”的规则收敛到单一门面，避免 ask/ask_stream/preview 三条链路继续漂移。

## 背景
这一轮不是继续处理 DOI 规范化本身，而是处理引用链接的边界问题。

当前 `fastQA` 内部存在 4 类重复构造：
- `app/modules/qa_kb/streaming.py::build_reference_links`
- `app/modules/qa_pdf/common.py::build_pdf_links`
- `app/routers/qa.py` 在 stream done / sync done 路径里二次补 `reference_links/pdf_links`
- `app/modules/documents/reference_preview.py::build_pdf_url`

这些实现目前大体一致，但边界已经开始发散：
- `kb_qa` 和 `pdf_qa` 各自维护链接拼接逻辑
- router 在 done 事件汇总时再次重复拼接
- reference preview 维护另一份 URL 构造函数
- stream path 与 sync path 对“上游自带 links”处理方式不同，存在 contract drift 风险

这类重复短期看不出问题，但后续只要任何一处开始“单独修正”编码规则、字段名或者去重规则，就会造成：
- SSE done event 和同步 ask 返回不一致
- `kb_qa` 和 `pdf_qa` 的引用链接格式不一致
- preview 接口与 ask 主链路行为不一致
- 上游 sidecar 给出的脏 `reference_links/pdf_links` 穿透到前端

## 对齐旧版的事实
已对照旧版 `fastapi-version/backend`：
- `qa_pdf/common.py` 旧版就是 `f"/api/v1/view_pdf/{doi}"`
- `reference_preview.py` 旧版也单独维护了 `build_pdf_url()`
- 也就是说，“重复存在”本身是旧版遗留，不是这次迁移独有问题

因此本次目标不是“发明新的引用协议”，而是：
- 在当前迁移版里先把重复收口
- 对常见 DOI 维持与旧版相同的 outward path
- 对非常见 DOI 补上 normalize 和 route-safe encode，避免统一以后把错误集中放大

## 设计决策

### 决策 1：继续留在 fastQA 本地，不依赖 public-service
引用链接只是 `fastQA` 对当前 documents/view_pdf 路由的内部协议适配，不应上升成 `public-service` 远程能力。

### 决策 2：统一到 storage/documents 活边界附近的小门面
这类链接本质上是“引用的 paper 如何被当前服务打开”，与 `documents/view_pdf` 路由和 storage 域更近，不应散落在 `qa_kb`、`qa_pdf`、router 三处。

统一门面放在 `fastQA/app/modules/storage/service.py`：
- `build_pdf_url(doi)`
- `build_pdf_links(references)`

这样做的原因：
- storage service 已经是 DOI 与 paper 资产的门面
- 上一轮 DOI 规范化已收口到这里
- 本轮继续把“DOI -> 可访问 PDF URL”也收口到这里，边界自然连续

### 决策 3：统一门面必须同时拥有 normalize + route-safe encode
如果 `storage_service` 只做字符串拼接，那它并不是真正的 outward boundary owner。

因此 `build_pdf_url()` 的职责不是简单 `f"/api/v1/view_pdf/{doi}"`，而是：
1. 先做 `normalize_doi()`
2. 再按 path segment 做 route-safe encode
3. 最后产出稳定的 `view_pdf` 路径

这样可以同时满足：
- 常见 DOI 输出仍保持 `/api/v1/view_pdf/10.1/demo` 这种旧行为
- `doi:` 前缀、percent-encoded、filename-like 输入能被统一折叠
- `?`、`#` 等保留字符不会在点击时被浏览器截断

### 决策 4：router 是最终 outward contract 边界，必须总是重建 links
`app/routers/qa.py` 不是链接协议的拥有者，但它是最终对前端出站的边界。

因此 router 的职责应是：
- 归一化 `references / reference_objects`
- 调统一门面重建 `reference_links/pdf_links`
- 再把规范后的 done payload 发给前端

这里的关键点是“重建”，不是“有则保留”。

原因：
- sync ask 路径已经会从 normalized references 重建 links
- 如果 stream done 路径保留上游传来的脏 links，就会与 sync ask 再次分叉
- router 既然是最后出站边界，就必须保证对外契约唯一

### 决策 5：调用方仍负责 reference 归一化，不把所有逻辑塞进门面
本次统一的是“链接构造”，不是“reference 去重/裁剪/对象归一化”全量上收。

因此：
- `normalize_reference_objects()` / `normalize_references()` 仍在调用方完成
- `storage_service` 只负责把已经选定的 DOI 列表变成稳定链接

这样能避免把这轮小收口升级成更大范围的 pipeline 重构。

## 方案比较

### 方案 A：推荐
- 在 `storage_service` 增加统一链接门面
- `qa_kb/streaming.py`、`qa_pdf/common.py`、`routers/qa.py`、`reference_preview.py` 全部改为消费该门面
- router 对 outward done payload 始终重建 links

优点：
- 改动小
- 边界清晰
- 能真正消除 sync/stream 漂移
- 与上一轮 DOI 收口方向一致

### 方案 B：新增 `reference_links.py` 纯工具模块
- 也可行
- 但会形成 DOI 一部分在 `storage_service`、另一部分在新工具模块的双中心

不推荐原因：
- 对当前代码结构来说，比 A 更分裂

### 方案 C：只统一 URL helper，不统一 router 的出站策略
- 不推荐
- 因为 stream path 仍可能透传上游脏 links，sync/stream 还是会漂

## 修改范围

### 修改
- `fastQA/app/modules/storage/service.py`
- `fastQA/app/modules/qa_kb/streaming.py`
- `fastQA/app/modules/qa_pdf/common.py`
- `fastQA/app/routers/qa.py`
- `fastQA/app/modules/documents/reference_preview.py`
- `fastQA/tests/test_documents_storage.py`
- `fastQA/tests/test_reference_link_boundary.py`
- 相关既有回归测试

### 不改
- `fastQA/app/modules/storage/paper_storage.py`
- `fastQA/app/modules/generation_pipeline/*`
- `fastQA/app/modules/documents/api.py`

## 目标行为
- `kb_qa` SSE done event 的 `reference_links/pdf_links` 继续存在，格式不变
- `pdf_qa` done event 的 `pdf_links` 继续存在，格式不变
- 同步 `ask` 汇总返回里的 `reference_links/pdf_links` 与 SSE done 保持一致
- preview 接口返回的 `pdf_url` 与 ask 主链路走同一出口
- 常见 DOI 仍保持 `/api/v1/view_pdf/10.1/a` 这类旧格式
- 异常 DOI 变体会先 normalize，再 route-safe encode

## 风险

### 风险 1：误改 URL 行为导致前端点击失效
缓解：
- 先写锁定常见 DOI 输出的失败测试
- 补充 normalize 变体和保留字符编码测试
- 明确验证 `kb_qa`、`pdf_qa`、preview 三条链路

### 风险 2：router 汇总逻辑和子模块逻辑再次分叉
缓解：
- 把 router 三个出站构造点都纳入测试
- 明确要求 stream done 覆盖上游脏 links，而不是 `setdefault`

### 风险 3：把 storage 域门面做得过重
结论：
- 当前只增加极小的纯函数门面，可接受
- 不引入状态、不引入 IO、不改变现有 storage 主链路

## 验收标准
- `qa_kb/streaming.py` 不再本地定义独立 `build_reference_links()`
- `qa_pdf/common.py` 不再本地定义独立 `build_pdf_links()`
- `reference_preview.py` 不再本地定义独立的 URL 拼接逻辑
- `storage_service.build_pdf_url()` 统一做 normalize + route-safe encode
- router 使用统一门面重建 outward `reference_links/pdf_links`
- 定向测试通过，且 sync ask / stream done / preview 行为一致
