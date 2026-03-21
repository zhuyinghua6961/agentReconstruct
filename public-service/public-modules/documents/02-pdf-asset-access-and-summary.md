# documents PDF 资产访问、文本提取与摘要

对应代码：
- `backend/app/modules/documents/service.py`
- `backend/app/modules/documents/api.py`
- `backend/app/modules/storage/service.py`
- `backend/app/modules/qa_pdf/pdf_extractor.py`

## 1. papers 根目录如何确定

`DocumentsService.__init__()` 会解析：
- `PAPERS_DIR`

规则：
- 如果是绝对路径，直接使用
- 如果是相对路径，拼到 workspace 根目录
- 启动时保证目录存在

因此默认 papers 落点是：
- `<workspace>/papers`

## 2. `view_pdf_path()` 的真实语义

`view_pdf_path()` 并不是简单看本地文件是否存在，而是：
- 通过 `storage_service.ensure_local_paper_pdf()` 确保本地可读

这意味着：
- 若本地没有，但对象存储有，可能先从对象存储下载到本地
- 成功后才返回本地路径

所以 `view_pdf` 实际上是“确保本地副本可读后再响应”。

## 3. `storage_service.ensure_local_paper_pdf()` 怎么工作

从 storage 模块看，它的策略是：

1. 构建目标本地路径 `papers/<doi转文件名>.pdf`
2. 如果当前 backend 是 MinIO：
   - 本地存在时，会检查对象存储是否也存在
   - 对象不存在时，尝试把本地文件回传 mirror
3. 如果本地不存在：
   - 尝试从对象存储下载临时文件
   - 下载成功后原子替换成本地文件
4. 最后返回本地路径或 `None`

这代表 papers PDF 也是：
- 本地副本 + 对象存储副本

## 4. `view_pdf` 的 GET / HEAD 差异

### 4.1 GET

成功时返回：
- `FileResponse`
- `media_type=application/pdf`
- `Content-Disposition: inline`

### 4.2 HEAD

成功时返回：
- 轻量 `Response`
- 同样带 `Content-Disposition`
- 不附带实际文件体

这在测试里也被明确固定了。

## 5. DOI 到文件名和 URL 的映射

文件名通过 storage service 规则构建：
- `/` -> `_`
- 最终后缀 `.pdf`

例如 DOI：
- `10.1/test`

文件名会变成：
- `10.1_test.pdf`

而 `view_pdf` URL 路径则保留 DOI 的 path 结构：
- `/api/v1/view_pdf/10.1/test`

因此：
- 文件名归档规则和 URL 展示规则不是一套编码方式

## 6. `check_pdf()` 的语义很轻

它只返回：
- `exists`
- `doi`
- `filename`

内部调用：
- `storage_service.paper_exists()`

如果是 MinIO backend：
- 先查对象存储是否存在
- 再 fallback 本地文件

所以这个接口本质上是一个轻量存在性探测，不会保证本地文件已经准备好。

## 7. `extract_pdf_text()` 复用的是 qa_pdf 底层能力

正文提取不是 documents 自己写解析器，而是调用：
- `qa_pdf.pdf_extractor.extract_pdf_text`

DocumentsService 只负责：
- 确保本地 PDF
- 处理 fitz 可用性
- 传递 `exclude_references=True`
- 再把返回的全文切成段落数组

这说明 documents 和 qa_pdf 底层解析能力并未彻底解耦。

## 8. 段落切分规则

`_segment_paragraphs()` 的切分逻辑是启发式的：

- 按句号/问号/感叹号后的大写开头拆句
- 凑够一定长度或句子数再落一段
- 每段长度大致控制在：
  - 超过 150 且已有两句
  - 或超过 400
- 最多保留 100 段

因此：
- 返回的 `paragraphs` 不是原始 PDF 的自然段
- 而是二次重组后的阅读段

## 9. `summarize_pdf()` 的真实调用链

流程：

1. 检查 OpenAI SDK 是否可用
2. 确保本地 PDF 存在
3. 提取正文
   - `exclude_references=True`
   - 页数上限来自 `MAX_PDF_PAGES`
4. 如果正文超过 12000 字，截断
5. 构造中文总结 prompt
6. 直接 `OpenAI(...).chat.completions.create(...)`
7. 取 `resp.choices[0].message.content`

模型名写死为：
- `deepseek-v3.1`

## 10. 摘要能力并没有走统一平台 LLM runtime

这是一个很关键的实现事实。

`summarize_pdf()`：
- 不用 runtime.llm_client
- 不用 ask gateway
- 不用 generation runtime
- 直接在 documents 模块里 new OpenAI client

这意味着：
- 摘要是一个工具式旁路能力
- 它与其他问答链路的 provider 配置未必完全一致

## 11. 摘要失败条件

常见失败包括：

- OpenAI SDK 不可用 -> `503`
- PDF 不存在 -> `404`
- 提取不到正文 / 扫描版 -> `500`
- OpenAI 调用异常 -> `500`

这里不是“业务错误全部 200”，而是相对标准的错误码。

## 12. 这条 PDF 资产链的本质

综合起来，documents 的 PDF 能力不是单纯“文件下载”：

- `check_pdf`
  - 轻量存在性探测
- `view_pdf`
  - 确保本地副本可读后再打开
- `extract_pdf_text`
  - 复用 qa_pdf 底层解析
- `summarize_pdf`
  - 在模块内直接调 LLM 生成工具型摘要

所以这已经是一条完整的 PDF 资产消费链，而不是几个独立零散接口。
