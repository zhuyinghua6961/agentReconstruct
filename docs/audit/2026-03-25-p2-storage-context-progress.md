# 2026-03-25 P2 收尾记录：storage 收口与上下文分层固化

## 范围

本文记录 `P2-1` 和 `P2-2` 的最终收尾状态，只写当前仓库真实运行态：

- `fastQA`
- `gateway`
- `public-service`
- `highThinkingQA`

---

## 结论先行

### P2-1：storage legacy helper 收口已完成

当前真实活链路里，`fastQA` 已经不再通过 `storage_service` 包装 legacy `paper_storage`。

本轮最终状态：

- `fastQA/app/modules/generation_pipeline/context_loading.py`
- `fastQA/app/modules/generation_pipeline/pdf_pipeline.py`
- `fastQA/app/modules/documents/service.py`

都统一走：

- [fastQA/app/modules/storage/service.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/storage/service.py)

并且这个 `storage_service` 本身已经改成独立实现，不再 import：

- `fastQA/app/modules/storage/paper_storage.py`

这意味着：

- `fastQA` 活链路调用面已统一到单一正式出口
- `fastQA` 活链路根点也已不再依赖 legacy helper

### P2-2：同会话上下文分层固化已完成

本轮最终状态：

- `fastQA` 的上下文 builder 已收敛到最小稳定合同
- `highThinkingQA` 的 summary 清洗也改成白名单
- `fastQA` router 不再继续透传原始 authority snapshot

现在两条主链的 prompt-facing summary 都只认：

- `short_summary`
- `open_threads`
- `memory_facts`

其中：

- `fastQA` Stage1 / Stage4 prompt 只消费 `recent_turns_for_llm + summary_for_llm`
- `highThinkingQA` rewrite 只消费 `recent_turns + summary`

---

## 一、P2-1 最终状态

## 1. generation / documents 活链路已统一到 storage service

当前 `fastQA` 的活调用面已经统一到：

- [fastQA/app/modules/storage/service.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/storage/service.py)

包括：

- [context_loading.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/generation_pipeline/context_loading.py)
- [pdf_pipeline.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/generation_pipeline/pdf_pipeline.py)
- [documents service](/home/cqy/worktrees/highThinking/fastQA/app/modules/documents/service.py)

## 2. `storage_service` 已从 wrapper 变成独立实现

现在的 `storage_service` 已内聚这些能力：

- DOI 规范化
- 论文文件名生成
- 本地候选路径查找
- MinIO 对象存在性检查
- MinIO 下载到临时文件后 promote 到目标路径
- 基于目标本地路径的下载锁

它已经不再直接 import：

- `build_paper_filename`
- `normalize_doi`
- `find_local_paper_pdf`
- `ensure_local_paper_pdf`

来自 legacy `paper_storage`

## 3. `paper_storage.py` 仍存在，但不再是活链路根点

当前仓库里 legacy 文件仍在：

- [paper_storage.py](/home/cqy/worktrees/highThinking/fastQA/app/modules/storage/paper_storage.py)

但它已经不再承载 `fastQA` 活链路的正式出口。

当前更准确的定位是：

- 兼容/遗留实现保留文件
- 不是当前运行时必须经过的根点

## 4. `gateway` / `public-service` / `highThinkingQA` 侧结论

- `gateway`：当前没有 direct helper 依赖，仍是 proxy / route resolver
- `public-service`：当前活链路本来就不依赖 legacy `paper_storage`
- `highThinkingQA`：仍留有 retired documents 面上的 helper 代码，但不是 ask 主链

因此 `P2-1` 以当前范围已经完成。

---

## 二、P2-2 最终状态

## 1. fastQA prompt-facing context 已收敛

`fastQA` 现在真正进入 prompt 的仍然只有：

- `recent_turns_for_llm`
- `summary_for_llm`

并且：

- `summary_for_llm` 白名单只保留 `short_summary/open_threads/memory_facts`
- `conversation_state` 只保留 `last_turn_route/last_focus_file_ids`
- `source_selection` 只保留最小文件描述与 source scope，不再夹带路径和对象存储细节

`used_files / execution_files` 当前只保留：

- `file_id`
- `file_type`
- `file_name`
- `selected_reason`
- `source`

不再继续透传：

- `local_path`
- `storage_ref`
- `file_meta`
- `file_status`
- `parse_status`
- `index_status`
- `processing_stage`

## 2. fastQA router 不再继续透传原始 snapshot

当前 `fastQA` 在 authority context merge 阶段已不再把原始：

- `authority_context_snapshot`

塞进 `options`

保留的是：

- `authority_summary`
- `authority_conversation_state`
- `authority_snapshot_version`
- `authority_pending_overlay`

这意味着后续更不容易有人误把整包 snapshot 又接回 prompt。

## 3. highThinkingQA summary 清洗已改成白名单

`highThinkingQA` 之前对 summary 的处理是黑名单模式。

本轮之后，`highThinkingQA` 改为与 `fastQA` 对齐：

- 只保留 `short_summary/open_threads/memory_facts`
- 并继续把 `short_summary` 映射成 `recent_focus`

这解决的是“未来 contract 漂移”风险，而不是当前已发生的 prompt 泄漏。

## 4. authority snapshot 本身当前仍是干净视图

结合这轮审计，`public-service` 当前返回给 QA 服务的 authority snapshot 本身就是过滤后的视图：

- `recent_turns`
- `summary`
- `conversation_state`

不会把 `steps/timings/used_files/file_selection` 直接塞进 snapshot 顶层。

所以这轮 `P2-2` 的本质是：

- 继续压缩消费侧边界
- 防止未来字段扩张后被误入 prompt

---

## 三、验证结果

### P2-1 相关

已跑：

```bash
conda run -n agent pytest \
  fastQA/tests/test_documents_storage.py \
  fastQA/tests/test_documents.py \
  fastQA/tests/test_context_loading.py \
  fastQA/tests/test_generation_pdf_pipeline.py \
  fastQA/tests/test_reference_link_boundary.py -q
```

结果：

- `32 passed`

### P2-2 相关

已跑：

```bash
conda run -n agent pytest \
  fastQA/tests/test_conversation_context_builder.py \
  fastQA/tests/test_qa_kb_context_usage.py \
  fastQA/tests/test_qa_cache_stage1.py \
  fastQA/tests/test_qa_placeholder.py \
  highThinkingQA/tests/test_conversation_context_service.py \
  highThinkingQA/tests/test_prompt_boundary.py -q
```

结果：

- `42 passed`

---

## 四、P2 收尾后的状态判断

### 已完成

1. `P2-1 storage legacy helper / shim / 调用点继续收口`
2. `P2-2 同会话 mixed fastQA / highThinkingQA 的上下文分层固化`

### 仍可继续做，但已不属于 P2

1. 清理 `fastQA` / `highThinkingQA` 仓库中剩余 retired storage/documents 兼容代码
2. 继续推进 `public-service` 的长期 summary / memory 能力
3. 统一更多跨服务 contract 文档与字段命名
