# 2026-03-26 引用证据 Contract 审计

## 本轮已落地

### fastQA
- `done` payload 现在会根据 `reference_objects` 组装 `doi_locations`
- `doi_locations` 项现在除了 `page/section/chunk_index`，还会带：
  - `source_text`
  - `source_preview`
  - `confidence`
- 这样前端 `PdfReader` 不仅能跳页，还能直接展示证据片段

### highThinkingQA
- `execute_ask` 与 `stream_ask_events` 现在都会输出 richer `reference_objects`
- richer `reference_objects` 最小字段：
  - `doi`
  - `title`
  - `section_name`
  - `chunk_index`
  - `evidence_text`
  - `page`
  - `page_range`
  - `locator_confidence`
- `doi_locations` 会在存在页码时附带跳页信息；没有页码时保持空字典

### frontend-vue
- `PdfReader` 已能优雅消费 evidence preview
- 当没有 `similarity` 时，不再显示 `NaN%`
- 当只有 `source_text/evidence_text` 时，也能直接展示证据片段

## 本轮明确没做

- 没做句子级精确高亮
- 没做 `highThinkingQA` 索引 metadata 重建
- 没补 `page_range` 在 `highThinkingQA` ingestion -> retrieval 全链路的真正闭环
- 没改现有 viewer 为页内文本定位器

## 当前已知缺口

### highThinkingQA 页码能力仍弱
`highThinkingQA` 当前虽然 contract 已留出 `page/page_range`，但检索对象本身还没有稳定把这两个字段带出来。

结论：
- `highThinkingQA` 当前稳定能力仍是 evidence preview
- 跳页能力要依赖后续 metadata 增强

### 引用面板仍主要展示 DOI 级预览
本轮用户可见增强主要落在：
- 回答区 DOI 点击 -> `PdfReader` 侧栏证据片段

独立引用面板还没有消费 richer `reference_objects`。

## 推荐下一步
1. 把 `fastQA` 的 `doi_locations` 扩展到更多路由分支的统一回传
2. 评估 `highThinkingQA` 是否补 `page_range` 入索引并暴露到 `RetrievedChunk`
3. 再决定是否让独立引用面板也直接消费 `reference_objects`
