# fastQA DOI 规范化收口设计

**目标**
收口 `fastQA` 内部 DOI 规范化的重复实现：保持 storage 域继续作为真实 paper 资产能力拥有者，只把 `documents/service.py` 中重复的 `normalize_doi()` 删除并改为统一依赖 storage 层入口。

## 背景
`fastQA` 与 `highThinkingQA` 不同。`fastQA` 的 generation pipeline 在真实问答路径里会直接调用 paper 存储能力：
- Stage2.5/context loading 会读取本地或 MinIO 里的 PDF 句子
- Stage3/pdf pipeline 会定位与物化 PDF 原文

因此 `fastQA/app/modules/storage/paper_storage.py` 不是误挂载，它属于真实活链路。

当前真正的问题不是“ask 主链路依赖了 storage”，而是 DOI 规范化实现出现重复：
- `app/modules/storage/paper_storage.py::normalize_doi`
- `app/modules/documents/service.py::normalize_doi`

两份长期并存会导致行为漂移风险。

## 设计决策

### 决策 1：storage 域继续拥有 DOI 规范化
在 `fastQA` 中，paper 文件名、paper 查找、MinIO 物化、本地查找都属于 storage 域。同一域内的 DOI 规范化应继续由 storage 拥有，而不是像 `highThinkingQA` 那样抽离出 ask 专属纯工具模块。

### 决策 2：documents 只消费 storage 入口，不再复制实现
`documents/service.py` 不再保留本地 `normalize_doi()` 实现，而是通过 `storage_service.normalize_doi()` 做统一入口。

### 决策 3：本次不动 generation pipeline
本次不调整：
- `app/modules/generation_pipeline/context_loading.py`
- `app/modules/generation_pipeline/pdf_pipeline.py`
- `app/modules/storage/paper_storage.py` 的物化逻辑

这样可以避免把“重复实现清理”升级成“活链路边界重构”。

## 方案比较

### 方案 A：推荐
- 在 `app/modules/storage/service.py` 增加 `normalize_doi()` 包装方法
- `documents/service.py` 删除本地 `normalize_doi()`，改用 `storage_service.normalize_doi()`
- 优点：最小改动，职责清晰，和 fastQA 当前 storage 主链路一致

### 方案 B：反向把 storage 改依赖 documents
- 不推荐
- 原因：会让真实 paper 资产层反过来依赖 documents 域，边界倒置

### 方案 C：新增 repo 级共享工具
- 暂不需要
- 原因：这次目标只是清理 fastQA 内部重复实现，不需要扩大到跨服务共享

## 修改范围

### 修改
- `fastQA/app/modules/storage/service.py`
- `fastQA/app/modules/documents/service.py`
- `fastQA/tests/test_documents_storage.py`
- `fastQA/tests/test_documents.py`

### 不改
- `fastQA/app/modules/storage/paper_storage.py`
- `fastQA/app/modules/generation_pipeline/context_loading.py`
- `fastQA/app/modules/generation_pipeline/pdf_pipeline.py`
- `fastQA/app/modules/qa_kb/streaming.py`
- `fastQA/app/modules/qa_pdf/common.py`

## 目标行为
- `documents/service.py` 不再拥有独立 DOI 规范化实现
- `documents/service.py` 的 `view_pdf_path/check_pdf/extract_pdf_text` 继续保持现有行为
- storage service 对外提供稳定的 DOI 规范化入口
- 不改变 PDF 路径解析、MinIO fallback、本地 paper 查找现有能力

## 风险

### 风险 1：documents 行为回归
缓解：
- 为 documents 路径增加“必须经由 storage_service.normalize_doi()”的边界测试
- 跑 documents 现有回归测试

### 风险 2：storage service 只是薄包装，增加一层可能被认为多余
结论：可接受。`storage_service` 本来就是 documents 域的门面入口，本次是在强化这层门面而不是引入新抽象。

## 验收标准
- `fastQA/app/modules/documents/service.py` 不再定义本地 `normalize_doi()`
- `fastQA/app/modules/storage/service.py` 暴露 `normalize_doi()` 入口
- 定向测试通过
- generation pipeline 相关 import 不受影响
