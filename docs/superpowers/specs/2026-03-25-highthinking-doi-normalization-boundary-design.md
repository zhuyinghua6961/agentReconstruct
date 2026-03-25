# highThinkingQA DOI 规范化边界调整设计

**目标**
把 `highThinkingQA` 的 `ask` 主链路从 `server/storage/paper_storage.py` 中解耦出来，仅保留本地纯字符串 DOI 规范化依赖；不改变现有 `public-service` 文档资产服务边界，不引入跨服务 RPC。

**背景**
当前 `highThinkingQA` 的 `ask` 路径只使用 `paper_storage.normalize_doi()` 做字符串清洗，但 `paper_storage.py` 语义上属于论文 PDF 物化、MinIO、本地缓存这类文件资产逻辑。这样会造成误导：看起来 `ask` 依赖 storage/paper 资产层，实际只是在借一个工具函数。

同时，`public-service` 已有自己的 `storage_service.normalize_doi()` 实现。两边长期并行会增加漂移风险。

## 当前事实

### 1. `ask` 路径中的真实用途
`highThinkingQA/server/services/ask_service.py` 当前用 `normalize_doi()` 做三件事：
- 从答案文本中提取并去重 DOI
- 构造 `reference_links` / `pdf_links`
- 把模型输出中的 DOI 引用格式适配成前端消费格式

`ask` 路径并不依赖：
- `ensure_local_paper_pdf()`
- `paper_pdf_exists()`
- MinIO 下载
- 本地 PDF 物化

### 2. `public-service` 的真实边界
`public-service` 已拥有文档资产服务：
- `view_pdf`
- `summarize_pdf`
- `extract_pdf_text`
- 文档 reference preview
- storage 层 paper 文件物化与 MinIO 读取

因此，论文文件资产相关逻辑继续归 `public-service` 是正确的。

### 3. 不能直接做成“public-service 独占 + highThinkingQA 远程调用”
这次不应把 DOI 规范化改成 RPC，因为：
- 它是纯字符串函数
- 位于 `ask` 的答案整理/流式收尾路径
- 远程调用只会增加时延、失败面和耦合

## 设计决策

### 决策 A：本次不引入仓库级共享 Python 包
原因：
- `highThinkingQA` 与 `public-service` 各自以不同 cwd 启动
- 当前没有稳定的 repo-root 共享包路径
- 为一个纯工具函数引入跨服务 import path 改造，收益不匹配风险

### 决策 B：本次先完成“主链路职责纠偏”
本次实现只做：
- 在 `highThinkingQA` 内新增纯工具模块，例如 `server/utils/doi.py`
- 把 `ask_service` 改为依赖这个纯工具模块，而不是 `server/storage/paper_storage.py`
- 保持 `paper_storage.py` 继续服务于 legacy 文件资产路径
- 用测试锁定 `highThinkingQA` 与 `public-service` 在核心 DOI 规范化语义上的对齐

### 决策 C：将真正的跨服务共享作为后续阶段
后续如果要彻底去重，可以单开一期：
- 建 repo-root shared package
- 统一服务启动脚本 / `PYTHONPATH`
- 再把 `public-service` 与 `highThinkingQA` 同时切到共享模块

## 本次重构范围

### 修改
- `highThinkingQA/server/services/ask_service.py`
- `highThinkingQA/tests/test_ask_service_executor.py`
- `highThinkingQA/server/storage/paper_storage.py` 仅保留现有兼容注释，不再承担 ask 主链路职责

### 新增
- `highThinkingQA/server/utils/doi.py`
- `highThinkingQA/tests/test_doi_utils.py`

### 不改
- `public-service` 运行逻辑
- `public-service` storage/documents 边界
- 任意服务启动脚本的 import path
- `paper_storage.py` 的文件资产能力

## 目标行为

新增纯工具模块需要完整承接下列 DOI 清洗行为：
- 去掉 `doi:` 前缀
- URL decode 直到稳定
- `\\` 转 `/`
- 去掉首尾污染符号 `()[] ,;:.` 与空白
- 处理 `papers/...` 路径输入
- 处理绝对路径/相对路径形式的 `.pdf`
- 去掉 `.pdf` 后缀
- 将 `10.xxxx_xxx` 还原为 `10.xxxx/xxx`
- 保持空值与非法输入的稳妥退化

## 风险

### 风险 1：提取 DOI 的格式化结果发生变化
缓解：
- 为 `ask_service` 现有断言补充测试
- 为 DOI 纯工具增加脏输入样例测试

### 风险 2：与 `public-service` 规范化语义漂移
缓解：
- 用测试对齐当前核心样例
- 本次设计文档明确下一阶段再做真正共享

### 风险 3：误动 `paper_storage` 的 legacy 文件路径
缓解：
- 本次不删 `paper_storage` 中任何文件资产函数
- 仅切断 `ask_service` 对它的 import

## 验收标准
- `highThinkingQA ask_service` 不再 import `server.storage.paper_storage.normalize_doi`
- `highThinkingQA ask_service` 的 DOI 提取、前端格式适配、reference link 构造行为保持不变
- `paper_storage.py` 仍可为 legacy 文档路径提供现有函数
- 相关测试全部通过

## 后续阶段建议
1. 评估是否需要 repo-root 共享包
2. 如需要，单开一期统一 `highThinkingQA/public-service` 的 DOI 工具来源
3. 再考虑删除 `public-service` 与 `highThinkingQA` 中重复的规范化实现
